from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from app.core.context_store import ContextStore
from app.core.settings import Settings, get_settings
from app.core.time_utils import ensure_utc, utc_now
from app.models.domain import ConversationTurn, SharedContext, UserConversationMemory


class ConversationManager:
    def __init__(
        self,
        store: ContextStore,
        settings: Settings | None = None,
    ) -> None:
        self.store = store
        self.settings = settings or get_settings()

    def ensure_active_session(self, user_id: str, force_new: bool = False) -> UserConversationMemory:
        user_key = str(user_id)
        snapshot = self.store.snapshot()
        memory = snapshot.conversation_memory.get(user_key)

        if memory is None:
            return self._create_session(user_key, reason="initial")

        if force_new:
            return self._roll_session(user_key, previous=memory, reason="manual_reset")

        timeout = timedelta(minutes=self.settings.session_timeout_minutes)
        if utc_now() - ensure_utc(memory.last_activity_at) >= timeout:
            return self._roll_session(user_key, previous=memory, reason="timeout")

        return memory

    def register_user_turn(
        self,
        user_id: str,
        text: str,
        *,
        force_new: bool = False,
    ) -> UserConversationMemory:
        self.ensure_active_session(user_id, force_new=force_new)
        now = utc_now()

        def mutator(state: SharedContext) -> dict[str, object]:
            memory = state.conversation_memory[str(user_id)]
            memory.turns.append(
                ConversationTurn(
                    role="user",
                    text=text,
                    timestamp=now,
                )
            )
            memory.last_activity_at = now
            memory.turns = memory.turns[-20:]
            return {
                "user_id": str(user_id),
                "session_id": memory.active_session_id,
                "turn_role": "user",
            }

        updated = self.store.update(
            agent="conversation_manager",
            action="register_user_turn",
            summary=f"Recorded a user turn for {user_id}.",
            mutator=mutator,
        )
        return updated.conversation_memory[str(user_id)]

    def register_assistant_turn(self, user_id: str, text: str) -> UserConversationMemory:
        self.ensure_active_session(user_id)
        now = utc_now()

        def mutator(state: SharedContext) -> dict[str, object]:
            memory = state.conversation_memory[str(user_id)]
            memory.turns.append(
                ConversationTurn(
                    role="assistant",
                    text=text,
                    timestamp=now,
                )
            )
            memory.last_activity_at = now
            memory.turns = memory.turns[-20:]
            return {
                "user_id": str(user_id),
                "session_id": memory.active_session_id,
                "turn_role": "assistant",
            }

        updated = self.store.update(
            agent="conversation_manager",
            action="register_assistant_turn",
            summary=f"Recorded an assistant turn for {user_id}.",
            mutator=mutator,
        )
        return updated.conversation_memory[str(user_id)]

    def start_new_session(self, user_id: str, reason: str = "manual_reset") -> UserConversationMemory:
        return self.ensure_active_session(user_id, force_new=True)

    def build_prompt_context(self, user_id: str, max_turns: int = 8) -> str:
        memory = self.ensure_active_session(user_id)
        recent_turns = memory.turns[-max_turns:]
        lines = [
            f"Active session id: {memory.active_session_id}",
            f"Session started: {memory.session_started_at.isoformat()}",
        ]

        if memory.carryover_context.strip():
            lines.append("Carryover context:")
            lines.append(memory.carryover_context.strip())

        if memory.current_status.strip():
            lines.append(f"Current user status: {memory.current_status.strip()}")

        if recent_turns:
            lines.append("Recent conversation:")
            for turn in recent_turns:
                role = "User" if turn.role == "user" else "Assistant"
                lines.append(f"- {role}: {turn.text}")

        return "\n".join(lines)

    def session_status(self, user_id: str) -> dict[str, object]:
        memory = self.ensure_active_session(user_id)
        return {
            "user_id": user_id,
            "session_id": memory.active_session_id,
            "started_at": memory.session_started_at.isoformat(),
            "last_activity_at": memory.last_activity_at.isoformat(),
            "current_status": memory.current_status,
            "turn_count": len(memory.turns),
            "completed_sessions": len(memory.completed_session_summaries),
        }

    def update_current_status(self, user_id: str, status: str) -> UserConversationMemory:
        self.ensure_active_session(user_id)
        now = utc_now()

        def mutator(state: SharedContext) -> dict[str, object]:
            memory = state.conversation_memory[str(user_id)]
            memory.current_status = status.strip()
            memory.last_activity_at = now
            return {
                "user_id": str(user_id),
                "session_id": memory.active_session_id,
                "current_status": memory.current_status,
            }

        updated = self.store.update(
            agent="conversation_manager",
            action="update_current_status",
            summary=f"Updated current status for {user_id}.",
            mutator=mutator,
        )
        return updated.conversation_memory[str(user_id)]

    def _create_session(self, user_id: str, reason: str) -> UserConversationMemory:
        now = utc_now()
        memory = UserConversationMemory(
            user_id=user_id,
            active_session_id=self._new_session_id(),
            session_started_at=now,
            last_activity_at=now,
        )

        def mutator(state: SharedContext) -> dict[str, object]:
            state.conversation_memory[user_id] = memory
            return {
                "user_id": user_id,
                "session_id": memory.active_session_id,
                "reason": reason,
            }

        updated = self.store.update(
            agent="conversation_manager",
            action="create_session",
            summary=f"Created a new conversation session for {user_id}.",
            mutator=mutator,
        )
        return updated.conversation_memory[user_id]

    def _roll_session(
        self,
        user_id: str,
        *,
        previous: UserConversationMemory,
        reason: str,
    ) -> UserConversationMemory:
        now = utc_now()
        summary = self._summarise_session(previous)
        carryover_parts = [part.strip() for part in [previous.carryover_context, summary] if part.strip()]
        carryover_context = "\n".join(carryover_parts[-2:])

        def mutator(state: SharedContext) -> dict[str, object]:
            state.conversation_memory[user_id] = UserConversationMemory(
                user_id=user_id,
                active_session_id=self._new_session_id(),
                session_started_at=now,
                last_activity_at=now,
                carryover_context=carryover_context,
                current_status=previous.current_status,
                turns=[],
                completed_session_summaries=(previous.completed_session_summaries + [summary])[-10:],
            )
            return {
                "user_id": user_id,
                "reason": reason,
                "previous_session_id": previous.active_session_id,
                "new_session_id": state.conversation_memory[user_id].active_session_id,
            }

        updated = self.store.update(
            agent="conversation_manager",
            action="roll_session",
            summary=f"Rolled conversation session for {user_id} due to {reason}.",
            mutator=mutator,
        )
        return updated.conversation_memory[user_id]

    @staticmethod
    def _new_session_id() -> str:
        return uuid4().hex[:12]

    @staticmethod
    def _summarise_session(memory: UserConversationMemory) -> str:
        if not memory.turns:
            return "Previous session had no meaningful conversation."

        recent_user_turns = [turn.text for turn in memory.turns if turn.role == "user"][-3:]
        recent_assistant_turns = [turn.text for turn in memory.turns if turn.role == "assistant"][-2:]

        lines = [
            f"Previous session {memory.active_session_id} ran from "
            f"{memory.session_started_at.isoformat()} to {memory.last_activity_at.isoformat()}.",
        ]
        if recent_user_turns:
            lines.append("Recent user intents: " + " | ".join(recent_user_turns))
        if recent_assistant_turns:
            lines.append("Recent fridge responses: " + " | ".join(recent_assistant_turns))
        return "\n".join(lines)
