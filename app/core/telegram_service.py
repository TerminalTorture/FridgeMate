from __future__ import annotations

import json
from urllib import error, request

from app.core.llm_service import LLMService
from app.core.orchestrator import MCPFridgeOrchestrator
from app.core.settings import Settings, get_settings


class TelegramService:
    def __init__(
        self,
        orchestrator: MCPFridgeOrchestrator,
        llm_service: LLMService,
        settings: Settings | None = None,
    ) -> None:
        self.orchestrator = orchestrator
        self.llm_service = llm_service
        self.settings = settings or get_settings()

    def handle_webhook(
        self,
        update: dict[str, object],
        secret_token: str | None,
    ) -> dict[str, object]:
        self._verify_secret(secret_token)

        message = self._extract_message(update)
        if message is None:
            return {"ok": True, "status": "ignored", "reason": "no_message_payload"}

        chat_id = message["chat_id"]
        user_id = message["user_id"]
        text = message["text"]
        reply = self._build_reply(user_id=user_id, text=text)
        telegram_result = self.send_message(chat_id=chat_id, text=reply)
        return {"ok": True, "status": "sent", "reply": reply, "telegram": telegram_result}

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

    def send_message(self, chat_id: str | int, text: str) -> dict[str, object]:
        return self._telegram_api_call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text,
            },
        )

    def _build_reply(self, user_id: str, text: str) -> str:
        translated = self._translate_command(text)
        if translated in {"__help__", "__start__"}:
            return (
                "MCP Fridge commands:\n"
                "/recipes\n"
                "/inventory\n"
                "/groceries [recipe name]\n"
                "/cook <recipe name>\n"
                "/utilities"
            )

        result = self.orchestrator.handle_telegram_message(user_id=user_id, message=translated)
        if result["intent"] != "unknown":
            return str(result["reply"])

        if self.llm_service.is_configured():
            return self.llm_service.generate_reply(user_id=user_id, user_message=text)

        return str(result["reply"])

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
        raw_payload = json.dumps(payload).encode("utf-8")
        request_obj = request.Request(
            url=url,
            data=raw_payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(request_obj, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Telegram API call failed with status {exc.code}: {body}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"Telegram API call failed: {exc.reason}") from exc
