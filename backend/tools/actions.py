from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.auth import AuthorizationError, ScopedRecordNotFound
from backend.database import Database
from backend.schemas import ActionResult, ActionType, CustomerIdentity, PendingAction


class ActionError(RuntimeError):
    """Raised when a local action cannot be prepared or confirmed safely."""


class CustomerActionTool:
    """State-changing escalation/follow-up tool with a mandatory confirmation gate."""

    name = "customer_action"

    def __init__(self, db: Database, ttl_minutes: int = 15):
        self.db = db
        self.ttl_minutes = ttl_minutes

    def _validate_target(self, identity: CustomerIdentity, target_id: str | None) -> None:
        if not target_id:
            return
        if target_id.upper().startswith("ORD-"):
            exists = self.db.scoped_order(identity.account_id, target_id)
        elif target_id.upper().startswith("TKT-"):
            exists = self.db.scoped_ticket(identity.account_id, target_id)
        else:
            raise ActionError("Unsupported action target")
        if exists is None:
            raise ScopedRecordNotFound("Record not found in your account")

    def prepare(
        self,
        *,
        identity: CustomerIdentity,
        action_type: ActionType,
        target_id: str | None,
        summary: str,
        payload: dict[str, Any],
    ) -> PendingAction:
        self._validate_target(identity, target_id)
        now = datetime.now(UTC)
        expires = now + timedelta(minutes=self.ttl_minutes)
        action_id = f"ACT-{secrets.token_hex(6).upper()}"
        self.db.execute(
            """INSERT INTO pending_actions
            (action_id, account_id, user_id, action_type, status, target_id, summary, payload_json, created_at, expires_at)
            VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)""",
            (
                action_id,
                identity.account_id,
                identity.user_id,
                action_type.value,
                target_id,
                summary,
                json.dumps(payload, separators=(",", ":")),
                now.isoformat(),
                expires.isoformat(),
            ),
        )
        return PendingAction(
            action_id=action_id,
            action_type=action_type,
            status="pending",
            summary=summary,
            target_id=target_id,
            expires_at=expires.isoformat(),
        )

    def _scoped_pending(self, identity: CustomerIdentity, action_id: str) -> dict[str, Any]:
        row = self.db.fetch_one(
            "SELECT * FROM pending_actions WHERE action_id = ? AND account_id = ? AND user_id = ?",
            (action_id, identity.account_id, identity.user_id),
        )
        if row is None:
            raise ScopedRecordNotFound("Action not found in your account")
        return row

    def confirm(self, identity: CustomerIdentity, action_id: str) -> ActionResult:
        row = self._scoped_pending(identity, action_id)
        if row["status"] != "pending":
            raise AuthorizationError(f"Action is already {row['status']}")
        if datetime.fromisoformat(row["expires_at"]) <= datetime.now(UTC):
            self.db.execute("UPDATE pending_actions SET status = 'expired' WHERE action_id = ?", (action_id,))
            raise AuthorizationError("Action confirmation has expired")
        confirmed_at = datetime.now(UTC).isoformat()
        with self.db.transaction() as connection:
            cursor = connection.execute(
                """UPDATE pending_actions SET status = 'confirmed', confirmed_at = ?
                WHERE action_id = ? AND account_id = ? AND user_id = ? AND status = 'pending'""",
                (confirmed_at, action_id, identity.account_id, identity.user_id),
            )
            if cursor.rowcount != 1:
                raise AuthorizationError("Action could not be confirmed")
        label = "escalation" if row["action_type"] == ActionType.CREATE_ESCALATION.value else "follow-up task"
        return ActionResult(action_id=action_id, status="confirmed", message=f"The {label} was created locally.")

    def cancel(self, identity: CustomerIdentity, action_id: str) -> ActionResult:
        row = self._scoped_pending(identity, action_id)
        if row["status"] != "pending":
            raise AuthorizationError(f"Action is already {row['status']}")
        cancelled_at = datetime.now(UTC).isoformat()
        self.db.execute(
            "UPDATE pending_actions SET status = 'cancelled', cancelled_at = ? WHERE action_id = ?",
            (cancelled_at, action_id),
        )
        return ActionResult(
            action_id=action_id, status="cancelled", message="The pending action was cancelled; nothing was created."
        )
