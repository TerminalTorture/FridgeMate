from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import TYPE_CHECKING, Any, Callable

from pydantic import BaseModel

from app.core.context_store import ContextStore
from app.core.http_client import post_json, stream_json_sse
from app.core.integration_debug import IntegrationDebugLog
from app.core.prompt_builder import PromptBuilder
from app.core.settings import Settings, get_settings
from app.core.system_prompt import SYSTEM_PROMPT
from app.core.tracing import add_event, record_tools_exposed
from app.models.domain import Recipe

if TYPE_CHECKING:
    from app.core.mcp_tools import MCPToolService


@dataclass
class LLMReplyResult:
    text: str
    tool_results: list[dict[str, object]] = field(default_factory=list)


class LLMService:
    def __init__(
        self,
        store: ContextStore,
        prompt_builder: PromptBuilder | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.store = store
        self.prompt_builder = prompt_builder
        self.settings = settings or get_settings()
        self.debug_log = IntegrationDebugLog()
        self.mcp_tool_service: MCPToolService | None = None

    def bind_mcp_tool_service(self, mcp_tool_service: MCPToolService) -> None:
        self.mcp_tool_service = mcp_tool_service
        if self.prompt_builder is not None:
            self.prompt_builder.bind_mcp_tool_service(mcp_tool_service)

    def is_configured(self) -> bool:
        return bool(self.settings.llm_api_key)

    def generate_reply(
        self,
        user_id: str,
        user_message: str,
        conversation_context: str | None = None,
    ) -> str:
        return self.generate_reply_result(
            user_id=user_id,
            user_message=user_message,
            conversation_context=conversation_context,
        ).text

    def generate_reply_result(
        self,
        user_id: str,
        user_message: str,
        conversation_context: str | None = None,
    ) -> LLMReplyResult:
        if not self.is_configured():
            raise RuntimeError("LLM_API_KEY is not configured.")

        instructions, payload = self._build_response_payload(
            user_id=user_id,
            user_message=user_message,
            conversation_context=conversation_context,
        )
        add_event(
            name="llm_generate_reply",
            detail={
                "model": self.settings.llm_model,
                "instructions_chars": len(instructions),
                "input_chars": len(str(payload.get("input", []))),
                "streaming": False,
            },
        )

        response_payload = self.create_response(payload)
        if self.mcp_tool_service is None:
            return LLMReplyResult(text=self._extract_output_text(response_payload))
        return self._complete_tool_loop(
            response_payload,
            instructions=instructions,
        )

    def generate_reply_streaming(
        self,
        user_id: str,
        user_message: str,
        conversation_context: str | None = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> str:
        return self.generate_reply_streaming_result(
            user_id=user_id,
            user_message=user_message,
            conversation_context=conversation_context,
            on_progress=on_progress,
        ).text

    def generate_reply_streaming_result(
        self,
        user_id: str,
        user_message: str,
        conversation_context: str | None = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> LLMReplyResult:
        if not self.is_configured():
            raise RuntimeError("LLM_API_KEY is not configured.")

        instructions, payload = self._build_response_payload(
            user_id=user_id,
            user_message=user_message,
            conversation_context=conversation_context,
        )
        add_event(
            name="llm_generate_reply",
            detail={
                "model": self.settings.llm_model,
                "instructions_chars": len(instructions),
                "input_chars": len(str(payload.get("input", []))),
                "streaming": True,
            },
        )

        if on_progress is not None:
            on_progress("Thinking...")

        response_payload = self.create_response_streaming(payload, on_progress=on_progress)
        if self.mcp_tool_service is None:
            return LLMReplyResult(text=self._extract_output_text(response_payload))
        return self._complete_tool_loop(
            response_payload,
            instructions=instructions,
            on_progress=on_progress,
            stream_responses=True,
        )

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

    def create_chat_completion(self, payload: dict[str, object]) -> dict[str, object]:
        if not self.is_configured():
            raise RuntimeError("LLM_API_KEY is not configured.")

        metadata = self._chat_completion_log_metadata(payload)
        try:
            response = post_json(
                url=self._chat_completions_url(),
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
                summary="Chat Completions API call succeeded.",
                metadata=metadata,
            )
            return response
        except RuntimeError as exc:
            self.debug_log.record(
                service="llm",
                direction="outbound",
                status="error",
                summary="Chat Completions API call failed.",
                metadata={**metadata, "error": str(exc)},
            )
            raise

    def create_response_streaming(
        self,
        payload: dict[str, object],
        *,
        on_progress: Callable[[str], None] | None = None,
    ) -> dict[str, object]:
        if not self.is_configured():
            raise RuntimeError("LLM_API_KEY is not configured.")

        stream_payload = dict(payload)
        stream_payload["stream"] = True
        partial_text = ""
        final_response: dict[str, object] | None = None

        try:
            for event in stream_json_sse(
                url=self._responses_url(),
                headers={
                    "Authorization": f"Bearer {self.settings.llm_api_key}",
                    "Content-Type": "application/json",
                },
                payload=stream_payload,
            ):
                event_type = str(event.get("type") or "")
                if event_type == "response.output_text.delta":
                    delta = event.get("delta")
                    if isinstance(delta, str) and delta:
                        partial_text += delta
                        if on_progress is not None and partial_text.strip():
                            on_progress(partial_text.strip())
                response = event.get("response")
                if isinstance(response, dict):
                    final_response = response

            self.debug_log.record(
                service="llm",
                direction="outbound",
                status="success",
                summary="Streaming Responses API call succeeded.",
                metadata={"model": payload.get("model")},
            )
        except RuntimeError as exc:
            self.debug_log.record(
                service="llm",
                direction="outbound",
                status="fallback",
                summary="Streaming Responses API call failed; retrying without streaming.",
                metadata={"model": payload.get("model"), "error": str(exc)},
            )
            return self.create_response(payload)

        if final_response is not None:
            return final_response
        if partial_text.strip():
            return {
                "output_text": partial_text.strip(),
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": partial_text.strip()}],
                    }
                ],
            }
        raise RuntimeError("Streaming LLM response did not contain a completed response.")

    def generate_online_recipe_preview(
        self,
        *,
        user_id: str,
        user_message: str,
        conversation_context: str | None,
        recipe: Recipe,
    ) -> str:
        if not self.is_configured():
            raise RuntimeError("LLM_API_KEY is not configured.")

        preferences = self.store.user_preferences(user_id)
        ingredients_text = ", ".join(
            f"{ingredient.name} ({ingredient.quantity:g} {ingredient.unit})"
            for ingredient in recipe.ingredients[:8]
        ) or "not available"
        source_text = recipe.source_title or recipe.source_url or "online source"
        prompt = (
            f"Telegram user id: {user_id}\n"
            f"Original request: {user_message}\n"
            f"Conversation context:\n{conversation_context.strip() if conversation_context else 'No prior session context.'}\n\n"
            "User preference summary:\n"
            f"- Mode: {preferences.mode}\n"
            f"- Max prep minutes: {preferences.max_prep_minutes}\n"
            f"- Dietary preferences: {', '.join(preferences.dietary_preferences) or 'none'}\n\n"
            "Found online recipe candidate:\n"
            f"- Name: {recipe.name}\n"
            f"- Description: {recipe.description}\n"
            f"- Cuisine: {recipe.cuisine}\n"
            f"- Prep minutes: {recipe.prep_minutes}\n"
            f"- Effort score: {recipe.effort_score}\n"
            f"- Calories: {recipe.calories}\n"
            f"- Protein: {recipe.protein_g}\n"
            f"- Source: {source_text}\n"
            f"- URL: {recipe.source_url or 'not available'}\n"
            f"- Key ingredients: {ingredients_text}\n"
        )
        payload: dict[str, object] = {
            "model": self.settings.llm_model,
            "instructions": (
                "You are MCP Fridge writing a Telegram reply. "
                "Summarize the found online recipe in plain text with light personalization based only on the provided context. "
                "Include the recipe name, the source title or URL, a direct link line if a URL is available, and a short key-ingredients summary. "
                "Optionally mention why it may or may not fit the user's preferences or prep constraints. "
                "Do not invent ingredients, steps, links, or user preferences. "
                "End with exactly one short confirmation question asking whether to import it into the recipe list."
            ),
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": prompt}],
                }
            ],
        }
        response_payload = self.create_response(payload)
        return self._extract_output_text(response_payload)

    def _complete_tool_loop(
        self,
        response_payload: dict[str, object],
        *,
        instructions: str,
        max_rounds: int = 6,
        on_progress: Callable[[str], None] | None = None,
        stream_responses: bool = False,
    ) -> LLMReplyResult:
        if self.mcp_tool_service is None:
            return LLMReplyResult(text=self._extract_output_text(response_payload))

        current = response_payload
        tool_results: list[dict[str, object]] = []
        for _ in range(max_rounds):
            function_calls = self._extract_function_calls(current)
            if not function_calls:
                if on_progress is not None:
                    on_progress("Finalizing reply...")
                return LLMReplyResult(
                    text=self._extract_output_text(current),
                    tool_results=tool_results,
                )

            if on_progress is not None:
                on_progress(self._tool_progress_message(function_calls))

            tool_outputs: list[dict[str, object]] = []
            for call in function_calls:
                result = self.mcp_tool_service.call_tool(call["name"], call["arguments"])
                tool_results.append(result)
                tool_outputs.append(
                    {
                        "type": "function_call_output",
                        "call_id": call["call_id"],
                        "output": json.dumps(self._json_safe(result)),
                    }
                )

            next_payload = {
                "model": self.settings.llm_model,
                "instructions": instructions,
                "previous_response_id": current.get("id"),
                "input": tool_outputs,
                "tools": self.mcp_tool_service.responses_api_tools(),
            }
            if stream_responses:
                current = self.create_response_streaming(next_payload, on_progress=on_progress)
            else:
                current = self.create_response(next_payload)
            add_event(
                name="llm_tool_round",
                detail={
                    "round_tool_calls": len(function_calls),
                    "tool_outputs": len(tool_outputs),
                },
            )

        raise RuntimeError("LLM tool loop exceeded the maximum number of rounds.")

    def _build_response_payload(
        self,
        *,
        user_id: str,
        user_message: str,
        conversation_context: str | None,
    ) -> tuple[str, dict[str, object]]:
        instructions = self._build_instructions(
            user_id=user_id,
            user_message=user_message,
            conversation_context=conversation_context,
        )
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
            "instructions": instructions,
            "input": [user_input],
        }
        if self.mcp_tool_service is not None:
            tools = self.mcp_tool_service.responses_api_tools()
            payload["tools"] = tools
            record_tools_exposed([str(tool.get("name") or "") for tool in tools])
        return instructions, payload

    @staticmethod
    def _tool_progress_message(function_calls: list[dict[str, object]]) -> str:
        if not function_calls:
            return "Checking fridge data..."
        if len(function_calls) == 1:
            name = str(function_calls[0].get("name") or "tool")
            return f"Checking fridge data with {name}..."
        return "Checking fridge data..."

    def _build_prompt(
        self,
        user_id: str,
        user_message: str,
        conversation_context: str | None,
    ) -> str:
        if self.prompt_builder is not None:
            return self.prompt_builder.build_user_input(
                user_id=user_id,
                user_message=user_message,
            )

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

    def _build_instructions(
        self,
        *,
        user_id: str,
        user_message: str,
        conversation_context: str | None,
    ) -> str:
        recipe_search_policy = self._recipe_search_policy_instruction(user_message)
        if self.prompt_builder is not None:
            instructions = self.prompt_builder.build_instructions(
                user_id=user_id,
                user_message=user_message,
                conversation_context=conversation_context,
            )
            if recipe_search_policy:
                return f"{instructions}\n\n## Recipe Search Policy\n{recipe_search_policy}"
            return instructions
        if recipe_search_policy:
            return f"{SYSTEM_PROMPT}\n\nRecipe search policy:\n{recipe_search_policy}"
        return SYSTEM_PROMPT

    def _responses_url(self) -> str:
        base = self.settings.llm_base_url or "https://api.openai.com/v1"
        return base.rstrip("/") + "/responses"

    def _chat_completions_url(self) -> str:
        base = self.settings.llm_base_url or "https://api.openai.com/v1"
        return base.rstrip("/") + "/chat/completions"

    def chat_completion_request_fingerprint(self, payload: dict[str, object]) -> str:
        canonical_payload = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()[:12]

    def debug_snapshot(self) -> dict[str, object]:
        return {
            "configured": self.is_configured(),
            "model": self.settings.llm_model,
            "mcp_tools_bound": self.mcp_tool_service is not None,
            "recent_events": self.debug_log.dump(),
        }

    @classmethod
    def is_explicit_online_recipe_request(cls, user_message: str) -> bool:
        lowered = user_message.strip().lower()
        if not lowered:
            return False
        has_recipe_cue = bool(
            re.search(r"\b(recipe|recipes|meal|dish|cook|dinner|lunch|breakfast)\b", lowered)
        )
        if not has_recipe_cue:
            return False
        explicit_cues = (
            "search for",
            "search online",
            "search the web",
            "find online",
            "on the web",
            "from the web",
            "online recipe",
            "online recipes",
            "new recipe",
            "new recipes",
            "something new",
        )
        return any(cue in lowered for cue in explicit_cues)

    @classmethod
    def _recipe_search_policy_instruction(cls, user_message: str) -> str:
        if cls.is_explicit_online_recipe_request(user_message):
            return (
                "The user explicitly asked for an online or new recipe. "
                "Use the MCP tool search_and_import_recipe first with selection_index=0 unless the user asked to review candidates. "
                "Include user_id when calling recipe search tools so the user's search_model preference applies. "
                "After tool execution, say the recipe was found online and include source_title or source_url when present. "
                "Do not claim online search happened unless the tool result is present."
            )
        return (
            "Do not use online recipe search for ordinary recipe suggestions. "
            "Use local recipe and inventory tools first unless the user explicitly asks for an online or new recipe."
        )

    def _chat_completion_log_metadata(self, payload: dict[str, object]) -> dict[str, object]:
        messages = payload.get("messages")
        message_roles: list[str] = []
        content_lengths: list[int] = []
        if isinstance(messages, list):
            for message in messages:
                if not isinstance(message, dict):
                    continue
                message_roles.append(str(message.get("role") or "unknown"))
                content_lengths.append(self._message_content_length(message.get("content")))
        return {
            "endpoint": self._chat_completions_url(),
            "model": payload.get("model"),
            "request_fingerprint": self.chat_completion_request_fingerprint(payload),
            "message_count": len(messages) if isinstance(messages, list) else 0,
            "message_roles": message_roles,
            "content_lengths": content_lengths,
            "has_web_search_options": isinstance(payload.get("web_search_options"), dict),
        }

    @staticmethod
    def _message_content_length(content: object) -> int:
        if isinstance(content, str):
            return len(content)
        if isinstance(content, list):
            total = 0
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    total += len(str(item["text"]))
            return total
        return 0

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
