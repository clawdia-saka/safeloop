"""SafeLoop 0.1.4 policy profile loading and fail-closed enforcement."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from safeloop.control_plane.auth import ROLE_PERMISSIONS, Principal
from safeloop.control_plane.lifecycle import _parse_time
from safeloop.control_plane.registry import ApprovalRecord
from safeloop.control_plane.signing import verify_approval_record

SUPPORTED_VERSION = "0.1.4"
_VALID_ROLES = ("viewer", "operator", "admin")
_ROLE_RANK = {role: index for index, role in enumerate(_VALID_ROLES)}
_VALID_STATUSES = {"REQUESTED", "APPROVED", "IN_FLIGHT", "REJECTED", "EXECUTED", "EXPIRED", "REVOKED"}


class PolicyConfigError(ValueError):
    """Raised for malformed or downgraded policy profile configuration."""


class PolicyDenied(PermissionError):
    """Raised when policy enforcement denies an action."""


@dataclass(frozen=True)
class ActionPolicy:
    require_role: str | None = None
    permission: str | None = None
    approval_status: str | None = None
    max_age_seconds: int | None = None
    require_anchor_verified: bool = False
    allowed_rollback_tiers: tuple[str, ...] = ()


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    policy: str
    action: str


@dataclass(frozen=True)
class PolicyProfileSet:
    version: str
    profiles: dict[str, dict[str, ActionPolicy]]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PolicyProfileSet":
        if not isinstance(data, dict):
            raise PolicyConfigError("policy config must be an object")
        version = data.get("version")
        if version != SUPPORTED_VERSION:
            raise PolicyConfigError(f"unsupported policy version {version!r}; expected {SUPPORTED_VERSION}")
        raw_profiles = data.get("profiles")
        if not isinstance(raw_profiles, dict) or not raw_profiles:
            raise PolicyConfigError("profiles must be a non-empty object")
        profiles: dict[str, dict[str, ActionPolicy]] = {}
        for profile_name, profile in raw_profiles.items():
            if not isinstance(profile_name, str) or not profile_name:
                raise PolicyConfigError("profile names must be non-empty strings")
            if not isinstance(profile, dict):
                raise PolicyConfigError(f"profile {profile_name} must be an object")
            raw_actions = profile.get("actions")
            if not isinstance(raw_actions, dict) or not raw_actions:
                raise PolicyConfigError(f"profile {profile_name} actions must be a non-empty object")
            actions: dict[str, ActionPolicy] = {}
            for action_name, action_data in raw_actions.items():
                if not isinstance(action_name, str) or not action_name:
                    raise PolicyConfigError("action names must be non-empty strings")
                actions[action_name] = _parse_action_policy(action_name, action_data)
            profiles[profile_name] = actions
        return cls(version=version, profiles=profiles)


def load_policy_profiles(path: str | Path) -> PolicyProfileSet:
    config_path = Path(path)
    text = config_path.read_text(encoding="utf-8")
    if config_path.suffix.lower() in {".yaml", ".yml"}:
        data = _load_yaml(text)
    else:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PolicyConfigError(f"malformed JSON policy config: {exc}") from exc
    return PolicyProfileSet.from_dict(data)


def enforce_policy(
    profiles: PolicyProfileSet,
    *,
    policy: str,
    action: str,
    principal: Principal,
    approval: ApprovalRecord | None = None,
    requested_by: str | None = None,
    subject: str | None = None,
    now: datetime | None = None,
    anchor_verified: bool = False,
    rollback_tier: str | None = None,
    signing_key: bytes | None = None,
) -> PolicyDecision:
    try:
        action_policy = profiles.profiles[policy][action]
    except KeyError as exc:
        if policy not in profiles.profiles:
            raise PolicyDenied(f"unknown policy: {policy}") from exc
        raise PolicyDenied(f"unknown action: {action}") from exc

    if action_policy.require_role is not None and _ROLE_RANK[principal.role] < _ROLE_RANK[action_policy.require_role]:
        raise PolicyDenied(f"principal role {principal.role} is below required role {action_policy.require_role}")
    if action_policy.permission is not None and action_policy.permission not in ROLE_PERMISSIONS[principal.role]:
        raise PolicyDenied(f"{principal.role} lacks permission {action_policy.permission}")
    if action_policy.require_anchor_verified and not anchor_verified:
        raise PolicyDenied("anchor verification required")
    if action_policy.allowed_rollback_tiers:
        if rollback_tier not in action_policy.allowed_rollback_tiers:
            raise PolicyDenied(f"rollback tier {rollback_tier!r} is not allowed")

    if action_policy.approval_status is not None:
        if approval is None:
            raise PolicyDenied("approval required")
        if approval.status != action_policy.approval_status:
            raise PolicyDenied(f"approval status {approval.status} does not match required {action_policy.approval_status}")
        if signing_key is None:
            raise PolicyDenied("approval signing_key required")
        if not verify_approval_record(approval, signing_key):
            raise PolicyDenied("approval signature invalid")
        if approval.action != action:
            raise PolicyDenied("approval action mismatch")
        if requested_by is not None and approval.requested_by != requested_by:
            raise PolicyDenied("approval requester mismatch")
        if subject is not None and approval.subject != subject:
            raise PolicyDenied("approval subject mismatch")
        if action_policy.max_age_seconds is not None:
            if now is None:
                raise PolicyDenied("current time required for approval age check")
            try:
                age = _coerce_utc(now) - _parse_time(approval.created_at)
            except (TypeError, ValueError) as exc:
                raise PolicyDenied("approval created_at is invalid") from exc
            if age.total_seconds() > action_policy.max_age_seconds:
                raise PolicyDenied("approval exceeds max_age_seconds")
            if age.total_seconds() < 0:
                raise PolicyDenied("approval created_at is in the future")

    return PolicyDecision(allowed=True, policy=policy, action=action)


def _parse_action_policy(action_name: str, data: Any) -> ActionPolicy:
    if not isinstance(data, dict):
        raise PolicyConfigError(f"action {action_name} must be an object")
    allowed_keys = {
        "require_role",
        "permission",
        "approval_status",
        "max_age_seconds",
        "require_anchor_verified",
        "allowed_rollback_tiers",
    }
    extra = set(data) - allowed_keys
    if extra:
        raise PolicyConfigError(f"action {action_name} contains unknown keys: {sorted(extra)}")
    role = data.get("require_role")
    if role is not None and role not in _VALID_ROLES:
        raise PolicyConfigError(f"action {action_name} has invalid require_role")
    permission = data.get("permission")
    all_permissions = set().union(*ROLE_PERMISSIONS.values())
    if permission is not None and permission not in all_permissions:
        raise PolicyConfigError(f"action {action_name} has invalid permission")
    status = data.get("approval_status")
    if status is not None and status not in _VALID_STATUSES:
        raise PolicyConfigError(f"action {action_name} has invalid approval_status")
    max_age = data.get("max_age_seconds")
    if max_age is not None and (not isinstance(max_age, int) or isinstance(max_age, bool) or max_age <= 0):
        raise PolicyConfigError(f"action {action_name} max_age_seconds must be a positive integer")
    require_anchor = data.get("require_anchor_verified", False)
    if not isinstance(require_anchor, bool):
        raise PolicyConfigError(f"action {action_name} require_anchor_verified must be boolean")
    tiers = data.get("allowed_rollback_tiers", [])
    if not isinstance(tiers, list) or not all(isinstance(tier, str) and tier for tier in tiers):
        raise PolicyConfigError(f"action {action_name} allowed_rollback_tiers must be a list of strings")
    if len(set(tiers)) != len(tiers):
        raise PolicyConfigError(f"action {action_name} allowed_rollback_tiers must not contain duplicates")
    return ActionPolicy(role, permission, status, max_age, require_anchor, tuple(tiers))


def _load_yaml(text: str) -> Any:
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError:
        return _load_simple_yaml(text)
    try:
        return yaml.safe_load(text)
    except Exception as exc:  # pragma: no cover - depends on optional PyYAML
        raise PolicyConfigError(f"malformed YAML policy config: {exc}") from exc


def _load_simple_yaml(text: str) -> dict[str, Any]:
    """Parse the small YAML subset used for SafeLoop profile files.

    Supports nested mappings via two-space indentation, scalar strings/bools/ints,
    empty inline lists (``[]``), and block lists of scalar strings.
    """
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    pending_list: tuple[int, dict[str, Any], str] | None = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if stripped.startswith("- "):
            if pending_list is None or indent <= pending_list[0]:
                raise PolicyConfigError("malformed YAML list")
            container = pending_list[1].get(pending_list[2])
            if container == {}:
                container = []
                pending_list[1][pending_list[2]] = container
            if not isinstance(container, list):
                raise PolicyConfigError("malformed YAML list")
            container.append(_yaml_scalar(stripped[2:].strip()))
            continue
        pending_list = None
        if ":" not in stripped:
            raise PolicyConfigError("malformed YAML mapping")
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        if not stack:
            raise PolicyConfigError("malformed YAML indentation")
        parent = stack[-1][1]
        if value == "":
            child: dict[str, Any] = {}
            parent[key] = child
            pending_list = (indent, parent, key)
            stack.append((indent, child))
        else:
            parent[key] = _yaml_scalar(value)
    return root


def _yaml_scalar(value: str) -> Any:
    if value == "[]":
        return []
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    try:
        return int(value)
    except ValueError:
        return value.strip('"\'')


def _coerce_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
