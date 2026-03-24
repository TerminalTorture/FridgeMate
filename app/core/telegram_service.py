from __future__ import annotations

import time

from app.core.conversation_manager import ConversationManager
from app.core.http_client import post_json
from app.core.integration_debug import IntegrationDebugLog
from app.core.llm_service import LLMService
from app.core.orchestrator import MCPFridgeOrchestrator
from app.core.settings import Settings, get_settings


class TelegramService:
    def __init__(
        self,
        orchestrator: MCPFridgeOrchestrator,
        llm_service: LLMService,
        conversation_manager: ConversationManager,
        settings: Settings | None = None,
    ) -> None:
        self.orchestrator = orchestrator
        self.llm_service = llm_service
        self.conversation_manager = conversation_manager
        self.settings = settings or get_settings()
        self.debug_log = IntegrationDebugLog()

    def handle_webhook(
        self,
        update: dict[str, object],
        secret_token: str | None,
    ) -> dict[str, object]:
        self._verify_secret(secret_token)
        return self.process_update(update)

    def process_update(self, update: dict[str, object]) -> dict[str, object]:
        self.debug_log.record(
            service="telegram",
            direction="inbound",
            status="received",
            summary="Telegram update received.",
            metadata={"has_message": bool(update.get("message") or update.get("edited_message"))},
        )

        message = self._extract_message(update)
        if message is None:
            return {"ok": True, "status": "ignored", "reason": "no_message_payload"}

        chat_id = message["chat_id"]
        user_id = message["user_id"]
        text = message["text"]
        reply = self.build_reply_for_user(user_id=user_id, text=text)
        telegram_result = self.send_message(chat_id=chat_id, text=reply)
        return {"ok": True, "status": "sent", "reply": reply, "telegram": telegram_result}

    def get_updates(
        self,
        offset: int | None = None,
        timeout_seconds: int | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "timeout": timeout_seconds or self.settings.telegram_poll_timeout_seconds,
            "allowed_updates": ["message"],
        }
        if offset is not None:
            payload["offset"] = offset
        return self._telegram_api_call("getUpdates", payload)

    def register_webhook(
        self,
        url: str | None = None,
        drop_pending_updates: bool = True,
    ) -> dict[str, object]:
        webhook_url = url or self.settings.telegram_webhook_url
        if not self.settings.telegram_bot_token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured.")
        if not webhook_url:
            raise ValueError("Telegram webhook URL is not configured.")

        payload: dict[str, object] = {
            "url": webhook_url,
            "drop_pending_updates": drop_pending_updates,
            "allowed_updates": ["message"],
        }
        if self.settings.telegram_webhook_secret:
            payload["secret_token"] = self.settings.telegram_webhook_secret

        return self._telegram_api_call("setWebhook", payload)

    def get_webhook_info(self) -> dict[str, object]:
        if not self.settings.telegram_bot_token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured.")
        return self._telegram_api_call("getWebhookInfo", {})

    def delete_webhook(self, drop_pending_updates: bool = False) -> dict[str, object]:
        return self._telegram_api_call(
            "deleteWebhook",
            {"drop_pending_updates": drop_pending_updates},
        )

    def send_message(self, chat_id: str | int, text: str) -> dict[str, object]:
        payload = {
            "chat_id": chat_id,
            "text": text[:4000],
        }
        retries = max(self.settings.telegram_send_retries, 1)
        last_error: RuntimeError | None = None

        for attempt in range(1, retries + 1):
            try:
                return self._telegram_api_call("sendMessage", payload)
            except RuntimeError as exc:
                last_error = exc
                self.debug_log.record(
                    service="telegram",
                    direction="internal",
                    status="retry",
                    summary="Retrying Telegram sendMessage after failure.",
                    metadata={
                        "attempt": attempt,
                        "max_attempts": retries,
                        "chat_id": str(chat_id),
                        "text_length": len(payload["text"]),
                        "error": str(exc),
                    },
                )
                if attempt < retries:
                    time.sleep(min(attempt, 3))

        assert last_error is not None
        raise last_error

    def build_reply_for_user(self, user_id: str, text: str) -> str:
        translated = self._translate_command(text)
        if translated == "__new_session__":
            session = self.conversation_manager.start_new_session(user_id, reason="telegram_new_command")
            reply = (
                "Hi How are you\n\n"
                "Started a new fridge session. I kept a compact summary of the previous conversation "
                f"and moved into session `{session.active_session_id}`."
            )
            self.conversation_manager.register_assistant_turn(user_id, reply)
            return reply

        if translated in {"__help__", "__start__"}:
            reply = (
                "MCP Fridge commands:\n"
                "/recipes\n"
                "/inventory\n"
                "/groceries [recipe name]\n"
                "/cook <recipe name>\n"
                "/utilities\n"
                "/new"
            )
            self.conversation_manager.register_user_turn(user_id, text)
            self.conversation_manager.register_assistant_turn(user_id, reply)
            return reply

        self.conversation_manager.register_user_turn(user_id, text)

        if self.llm_service.is_configured() and self._prefer_llm_tooling(translated):
            reply = self._generate_llm_reply(user_id=user_id, text=text)
            self.conversation_manager.register_assistant_turn(user_id, reply)
            return reply

        result = self.orchestrator.handle_telegram_message(user_id=user_id, message=translated)
        reply = str(result["reply"])
        if result["intent"] != "unknown":
            self.conversation_manager.register_assistant_turn(user_id, reply)
            return reply

        if self.llm_service.is_configured():
            reply = self._generate_llm_reply(user_id=user_id, text=text)
            self.conversation_manager.register_assistant_turn(user_id, reply)
            return reply

        self.conversation_manager.register_assistant_turn(user_id, reply)
        return reply

    def _verify_secret(self, secret_token: str | None) -> None:
        expected = self.settings.telegram_webhook_secret
        if expected and secret_token != expected:
            raise PermissionError("Invalid Telegram webhook secret token.")

    @staticmethod
    def _extract_message(update: dict[str, object]) -> dict[str, str] | None:
        candidate = update.get("message") or update.get("edited_message")
        if not isinstance(candidate, dict):
            return None

        text = candidate.get("text")
        chat = candidate.get("chat", {})
        user = candidate.get("from", {})
        if not isinstance(text, str):
            return None
        if not isinstance(chat, dict):
            return None

        chat_id = chat.get("id")
        if chat_id is None:
            return None

        user_id = user.get("id", chat_id) if isinstance(user, dict) else chat_id
        return {
            "chat_id": str(chat_id),
            "user_id": str(user_id),
            "text": text.strip(),
        }

    @staticmethod
    def _translate_command(text: str) -> str:
        stripped = text.strip()
        lowered = stripped.lower()

        if lowered.startswith("/start"):
            return "__start__"
        if lowered.startswith("/help"):
            return "__help__"
        if lowered.startswith("/new"):
            return "__new_session__"
        if lowered.startswith("/recipes"):
            return "what can i cook?"
        if lowered.startswith("/inventory"):
            return "check inventory"
        if lowered.startswith("/utilities"):
            return "utilities"
        if lowered.startswith("/groceries"):
            parts = stripped.split(maxsplit=1)
            return (
                f"order groceries for {parts[1]}"
                if len(parts) > 1 and parts[1].strip()
                else "order groceries"
            )
        if lowered.startswith("/cook"):
            parts = stripped.split(maxsplit=1)
            return stripped if len(parts) > 1 else "cook "
        return stripped

    def _telegram_api_call(self, method_name: str, payload: dict[str, object]) -> dict[str, object]:
        if not self.settings.telegram_bot_token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured.")

        url = f"https://api.telegram.org/bot{self.settings.telegram_bot_token}/{method_name}"
        try:
            response = post_json(
                url=url,
                headers={"Content-Type": "application/json"},
                payload=payload,
            )
            self.debug_log.record(
                service="telegram",
                direction="outbound",
                status="success",
                summary=f"Telegram API call succeeded: {method_name}.",
                metadata={"method": method_name},
            )
            return response
        except RuntimeError as exc:
            self.debug_log.record(
                service="telegram",
                direction="outbound",
                status="error",
                summary=f"Telegram API call failed: {method_name}.",
                metadata={"method": method_name, "error": str(exc)},
            )
            raise RuntimeError(str(exc)) from exc

    def _generate_llm_reply(self, user_id: str, text: str) -> str:
        conversation_context = self.conversation_manager.build_prompt_context(user_id)
        self.debug_log.record(
            service="telegram",
            direction="internal",
            status="fallback",
            summary="Falling back to LLM reply generation.",
            metadata={"user_id": user_id},
        )
        return self.llm_service.generate_reply(
            user_id=user_id,
            user_message=text,
            conversation_context=conversation_context,
        )

    @staticmethod
    def _prefer_llm_tooling(text: str) -> bool:
        lowered = text.lower()
        mutation_verbs = (
            "delete",
            "clear",
            "remove",
            "add",
            "update",
            "set",
            "import",
        )
        return any(verb in lowered for verb in mutation_verbs)

    def debug_snapshot(self) -> dict[str, object]:
        return {
            "configured": bool(self.settings.telegram_bot_token),
            "mode": self.settings.telegram_mode,
            "webhook_url_configured": bool(self.settings.telegram_webhook_url),
            "send_retries": self.settings.telegram_send_retries,
            "recent_events": self.debug_log.dump(),
        }
