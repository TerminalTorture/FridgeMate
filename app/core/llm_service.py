from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from app.core.context_store import ContextStore
from app.core.http_client import post_json
from app.core.integration_debug import IntegrationDebugLog
from app.core.settings import Settings, get_settings
from app.core.system_prompt import SYSTEM_PROMPT

if TYPE_CHECKING:
    from app.core.mcp_tools import MCPToolService


class LLMService:
    def __init__(
        self,
        store: ContextStore,
        settings: Settings | None = None,
    ) -> None:
        self.store = store
        self.settings = settings or get_settings()
        self.debug_log = IntegrationDebugLog()
        self.mcp_tool_service: MCPToolService | None = None

    def bind_mcp_tool_service(self, mcp_tool_service: MCPToolService) -> None:
        self.mcp_tool_service = mcp_tool_service

    def is_configured(self) -> bool:
        return bool(self.settings.llm_api_key)

    def generate_reply(
        self,
        user_id: str,
        user_message: str,
        conversation_context: str | None = None,
    ) -> str:
        if not self.is_configured():
            raise RuntimeError("LLM_API_KEY is not configured.")

        user_input = {
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": self._build_prompt(
                        user_id=user_id,
                        user_message=user_message,
                        conversation_context=conversation_context,
                    ),
                }
            ],
        }

        payload: dict[str, object] = {
            "model": self.settings.llm_model,
            "instructions": SYSTEM_PROMPT,
            "input": [user_input],
        }
        if self.mcp_tool_service is not None:
            payload["tools"] = self.mcp_tool_service.responses_api_tools()

        response_payload = self.create_response(payload)
        if self.mcp_tool_service is None:
            return self._extract_output_text(response_payload)
        return self._complete_tool_loop(response_payload)

    def create_response(self, payload: dict[str, object]) -> dict[str, object]:
        if not self.is_configured():
            raise RuntimeError("LLM_API_KEY is not configured.")

        try:
            response = post_json(
                url=self._responses_url(),
                headers={
                    "Authorization": f"Bearer {self.settings.llm_api_key}",
                    "Content-Type": "application/json",
                },
                payload=payload,
            )
            self.debug_log.record(
                service="llm",
                direction="outbound",
                status="success",
                summary="Responses API call succeeded.",
                metadata={"model": payload.get("model")},
            )
            return response
        except RuntimeError as exc:
            self.debug_log.record(
                service="llm",
                direction="outbound",
                status="error",
                summary="Responses API call failed.",
                metadata={"model": payload.get("model"), "error": str(exc)},
            )
            raise

    def _complete_tool_loop(self, response_payload: dict[str, object], max_rounds: int = 6) -> str:
        if self.mcp_tool_service is None:
            return self._extract_output_text(response_payload)

        current = response_payload
        for _ in range(max_rounds):
            function_calls = self._extract_function_calls(current)
            if not function_calls:
                return self._extract_output_text(current)

            tool_outputs: list[dict[str, object]] = []
            for call in function_calls:
                result = self.mcp_tool_service.call_tool(call["name"], call["arguments"])
                tool_outputs.append(
                    {
                        "type": "function_call_output",
                        "call_id": call["call_id"],
                        "output": json.dumps(self._json_safe(result)),
                    }
                )

            current = self.create_response(
                {
                    "model": self.settings.llm_model,
                    "instructions": SYSTEM_PROMPT,
                    "previous_response_id": current.get("id"),
                    "input": tool_outputs,
                    "tools": self.mcp_tool_service.responses_api_tools(),
                }
            )

        raise RuntimeError("LLM tool loop exceeded the maximum number of rounds.")

    def _build_prompt(
        self,
        user_id: str,
        user_message: str,
        conversation_context: str | None = None,
    ) -> str:
        snapshot = self.store.snapshot()
        inventory = ", ".join(
            f"{item.name} ({item.quantity:g} {item.unit})"
            for item in sorted(snapshot.inventory, key=lambda item: item.name.lower())[:12]
        )
        expiring = ", ".join(
            item.name for item in snapshot.inventory if item.expires_on is not None
        ) or "none"
        recent_meals = ", ".join(
            meal.recipe_name for meal in snapshot.meal_history[:3]
        ) or "none"
        low_stock = ", ".join(
            item.name
            for item in snapshot.inventory
            if item.quantity < item.min_desired_quantity
        ) or "none"
        session = snapshot.conversation_memory.get(str(user_id))
        current_status = session.current_status if session else ""

        return (
            f"Telegram user id: {user_id}\n"
            f"User message: {user_message}\n\n"
            f"Conversation memory:\n{conversation_context.strip() if conversation_context else 'No prior session context.'}\n\n"
            "Current MCP Fridge context:\n"
            f"- Inventory: {inventory or 'none'}\n"
            f"- Expiring-tracked items: {expiring}\n"
            f"- Low stock items: {low_stock}\n"
            f"- Recent meals: {recent_meals}\n"
            f"- Water level: {snapshot.utilities.water_level_percent}%\n"
            f"- Ice level: {snapshot.utilities.ice_level_percent}%\n"
            f"- Dietary preferences: {', '.join(snapshot.nutrition_profile.dietary_preferences) or 'none'}\n"
            f"- Current user status: {current_status or 'none'}\n"
            "\nIf the user asks to change fridge memory or status, use MCP tools to perform the update before replying."
            "\nRespond only as MCP Fridge."
        )

    def _responses_url(self) -> str:
        base = self.settings.llm_base_url or "https://api.openai.com/v1"
        return base.rstrip("/") + "/responses"

    def debug_snapshot(self) -> dict[str, object]:
        return {
            "configured": self.is_configured(),
            "model": self.settings.llm_model,
            "mcp_tools_bound": self.mcp_tool_service is not None,
            "recent_events": self.debug_log.dump(),
        }

    @staticmethod
    def _extract_output_text(response_payload: dict[str, object]) -> str:
        output_text = response_payload.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()

        output = response_payload.get("output", [])
        if isinstance(output, list):
            texts: list[str] = []
            for item in output:
                if not isinstance(item, dict):
                    continue
                if item.get("type") != "message":
                    continue
                for content_item in item.get("content", []):
                    if (
                        isinstance(content_item, dict)
                        and content_item.get("type") == "output_text"
                        and isinstance(content_item.get("text"), str)
                    ):
                        texts.append(content_item["text"])
            if texts:
                return "\n".join(texts).strip()

        raise RuntimeError("LLM response did not contain output text.")

    @staticmethod
    def _extract_function_calls(response_payload: dict[str, object]) -> list[dict[str, object]]:
        output = response_payload.get("output", [])
        if not isinstance(output, list):
            return []

        function_calls: list[dict[str, object]] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "function_call":
                continue
            raw_arguments = item.get("arguments") or "{}"
            if isinstance(raw_arguments, str):
                arguments = json.loads(raw_arguments or "{}")
            elif isinstance(raw_arguments, dict):
                arguments = raw_arguments
            else:
                arguments = {}
            function_calls.append(
                {
                    "call_id": str(item.get("call_id") or item.get("id") or ""),
                    "name": str(item.get("name") or ""),
                    "arguments": arguments,
                }
            )
        return [call for call in function_calls if call["call_id"] and call["name"]]

    @classmethod
    def _json_safe(cls, value: Any) -> Any:
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        if isinstance(value, dict):
            return {str(key): cls._json_safe(item) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._json_safe(item) for item in value]
        return value
