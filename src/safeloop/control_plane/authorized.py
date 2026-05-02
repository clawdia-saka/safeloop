"""Permission-enforced control-plane operation helpers for the local MVP."""

from __future__ import annotations

from safeloop.control_plane.auth import Principal, require_permission
from safeloop.control_plane.registry import ApprovalRecord, ControlPlaneRegistry, RegistryUser


def upsert_user_as(
    registry: ControlPlaneRegistry, principal: Principal, user: RegistryUser
) -> None:
    """Create or update a user after enforcing the manage_users permission."""
    require_permission(principal, "manage_users")
    registry.upsert_user(user)


def record_approval_as(
    registry: ControlPlaneRegistry, principal: Principal, approval: ApprovalRecord
) -> None:
    """Record an approval request/decision after enforcing approve permission."""
    require_permission(principal, "approve")
    registry.record_approval(approval)


def list_approvals_as(
    registry: ControlPlaneRegistry, principal: Principal
) -> list[ApprovalRecord]:
    """List approvals after enforcing view permission."""
    require_permission(principal, "view")
    return registry.list_approvals()
