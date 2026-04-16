from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import RLock
from typing import Callable, NotRequired, TypedDict
from uuid import uuid4

from app.core.time_utils import utc_now


class PendingConfirmationPayload(TypedDict):
    confirmation_id: str
    user_id: str
    action: str
    arguments: dict[str, object]
    summary: str
    created_at: str
    expires_at: str


class ConfirmationRequestResult(TypedDict):
    requires_confirmation: bool
    message: str
    pending_action: PendingConfirmationPayload


class ConfirmationResolutionResult(TypedDict):
    pending_action: PendingConfirmationPayload
    confirmed: NotRequired[bool]
    cancelled: NotRequired[bool]
    result: NotRequired[dict[str, object]]


@dataclass
class PendingConfirmation:
    confirmation_id: str
    user_id: str
    action: str
    arguments: dict[str, object]
    summary: str
    created_at: datetime
    expires_at: datetime

    def model_dump(self) -> PendingConfirmationPayload:
        return {
            "confirmation_id": self.confirmation_id,
            "user_id": self.user_id,
            "action": self.action,
            "arguments": self.arguments,
            "summary": self.summary,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }


class ConfirmationManager:
    def __init__(self, ttl_minutes: int = 10) -> None:
        self.ttl_minutes = ttl_minutes
        self._pending: dict[str, PendingConfirmation] = {}
        self._lock = RLock()

    def request_confirmation(
        self,
        *,
        user_id: str,
        action: str,
        arguments: dict[str, object],
        summary: str,
    ) -> ConfirmationRequestResult:
        self._prune_expired()
        now = utc_now()
        confirmation = PendingConfirmation(
            confirmation_id=uuid4().hex[:12],
            user_id=user_id or "unknown",
            action=action,
            arguments=dict(arguments),
            summary=summary,
            created_at=now,
            expires_at=now + timedelta(minutes=self.ttl_minutes),
        )
        with self._lock:
            self._pending[confirmation.confirmation_id] = confirmation
        return {
            "requires_confirmation": True,
            "message": (
                f"Please confirm before I proceed: {summary} "
                f"Use confirm_pending_action with confirmation_id={confirmation.confirmation_id}, "
                "or cancel_pending_action."
            ),
            "pending_action": confirmation.model_dump(),
        }

    def confirm(
        self,
        confirmation_id: str,
        executor: Callable[[PendingConfirmation], dict[str, object]],
    ) -> ConfirmationResolutionResult:
        self._prune_expired()
        with self._lock:
            confirmation = self._pending.pop(confirmation_id, None)
        if confirmation is None:
            raise LookupError(f"Pending confirmation {confirmation_id} was not found or expired.")
        result = executor(confirmation)
        return {
            "confirmed": True,
            "pending_action": confirmation.model_dump(),
            "result": result,
        }

    def cancel(self, confirmation_id: str) -> ConfirmationResolutionResult:
        self._prune_expired()
        with self._lock:
            confirmation = self._pending.pop(confirmation_id, None)
        if confirmation is None:
            raise LookupError(f"Pending confirmation {confirmation_id} was not found or expired.")
        return {
            "cancelled": True,
            "pending_action": confirmation.model_dump(),
        }

    def pending_actions(self, user_id: str | None = None) -> list[PendingConfirmationPayload]:
        self._prune_expired()
        with self._lock:
            actions = list(self._pending.values())
        if user_id:
            actions = [action for action in actions if action.user_id in {user_id, "unknown"}]
        return [action.model_dump() for action in actions]

    def _prune_expired(self) -> None:
        now = utc_now()
        with self._lock:
            expired = [
                confirmation_id
                for confirmation_id, confirmation in self._pending.items()
                if confirmation.expires_at <= now
            ]
            for confirmation_id in expired:
                self._pending.pop(confirmation_id, None)
