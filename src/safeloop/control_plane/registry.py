"""SQLite-backed registry for the SafeLoop 0.1.0 control plane MVP."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Literal

RegistryRole = Literal["admin", "operator", "viewer"]
ApprovalStatus = Literal["pending", "approved", "rejected", "expired"]


@dataclass(frozen=True)
class RegistryUser:
    user_id: str
    role: RegistryRole
    display_name: str | None = None


@dataclass(frozen=True)
class ApprovalRecord:
    approval_id: str
    requested_by: str
    action: str
    subject: str
    status: ApprovalStatus
    signed_payload: str
    signature: str
    created_at: str


@dataclass(frozen=True)
class ApprovalEvent:
    event_id: str
    approval_id: str
    event_type: str
    actor: str
    payload: str
    created_at: str


@dataclass(frozen=True)
class OperatorTokenRecord:
    token_hash: str
    salt: str
    user_id: str
    role: RegistryRole


class ControlPlaneRegistry:
    """Small SQLite registry isolated from runtime journal storage.

    The registry stores only the control-plane slice needed by local demos/tests:
    operators/users, hashed bearer-token metadata, approval requests, and an
    append-only approval event/audit log. It intentionally does not implement a
    workflow lifecycle beyond create/get/list and event append/read helpers.
    """

    _SCHEMA_VERSION = 1

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def initialize(self) -> None:
        init_control_plane_registry(self.path)
        self._assert_supported_schema()

    def schema_version(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM control_plane_metadata WHERE key = ?", ("schema_version",)
            ).fetchone()
        return int(row[0]) if row else 0

    def table_names(self) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        return {row[0] for row in rows}

    def upsert_user(self, user: RegistryUser) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO users(user_id, role, display_name) VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    role = excluded.role,
                    display_name = excluded.display_name
                """,
                (user.user_id, user.role, user.display_name),
            )

    def list_users(self) -> list[RegistryUser]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT user_id, role, display_name FROM users ORDER BY user_id ASC"
            ).fetchall()
        return [RegistryUser(user_id=row[0], role=row[1], display_name=row[2]) for row in rows]

    def upsert_operator_token(self, token: OperatorTokenRecord) -> None:
        """Persist hashed token metadata compatible with LocalTokenStore."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO local_tokens(token_hash, salt, user_id, role) VALUES (?, ?, ?, ?)
                ON CONFLICT(token_hash) DO UPDATE SET
                    salt = excluded.salt,
                    user_id = excluded.user_id,
                    role = excluded.role
                """,
                (token.token_hash, token.salt, token.user_id, token.role),
            )

    def list_operator_tokens(self) -> list[OperatorTokenRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT token_hash, salt, user_id, role FROM local_tokens ORDER BY user_id ASC, token_hash ASC"
            ).fetchall()
        return [OperatorTokenRecord(row[0], row[1], row[2], row[3]) for row in rows]

    def create_approval(self, approval: ApprovalRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO approvals(
                    approval_id, requested_by, action, subject, status,
                    signed_payload, signature, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval.approval_id,
                    approval.requested_by,
                    approval.action,
                    approval.subject,
                    approval.status,
                    approval.signed_payload,
                    approval.signature,
                    approval.created_at,
                ),
            )

    def record_approval(self, approval: ApprovalRecord) -> None:
        """Backward-compatible alias for the local demo API."""
        self.create_approval(approval)

    def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT approval_id, requested_by, action, subject, status,
                       signed_payload, signature, created_at
                FROM approvals WHERE approval_id = ?
                """,
                (approval_id,),
            ).fetchone()
        return self._approval_from_row(row) if row else None

    def list_approvals(self, *, status: ApprovalStatus | None = None) -> list[ApprovalRecord]:
        sql = """
            SELECT approval_id, requested_by, action, subject, status,
                   signed_payload, signature, created_at
            FROM approvals
        """
        params: tuple[str, ...] = ()
        if status is not None:
            sql += " WHERE status = ?"
            params = (status,)
        sql += " ORDER BY created_at ASC, approval_id ASC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._approval_from_row(row) for row in rows]

    def append_approval_event(self, event: ApprovalEvent) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO approval_events(
                    event_id, approval_id, event_type, actor, payload, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.approval_id,
                    event.event_type,
                    event.actor,
                    event.payload,
                    event.created_at,
                ),
            )

    def list_approval_events(self, approval_id: str | None = None) -> list[ApprovalEvent]:
        sql = """
            SELECT event_id, approval_id, event_type, actor, payload, created_at
            FROM approval_events
        """
        params: tuple[str, ...] = ()
        if approval_id is not None:
            sql += " WHERE approval_id = ?"
            params = (approval_id,)
        sql += " ORDER BY created_at ASC, event_id ASC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [ApprovalEvent(row[0], row[1], row[2], row[3], row[4], row[5]) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _assert_supported_schema(self) -> None:
        version = self.schema_version()
        if version > self._SCHEMA_VERSION:
            raise RuntimeError(
                f"unsupported future schema_version={version}; "
                f"registry supports schema_version={self._SCHEMA_VERSION}"
            )
        if version < self._SCHEMA_VERSION:
            raise RuntimeError(
                f"unsupported old schema_version={version}; explicit migration is required"
            )

    @staticmethod
    def _approval_from_row(row: sqlite3.Row | tuple[str, ...]) -> ApprovalRecord:
        return ApprovalRecord(
            approval_id=row[0],
            requested_by=row[1],
            action=row[2],
            subject=row[3],
            status=row[4],
            signed_payload=row[5],
            signature=row[6],
            created_at=row[7],
        )


def init_control_plane_registry(path: str | Path) -> None:
    """Create the SQLite control-plane schema if it does not already exist."""
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS control_plane_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                role TEXT NOT NULL CHECK (role IN ('admin', 'operator', 'viewer')),
                display_name TEXT
            );

            CREATE TABLE IF NOT EXISTS local_tokens (
                token_hash TEXT PRIMARY KEY,
                salt TEXT NOT NULL,
                user_id TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('admin', 'operator', 'viewer'))
            );

            CREATE TABLE IF NOT EXISTS approvals (
                approval_id TEXT PRIMARY KEY,
                requested_by TEXT NOT NULL,
                action TEXT NOT NULL,
                subject TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected', 'expired')),
                signed_payload TEXT NOT NULL,
                signature TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS approval_events (
                event_id TEXT PRIMARY KEY,
                approval_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                actor TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (approval_id) REFERENCES approvals(approval_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS external_anchors (
                anchor_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO control_plane_metadata(key, value) VALUES (?, ?)",
            ("schema_version", str(ControlPlaneRegistry._SCHEMA_VERSION)),
        )
