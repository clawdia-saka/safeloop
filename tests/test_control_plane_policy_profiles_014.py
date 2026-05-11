from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from safeloop.control_plane.auth import Principal
from safeloop.control_plane.lifecycle import ApprovalLifecycleStore
from safeloop.control_plane.policy_profiles import (
    PolicyConfigError,
    PolicyDenied,
    PolicyProfileSet,
    enforce_policy,
    load_policy_profiles,
)
from safeloop.control_plane.registry import ApprovalRecord

KEY = b"policy-profile-test-key"
T0 = datetime(2026, 5, 3, 5, 0, tzinfo=timezone.utc)


def test_load_json_policy_profile_and_enforce_all_guards(tmp_path) -> None:
    path = tmp_path / "policies.json"
    path.write_text(
        """
        {
          "version": "0.1.4",
          "profiles": {
            "guarded_auto_undo": {
              "actions": {
                "rollback": {
                  "require_role": "operator",
                  "permission": "resume",
                  "approval_status": "APPROVED",
                  "max_age_seconds": 60,
                  "require_anchor_verified": true,
                  "allowed_rollback_tiers": ["journal", "compensating"]
                }
              }
            }
          }
        }
        """,
        encoding="utf-8",
    )
    profiles = load_policy_profiles(path)
    store = ApprovalLifecycleStore(KEY, ttl=timedelta(minutes=10))
    approved = store.approve(
        store.request(
            approval_id="a1",
            requested_by="ops",
            action="rollback",
            subject="runs/r1",
            created_at=T0,
        ).approval_id,
        now=T0 + timedelta(seconds=1),
    )

    decision = enforce_policy(
        profiles,
        policy="guarded_auto_undo",
        action="rollback",
        principal=Principal(user_id="ops", role="operator"),
        approval=approved,
        requested_by="ops",
        subject="runs/r1",
        now=T0 + timedelta(seconds=30),
        anchor_verified=True,
        rollback_tier="journal",
        signing_key=KEY,
    )

    assert decision.allowed is True
    assert decision.policy == "guarded_auto_undo"
    assert decision.action == "rollback"


@pytest.mark.parametrize("policy,action", [("missing", "rollback"), ("guarded_auto_undo", "missing")])
def test_unknown_policy_or_action_fails_closed(policy: str, action: str) -> None:
    profiles = PolicyProfileSet.from_dict(
        {
            "version": "0.1.4",
            "profiles": {"guarded_auto_undo": {"actions": {"rollback": {"permission": "resume"}}}},
        }
    )

    with pytest.raises(PolicyDenied, match="unknown policy|unknown action"):
        enforce_policy(
            profiles,
            policy=policy,
            action=action,
            principal=Principal(user_id="ops", role="admin"),
        )


def test_policy_downgrade_and_malformed_configs_fail(tmp_path) -> None:
    downgraded = tmp_path / "downgraded.json"
    downgraded.write_text('{"version":"0.1.3","profiles":{}}', encoding="utf-8")
    with pytest.raises(PolicyConfigError, match="version"):
        load_policy_profiles(downgraded)

    malformed = tmp_path / "malformed.json"
    malformed.write_text(
        '{"version":"0.1.4","profiles":{"p":{"actions":{"a":{"approval_status":"APPROVED","max_age_seconds":0}}}}}',
        encoding="utf-8",
    )
    with pytest.raises(PolicyConfigError, match="max_age_seconds"):
        load_policy_profiles(malformed)


def test_e2e_guarded_auto_undo_and_external_dispatch_policies(tmp_path) -> None:
    path = tmp_path / "policies.yaml"
    path.write_text(
        """
        version: 0.1.4
        profiles:
          guarded_auto_undo:
            actions:
              rollback:
                require_role: operator
                permission: resume
                approval_status: APPROVED
                max_age_seconds: 120
                require_anchor_verified: true
                allowed_rollback_tiers:
                  - journal
          external_dispatch:
            actions:
              dispatch_webhook:
                require_role: admin
                permission: approve
                approval_status: APPROVED
                max_age_seconds: 30
                require_anchor_verified: false
                allowed_rollback_tiers: []
        """,
        encoding="utf-8",
    )
    profiles = load_policy_profiles(path)
    store = ApprovalLifecycleStore(KEY)
    rollback = store.approve(store.request(approval_id="rb", requested_by="ops", action="rollback", subject="runs/rb", created_at=T0).approval_id, now=T0 + timedelta(seconds=1))
    dispatch = store.approve(store.request(approval_id="wh", requested_by="admin", action="dispatch_webhook", subject="webhooks/w1", created_at=T0).approval_id, now=T0 + timedelta(seconds=1))

    assert enforce_policy(profiles, policy="guarded_auto_undo", action="rollback", principal=Principal("ops", "operator"), approval=rollback, requested_by="ops", subject="runs/rb", now=T0 + timedelta(seconds=2), anchor_verified=True, rollback_tier="journal", signing_key=KEY).allowed
    assert enforce_policy(profiles, policy="external_dispatch", action="dispatch_webhook", principal=Principal("admin", "admin"), approval=dispatch, requested_by="admin", subject="webhooks/w1", now=T0 + timedelta(seconds=2), anchor_verified=False, rollback_tier=None, signing_key=KEY).allowed

    with pytest.raises(PolicyDenied, match="role"):
        enforce_policy(profiles, policy="external_dispatch", action="dispatch_webhook", principal=Principal("ops", "operator"), approval=dispatch, requested_by="admin", subject="webhooks/w1", now=T0 + timedelta(seconds=2), signing_key=KEY)


def test_invalid_principal_role_fails_closed_with_policy_denied() -> None:
    profiles = PolicyProfileSet.from_dict(
        {
            "version": "0.1.4",
            "profiles": {"p": {"actions": {"a": {"require_role": "viewer", "permission": "view"}}}},
        }
    )

    with pytest.raises(PolicyDenied, match="invalid principal role"):
        enforce_policy(
            profiles,
            policy="p",
            action="a",
            principal=Principal(user_id="mallory", role="superuser"),  # type: ignore[arg-type]
        )


def test_policy_config_rejects_max_age_without_approval_status() -> None:
    with pytest.raises(PolicyConfigError, match="max_age_seconds requires approval_status"):
        PolicyProfileSet.from_dict(
            {
                "version": "0.1.4",
                "profiles": {"p": {"actions": {"a": {"max_age_seconds": 60}}}},
            }
        )


def test_approval_policy_requires_signing_key_and_rejects_forged_approved_record() -> None:
    profiles = PolicyProfileSet.from_dict(
        {
            "version": "0.1.4",
            "profiles": {
                "guarded_auto_undo": {
                    "actions": {
                        "rollback": {
                            "require_role": "operator",
                            "permission": "resume",
                            "approval_status": "APPROVED",
                        }
                    }
                }
            },
        }
    )
    forged = ApprovalRecord(
        approval_id="forged",
        requested_by="ops",
        action="rollback",
        subject="runs/r1",
        status="APPROVED",
        signed_payload="",
        signature="bad",
        created_at=T0.isoformat(),
    )

    with pytest.raises(PolicyDenied, match="signing_key required"):
        enforce_policy(
            profiles,
            policy="guarded_auto_undo",
            action="rollback",
            principal=Principal("ops", "operator"),
            approval=forged,
        )

    with pytest.raises(PolicyDenied, match="signature invalid"):
        enforce_policy(
            profiles,
            policy="guarded_auto_undo",
            action="rollback",
            principal=Principal("ops", "operator"),
            approval=forged,
            signing_key=KEY,
        )
