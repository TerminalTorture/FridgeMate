from __future__ import annotations

import asyncio
from concurrent.futures import Future
from itertools import count
from typing import Awaitable, Callable

from app.core.conversation_manager import ConversationManager
from app.core.decision_engine import DecisionEngine
from app.core.heartbeat_service import HeartbeatService
from app.core.http_client import post_json
from app.core.integration_debug import IntegrationDebugLog
from app.core.llm_service import LLMService
from app.core.orchestrator import MCPFridgeOrchestrator
from app.core.settings import Settings, get_settings
from app.core.tracing import add_event, trace_scope, update_trace_metadata


class TelegramService:
    _TELEGRAM_MESSAGE_LIMIT = 4000
    _TELEGRAM_DRAFT_LIMIT = 4096

    def __init__(
        self,
        orchestrator: MCPFridgeOrchestrator,
        llm_service: LLMService,
        conversation_manager: ConversationManager,
        heartbeat_service: HeartbeatService,
        decision_engine: DecisionEngine,
        settings: Settings | None = None,
    ) -> None:
        self.orchestrator = orchestrator
        self.llm_service = llm_service
        self.conversation_manager = conversation_manager
        self.heartbeat_service = heartbeat_service
        self.decision_engine = decision_engine
        self.settings = settings or get_settings()
        self.debug_log = IntegrationDebugLog()
        self._draft_id_counter = count(1)
        self._draft_streaming_supported: bool | None = None

    def handle_webhook(
        self,
        update: dict[str, object],
        secret_token: str | None,
    ) -> dict[str, object]:
        return asyncio.run(self.handle_webhook_async(update, secret_token))

    async def handle_webhook_async(
        self,
        update: dict[str, object],
        secret_token: str | None,
    ) -> dict[str, object]:
        self._verify_secret(secret_token)
        return await self.process_update_async(update)

    def process_update(self, update: dict[str, object]) -> dict[str, object]:
        return asyncio.run(self.process_update_async(update))

    async def process_update_async(self, update: dict[str, object]) -> dict[str, object]:
        trace_request_id = str(update.get("update_id") or "") or None
        with trace_scope(
            channel="telegram",
            request_id=trace_request_id,
            metadata={
                "has_message": bool(update.get("message") or update.get("edited_message")),
                "has_callback": bool(update.get("callback_query")),
            },
        ):
            self.debug_log.record(
                service="telegram",
                direction="inbound",
                status="received",
                summary="Telegram update received.",
                metadata={
                    "has_message": bool(update.get("message") or update.get("edited_message")),
                    "has_callback": bool(update.get("callback_query")),
                },
            )

            callback = self._extract_callback(update)
            if callback is not None:
                update_trace_metadata(user_id=callback["user_id"], chat_id=callback["chat_id"])
                add_event(name="telegram_callback_received", detail={"action": callback["action"]})
                self.heartbeat_service.register_chat(callback["user_id"], callback["chat_id"])
                result = self.decision_engine.handle_callback(
                    user_id=callback["user_id"],
                    action=callback["action"],
                    intervention_id=callback["intervention_id"],
                )
                reply_text = str(result.get("message") or "")
                reply_markup = result.get("reply_markup")
                await self.answer_callback_query_async(callback["callback_query_id"])
                telegram_result = await self.send_message_async(
                    chat_id=callback["chat_id"],
                    text=reply_text,
                    reply_markup=reply_markup if isinstance(reply_markup, dict) else None,
                )
                return {"ok": True, "status": "sent", "reply": reply_text, "telegram": telegram_result}

            message = self._extract_message(update)
            if message is None:
                return {"ok": True, "status": "ignored", "reason": "no_message_payload"}

            chat_id = message["chat_id"]
            user_id = message["user_id"]
            text = message["text"]
            update_trace_metadata(user_id=user_id, chat_id=chat_id)
            add_event(name="telegram_message_received", detail={"text_chars": len(text)})
            self.heartbeat_service.register_chat(user_id, chat_id)
            reply = await self.build_reply_for_user_with_streaming_async(
                user_id=user_id,
                text=text,
                chat_id=chat_id,
            )
            telegram_result = await self.send_message_async(chat_id=chat_id, text=reply)
            return {"ok": True, "status": "sent", "reply": reply, "telegram": telegram_result}

    def get_updates(
        self,
        offset: int | None = None,
        timeout_seconds: int | None = None,
    ) -> dict[str, object]:
        return asyncio.run(self.get_updates_async(offset=offset, timeout_seconds=timeout_seconds))

    async def get_updates_async(
        self,
        offset: int | None = None,
        timeout_seconds: int | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "timeout": timeout_seconds or self.settings.telegram_poll_timeout_seconds,
            "allowed_updates": ["message", "edited_message", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset
        return await self._telegram_api_call_async("getUpdates", payload)

    def register_webhook(
        self,
        url: str | None = None,
        drop_pending_updates: bool = True,
    ) -> dict[str, object]:
        return asyncio.run(self.register_webhook_async(url=url, drop_pending_updates=drop_pending_updates))

    async def register_webhook_async(
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
            "allowed_updates": ["message", "edited_message", "callback_query"],
        }
        if self.settings.telegram_webhook_secret:
            payload["secret_token"] = self.settings.telegram_webhook_secret

        return await self._telegram_api_call_async("setWebhook", payload)

    def get_webhook_info(self) -> dict[str, object]:
        return asyncio.run(self.get_webhook_info_async())

    async def get_webhook_info_async(self) -> dict[str, object]:
        if not self.settings.telegram_bot_token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured.")
        return await self._telegram_api_call_async("getWebhookInfo", {})

    def delete_webhook(self, drop_pending_updates: bool = False) -> dict[str, object]:
        return asyncio.run(self.delete_webhook_async(drop_pending_updates=drop_pending_updates))

    async def delete_webhook_async(self, drop_pending_updates: bool = False) -> dict[str, object]:
        return await self._telegram_api_call_async(
            "deleteWebhook",
            {"drop_pending_updates": drop_pending_updates},
        )

    def send_message(
        self,
        chat_id: str | int,
        text: str,
        reply_markup: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return asyncio.run(self.send_message_async(chat_id=chat_id, text=text, reply_markup=reply_markup))

    async def send_message_async(
        self,
        chat_id: str | int,
        text: str,
        reply_markup: dict[str, object] | None = None,
    ) -> dict[str, object]:
        chunks = self._split_message_chunks(text, limit=self._TELEGRAM_MESSAGE_LIMIT)
        results: list[dict[str, object]] = []
        for index, chunk in enumerate(chunks):
            results.append(
                await self._send_message_chunk_async(
                    chat_id=chat_id,
                    text=chunk,
                    reply_markup=reply_markup if index == 0 else None,
                )
            )

        if len(results) == 1:
            return results[0]
        return {"ok": True, "messages": results, "chunks_sent": len(results)}

    async def _send_message_chunk_async(
        self,
        *,
        chat_id: str | int,
        text: str,
        reply_markup: dict[str, object] | None = None,
    ) -> dict[str, object]:
        payload = {
            "chat_id": chat_id,
            "text": text,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        retries = max(self.settings.telegram_send_retries, 1)
        last_error: RuntimeError | None = None

        for attempt in range(1, retries + 1):
            try:
                return await self._telegram_api_call_async("sendMessage", payload)
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
                    await asyncio.sleep(min(attempt, 3))

        assert last_error is not None
        raise last_error

    async def send_message_draft_async(
        self,
        chat_id: str | int,
        draft_id: int,
        text: str,
        *,
        message_thread_id: int | None = None,
        parse_mode: str | None = None,
        entities: list[dict[str, object]] | None = None,
    ) -> bool:
        payload: dict[str, object] = {
            "chat_id": self._normalize_chat_id(chat_id),
            "draft_id": draft_id,
            "text": text[: self._TELEGRAM_DRAFT_LIMIT],
        }
        if message_thread_id is not None:
            payload["message_thread_id"] = message_thread_id
        if parse_mode is not None:
            payload["parse_mode"] = parse_mode
        if entities is not None:
            payload["entities"] = entities

        await self._telegram_api_call_async("sendMessageDraft", payload)
        return True

    async def answer_callback_query_async(self, callback_query_id: str, text: str | None = None) -> dict[str, object]:
        payload: dict[str, object] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        return await self._telegram_api_call_async("answerCallbackQuery", payload)

    def build_reply_for_user(self, user_id: str, text: str, chat_id: str | None = None) -> str:
        return asyncio.run(self.build_reply_for_user_async(user_id=user_id, text=text, chat_id=chat_id))

    async def build_reply_for_user_with_streaming_async(
        self,
        *,
        user_id: str,
        text: str,
        chat_id: str,
    ) -> str:
        draft_callback = self._build_draft_sender(chat_id=chat_id, draft_id=next(self._draft_id_counter))
        await draft_callback("Working on it...")
        return await self.build_reply_for_user_async(
            user_id=user_id,
            text=text,
            chat_id=chat_id,
            draft_callback=draft_callback,
        )

    async def build_reply_for_user_async(
        self,
        user_id: str,
        text: str,
        chat_id: str | None = None,
        draft_callback: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        translated = self._translate_command(text)
        if chat_id:
            self.heartbeat_service.register_chat(user_id, chat_id)

        if translated == "__new_session__":
            session = self.conversation_manager.start_new_session(user_id, reason="telegram_new_command")
            self.conversation_manager.register_user_turn(user_id, text)
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
                "/heartbeat [status|on|off|time HH:MM|every MINUTES|interval MINUTES|now]\n"
                "/new"
            )
            self.conversation_manager.register_user_turn(user_id, text)
            self.conversation_manager.register_assistant_turn(user_id, reply)
            return reply

        if translated.startswith("__heartbeat__"):
            self.conversation_manager.register_user_turn(user_id, text)
            reply = self._handle_heartbeat_command(user_id=user_id, command=translated, chat_id=chat_id)
            self.conversation_manager.register_assistant_turn(user_id, reply)
            return reply

        self.conversation_manager.register_user_turn(user_id, text)

        override_result = self.decision_engine.apply_override_text(user_id, text)
        if override_result is not None:
            reply = str(override_result["message"])
            self.conversation_manager.register_assistant_turn(user_id, reply)
            return reply

        if self.llm_service.is_configured() and self._should_use_llm_first(text=text, translated=translated):
            fallback_result = await self._orchestrator_result_async(user_id=user_id, message=translated)
            reply = await self._generate_llm_reply_with_fallback(
                user_id=user_id,
                text=text,
                fallback_reply=str(fallback_result["reply"]),
                draft_callback=draft_callback,
            )
            self.conversation_manager.register_assistant_turn(user_id, reply)
            return reply

        if draft_callback is not None:
            await draft_callback("Checking fridge...")

        result = await self._orchestrator_result_async(user_id=user_id, message=translated)
        reply = str(result["reply"])
        if result["intent"] != "unknown":
            self.conversation_manager.register_assistant_turn(user_id, reply)
            return reply

        if self.llm_service.is_configured():
            reply = await self._generate_llm_reply_with_fallback(
                user_id=user_id,
                text=text,
                fallback_reply=reply,
                draft_callback=draft_callback,
            )
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
    def _extract_callback(update: dict[str, object]) -> dict[str, str] | None:
        candidate = update.get("callback_query")
        if not isinstance(candidate, dict):
            return None
        payload = candidate.get("data")
        if not isinstance(payload, str) or not payload.startswith("fm:"):
            return None
        parts = payload.split(":", 2)
        if len(parts) != 3:
            return None
        message = candidate.get("message", {})
        user = candidate.get("from", {})
        if not isinstance(message, dict):
            return None
        if not isinstance(user, dict):
            return None
        chat = message.get("chat", {})
        chat_id = chat.get("id") if isinstance(chat, dict) else None
        callback_query_id = candidate.get("id")
        user_id = user.get("id")
        if chat_id is None or callback_query_id is None or user_id is None:
            return None
        return {
            "callback_query_id": str(callback_query_id),
            "chat_id": str(chat_id),
            "user_id": str(user_id),
            "action": parts[1],
            "intervention_id": parts[2],
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
        if lowered.startswith("/heartbeat"):
            return "__heartbeat__ " + stripped[len("/heartbeat"):].strip()
        if lowered.startswith("/cook"):
            parts = stripped.split(maxsplit=1)
            return stripped if len(parts) > 1 else "cook "
        return stripped

    def _handle_heartbeat_command(self, *, user_id: str, command: str, chat_id: str | None) -> str:
        parts = command.split()
        subcommand = parts[1].lower() if len(parts) > 1 else "status"

        if subcommand in {"status", ""}:
            return self.heartbeat_service.format_status_message(user_id)
        if subcommand == "on":
            self.heartbeat_service.configure(user_id, enabled=True, chat_id=chat_id)
            return self.heartbeat_service.format_status_message(user_id)
        if subcommand == "off":
            self.heartbeat_service.configure(user_id, enabled=False, chat_id=chat_id)
            return self.heartbeat_service.format_status_message(user_id)
        if subcommand == "time":
            if len(parts) < 3:
                return "Use /heartbeat time HH:MM"
            try:
                hour_text, minute_text = parts[2].split(":", 1)
                hour = int(hour_text)
                minute = int(minute_text)
                if hour < 0 or hour > 23 or minute < 0 or minute > 59:
                    raise ValueError
            except ValueError:
                return "Use /heartbeat time HH:MM"
            self.heartbeat_service.configure(
                user_id,
                dinner_time=f"{hour:02d}:{minute:02d}",
                enabled=True,
                chat_id=chat_id,
            )
            return self.heartbeat_service.format_status_message(user_id)
        if subcommand in {"every", "interval"}:
            if len(parts) < 3:
                return "Use /heartbeat every MINUTES"
            try:
                interval_minutes = int(parts[2])
                if interval_minutes < 1 or interval_minutes > 1440:
                    raise ValueError
            except ValueError:
                return "Use /heartbeat every MINUTES"
            self.heartbeat_service.configure(
                user_id,
                enabled=True,
                interval_minutes=interval_minutes,
                chat_id=chat_id,
            )
            return self.heartbeat_service.format_status_message(user_id)
        if subcommand == "now":
            result = self.heartbeat_service.run_for_user(user_id, force=True, notify=False)
            return str(result.get("message") or "")
        return (
            "Heartbeat commands: /heartbeat status, /heartbeat on, /heartbeat off, "
            "/heartbeat time HH:MM, /heartbeat every MINUTES, /heartbeat interval MINUTES, /heartbeat now"
        )

    async def _telegram_api_call_async(self, method_name: str, payload: dict[str, object]) -> dict[str, object]:
        if not self.settings.telegram_bot_token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured.")

        url = f"https://api.telegram.org/bot{self.settings.telegram_bot_token}/{method_name}"
        try:
            response = await asyncio.to_thread(
                post_json,
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

    async def _generate_llm_reply_async(self, user_id: str, text: str) -> str:
        conversation_context = self.conversation_manager.build_prompt_context(user_id)
        self.debug_log.record(
            service="telegram",
            direction="internal",
            status="fallback",
            summary="Falling back to LLM reply generation.",
            metadata={"user_id": user_id},
        )
        return await asyncio.to_thread(
            self.llm_service.generate_reply,
            user_id=user_id,
            user_message=text,
            conversation_context=conversation_context,
        )

    async def _generate_llm_reply_streaming_async(
        self,
        *,
        user_id: str,
        text: str,
        draft_callback: Callable[[str], Awaitable[None]],
    ) -> str:
        conversation_context = self.conversation_manager.build_prompt_context(user_id)
        self.debug_log.record(
            service="telegram",
            direction="internal",
            status="fallback",
            summary="Using streaming LLM reply generation.",
            metadata={"user_id": user_id},
        )
        loop = asyncio.get_running_loop()
        scheduled_updates: list[Future[object]] = []

        def on_progress(partial_text: str) -> None:
            scheduled_updates.append(
                asyncio.run_coroutine_threadsafe(draft_callback(partial_text), loop)
            )

        reply = await asyncio.to_thread(
            self.llm_service.generate_reply_streaming,
            user_id=user_id,
            user_message=text,
            conversation_context=conversation_context,
            on_progress=on_progress,
        )

        await draft_callback("Finalizing reply...")

        for future in scheduled_updates:
            try:
                await asyncio.wrap_future(future)
            except Exception:
                continue
        return reply

    async def _generate_llm_reply_with_fallback(
        self,
        *,
        user_id: str,
        text: str,
        fallback_reply: str | None = None,
        draft_callback: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        try:
            if draft_callback is not None:
                return await self._generate_llm_reply_streaming_async(
                    user_id=user_id,
                    text=text,
                    draft_callback=draft_callback,
                )
            return await self._generate_llm_reply_async(user_id=user_id, text=text)
        except Exception as exc:
            self.debug_log.record(
                service="telegram",
                direction="internal",
                status="fallback_error",
                summary="LLM reply failed; using non-LLM fallback.",
                metadata={"user_id": user_id, "error": str(exc)},
            )
            reason = "the LLM provider rejected the request"
            lowered = str(exc).lower()
            if "authentication" in lowered or "unauthorized" in lowered or "invalid api key" in lowered:
                reason = "the LLM provider rejected authentication"
            if fallback_reply:
                return f"{fallback_reply}\n\nNote: richer LLM replies are currently unavailable because {reason}."
            return (
                f"I received your message, but {reason}, so I could not generate a richer reply. "
                "Basic fridge commands still work: /inventory, /recipes, /utilities, /heartbeat."
            )

    async def _orchestrator_result_async(self, *, user_id: str, message: str) -> dict[str, object]:
        return await asyncio.to_thread(
            self.orchestrator.handle_telegram_message,
            user_id=user_id,
            message=message,
        )

    def _build_draft_sender(
        self,
        *,
        chat_id: str,
        draft_id: int,
    ) -> Callable[[str], Awaitable[None]]:
        last_text = ""
        last_sent_at = 0.0

        async def send_draft(text: str) -> None:
            nonlocal last_text, last_sent_at
            if not text or self._draft_streaming_supported is False:
                return

            loop = asyncio.get_running_loop()
            now = loop.time()
            normalized = text[: self._TELEGRAM_DRAFT_LIMIT]
            force_send = normalized.startswith("Finalizing")
            if normalized == last_text:
                return
            if not force_send and last_text and now - last_sent_at < 0.35:
                return

            try:
                await self.send_message_draft_async(
                    chat_id=chat_id,
                    draft_id=draft_id,
                    text=normalized,
                )
                last_text = normalized
                last_sent_at = now
                self._draft_streaming_supported = True
            except RuntimeError as exc:
                self._draft_streaming_supported = False
                self.debug_log.record(
                    service="telegram",
                    direction="internal",
                    status="fallback",
                    summary="Telegram draft streaming unavailable; falling back to sendMessage.",
                    metadata={"chat_id": chat_id, "error": str(exc)},
                )

        return send_draft

    @staticmethod
    def _normalize_chat_id(chat_id: str | int) -> str | int:
        if isinstance(chat_id, int):
            return chat_id
        try:
            return int(chat_id)
        except (TypeError, ValueError):
            return chat_id

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
            "order",
            "buy",
            "confirm",
            "cancel",
        )
        return any(verb in lowered for verb in mutation_verbs)

    @staticmethod
    def _should_use_llm_first(*, text: str, translated: str) -> bool:
        stripped = text.strip()
        if not stripped:
            return False
        if stripped.startswith("/"):
            return False
        return True

    def debug_snapshot(self) -> dict[str, object]:
        return {
            "configured": bool(self.settings.telegram_bot_token),
            "mode": self.settings.telegram_mode,
            "webhook_url_configured": bool(self.settings.telegram_webhook_url),
            "send_retries": self.settings.telegram_send_retries,
            "recent_events": self.debug_log.dump(),
        }

    @classmethod
    def _split_message_chunks(cls, text: str, *, limit: int) -> list[str]:
        normalized = text.strip()
        if not normalized:
            return [""]
        if len(normalized) <= limit:
            return [normalized]

        chunks: list[str] = []
        remaining = normalized
        while remaining:
            if len(remaining) <= limit:
                chunks.append(remaining)
                break

            split_at = remaining.rfind("\n\n", 0, limit + 1)
            if split_at <= 0:
                split_at = remaining.rfind("\n", 0, limit + 1)
            if split_at <= 0:
                split_at = remaining.rfind(" ", 0, limit + 1)
            if split_at <= 0:
                split_at = limit

            chunk = remaining[:split_at].strip()
            if not chunk:
                chunk = remaining[:limit]
                split_at = len(chunk)
            chunks.append(chunk)
            remaining = remaining[split_at:].lstrip()

        return chunks
