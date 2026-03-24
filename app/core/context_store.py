from __future__ import annotations

from datetime import datetime
from threading import RLock
from typing import Callable

from app.models.domain import ContextEvent, SharedContext


class ContextStore:
    def __init__(self, initial_state: SharedContext) -> None:
        self._state = initial_state
        self._lock = RLock()

    def snapshot(self) -> SharedContext:
        with self._lock:
            return self._state.model_copy(deep=True)

    def update(
        self,
        *,
        agent: str,
        action: str,
        summary: str,
        mutator: Callable[[SharedContext], dict[str, object] | None],
    ) -> SharedContext:
        with self._lock:
            changes = mutator(self._state) or {}
            self._state.version += 1
            self._state.recent_events.insert(
                0,
                ContextEvent(
                    timestamp=datetime.utcnow(),
                    agent=agent,
                    action=action,
                    summary=summary,
                    changes=changes,
                ),
            )
            self._state.recent_events = self._state.recent_events[:50]
            return self._state.model_copy(deep=True)

