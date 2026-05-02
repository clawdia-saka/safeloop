"""Local-demo authentication and RBAC helpers for the control plane MVP.

This module intentionally implements only local token storage suitable for demos and
unit tests. It hashes bearer tokens with per-token salts before persistence, but it
is not a hosted identity provider, session system, or production secret store.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import secrets
import sqlite3
from typing import Literal

RegistryRole = Literal["admin", "operator", "viewer"]
Permission = Literal[
    "view",
    "request_approval",
    "approve",
    "reject",
    "resume",
    "manage_users",
    "rotate_secrets",
    "export_anchors",
]

ROLE_PERMISSIONS: dict[RegistryRole, frozenset[Permission]] = {
    "admin": frozenset(
        {
            "view",
            "request_approval",
            "approve",
            "reject",
            "resume",
            "manage_users",
            "rotate_secrets",
            "export_anchors",
        }
    ),
    "operator": frozenset(
        {"view", "request_approval", "approve", "reject", "resume", "export_anchors"}
    ),
    "viewer": frozenset({"view"}),
}


class AccessDenied(PermissionError):
    """Raised when an authenticated principal lacks a required permission."""


class AuthenticationError(PermissionError):
    """Raised when a token cannot be authenticated."""


@dataclass(frozen=True)
class Principal:
    user_id: str
    role: RegistryRole


class LocalTokenStore:
    """SQLite-backed local token store that never persists raw token values."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS local_tokens (
                    token_hash TEXT PRIMARY KEY,
                    salt TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('admin', 'operator', 'viewer'))
                )
                """
            )

    def issue_token(self, user_id: str, role: RegistryRole) -> str:
        token = secrets.token_urlsafe(32)
        salt = secrets.token_hex(16)
        token_hash = _hash_token(token, salt)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO local_tokens(token_hash, salt, user_id, role) VALUES (?, ?, ?, ?)",
                (token_hash, salt, user_id, role),
            )
        return token

    def authenticate(self, token: str) -> Principal:
        with self._connect() as conn:
            rows = conn.execute("SELECT token_hash, salt, user_id, role FROM local_tokens").fetchall()
        for token_hash, salt, user_id, role in rows:
            if secrets.compare_digest(token_hash, _hash_token(token, salt)):
                return Principal(user_id=user_id, role=role)
        raise AuthenticationError("invalid token")

    def debug_token_hashes(self) -> set[str]:
        """Return stored hashes for tests/diagnostics without exposing raw tokens."""
        with self._connect() as conn:
            rows = conn.execute("SELECT token_hash FROM local_tokens").fetchall()
        return {row[0] for row in rows}

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)


def require_permission(principal: Principal, permission: Permission) -> None:
    if permission not in ROLE_PERMISSIONS[principal.role]:
        raise AccessDenied(f"{principal.role} cannot {permission}")


def _hash_token(token: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{token}".encode("utf-8")).hexdigest()
