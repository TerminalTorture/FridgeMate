from __future__ import annotations

import json

from app.core.search_models import DEFAULT_SEARCH_MODEL, is_valid_search_model
from app.core.llm_service import LLMService
from app.models.domain import Recipe, RecipeIngredient


class RecipeSearchChatCompletionError(RuntimeError):
    def __init__(self, message: str, *, request_fingerprint: str, original_error: Exception) -> None:
        super().__init__(message)
        self.request_fingerprint = request_fingerprint
        self.original_error = original_error


class RecipeDiscoveryService:
    def __init__(self, llm_service: LLMService) -> None:
        self.llm_service = llm_service

    def search_online_recipes(
        self,
        query: str,
        max_results: int = 3,
        *,
        user_id: str | None = None,
    ) -> list[Recipe]:
        query = query.strip()
        if not query:
            raise ValueError("query must not be empty.")

        search_model = self._resolve_search_model(user_id)
        payload = self._build_chat_completion_payload(
            query=query,
            max_results=max_results,
            search_model=search_model,
        )
        self._validate_chat_completion_payload(payload)
        request_fingerprint = self.llm_service.chat_completion_request_fingerprint(payload)

        response_payload = self._create_chat_completion_with_retry(payload)
        try:
            response_text = self._extract_chat_completion_text(response_payload)
            parsed = self._extract_json(response_text)
        except json.JSONDecodeError as exc:
            self._log_recipe_search_parse_failure(
                request_fingerprint=request_fingerprint,
                response_text=response_text,
                error=exc,
            )
            raise RecipeSearchChatCompletionError(
                f"Online recipe search returned invalid JSON: {exc}",
                request_fingerprint=request_fingerprint,
                original_error=exc,
            ) from exc
        except RuntimeError as exc:
            self.llm_service.debug_log.record(
                service="llm",
                direction="internal",
                status="error",
                summary="Recipe search response could not be interpreted.",
                metadata={
                    "request_fingerprint": request_fingerprint,
                    "error": str(exc),
                },
            )
            raise RecipeSearchChatCompletionError(
                f"Online recipe search response was invalid: {exc}",
                request_fingerprint=request_fingerprint,
                original_error=exc,
            ) from exc

        recipes: list[Recipe] = []
        for item in parsed.get("recipes", [])[:max_results]:
            if not isinstance(item, dict):
                continue
            recipes.append(
                Recipe(
                    id=self._slugify(str(item.get("id") or item.get("name") or "online_recipe")),
                    name=str(item.get("name") or "Imported Recipe"),
                    description=str(item.get("description") or "Imported from online recipe search."),
                    ingredients=[
                        RecipeIngredient(
                            name=str(ingredient.get("name") or "ingredient"),
                            quantity=float(ingredient.get("quantity") or 1),
                            unit=str(ingredient.get("unit") or "unit"),
                            optional=bool(ingredient.get("optional", False)),
                        )
                        for ingredient in item.get("ingredients", [])
                        if isinstance(ingredient, dict)
                    ],
                    instructions=[
                        str(step)
                        for step in item.get("instructions", [])
                        if isinstance(step, str)
                    ],
                    tags=[
                        str(tag)
                        for tag in item.get("tags", [])
                        if isinstance(tag, str)
                    ],
                    calories=int(item.get("calories") or 0),
                    protein_g=int(item.get("protein_g") or 0),
                    prep_minutes=int(item.get("prep_minutes") or 10),
                    step_count=int(item.get("step_count") or 3),
                    effort_score=self._normalized_effort_score(item.get("effort_score")),
                    suitable_when_tired=bool(item.get("suitable_when_tired", True)),
                    cuisine=str(item.get("cuisine") or "global"),
                    source_url=self._optional_text(item.get("source_url")),
                    source_title=self._optional_text(item.get("source_title")),
                )
            )

        return recipes

    def _build_chat_completion_payload(
        self,
        *,
        query: str,
        max_results: int,
        search_model: str,
    ) -> dict[str, object]:
        return {
            "model": search_model,
            "web_search_options": {},
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "recipe_search_results",
                    "strict": True,
                    "schema": self._recipe_search_schema(),
                },
            },
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Search the public web for recipes relevant to the user's request. "
                        "Return recipe results that exactly match the supplied JSON schema. "
                        "Use short practical instructions. If nutrition is unknown, use 0. "
                        "If source_url or source_title are unknown, use an empty string."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Find up to {max_results} online recipes for: {query}",
                },
            ],
        }

    def _create_chat_completion_with_retry(self, payload: dict[str, object]) -> dict[str, object]:
        request_fingerprint = self.llm_service.chat_completion_request_fingerprint(payload)
        try:
            return self.llm_service.create_chat_completion(payload)
        except RuntimeError as first_error:
            if not self._should_retry_chat_completion_error(first_error):
                raise RecipeSearchChatCompletionError(
                    f"Online recipe search failed: {first_error}",
                    request_fingerprint=request_fingerprint,
                    original_error=first_error,
                ) from first_error
            try:
                return self.llm_service.create_chat_completion(payload)
            except RuntimeError as second_error:
                raise RecipeSearchChatCompletionError(
                    f"Online recipe search failed: {first_error}",
                    request_fingerprint=request_fingerprint,
                    original_error=first_error,
                ) from second_error

    @staticmethod
    def _should_retry_chat_completion_error(error: RuntimeError) -> bool:
        message = str(error).lower()
        if "could not parse the json body of your request" in message:
            return True
        if "http request failed" not in message:
            return False
        retriable_statuses = ("status 408", "status 409", "status 429", "status 500", "status 502", "status 503", "status 504")
        if any(status in message for status in retriable_statuses):
            return True
        return "timed out" in message or "temporarily unavailable" in message

    @staticmethod
    def _validate_chat_completion_payload(payload: dict[str, object]) -> None:
        model = payload.get("model")
        if not isinstance(model, str) or not is_valid_search_model(model):
            raise ValueError("Recipe search payload requires a supported search model.")
        web_search_options = payload.get("web_search_options")
        if not isinstance(web_search_options, dict):
            raise ValueError("Recipe search payload requires web_search_options to be an object.")
        response_format = payload.get("response_format")
        if not isinstance(response_format, dict):
            raise ValueError("Recipe search payload requires response_format.")
        if response_format.get("type") != "json_schema":
            raise ValueError("Recipe search payload response_format must use json_schema.")
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            raise ValueError("Recipe search payload requires at least one message.")
        for index, message in enumerate(messages):
            if not isinstance(message, dict):
                raise ValueError(f"Recipe search payload message {index} must be an object.")
            role = message.get("role")
            content = message.get("content")
            if not isinstance(role, str) or not role.strip():
                raise ValueError(f"Recipe search payload message {index} must include a role.")
            if not isinstance(content, str) or not content.strip():
                raise ValueError(f"Recipe search payload message {index} must include string content.")

    def _resolve_search_model(self, user_id: str | None) -> str:
        if user_id:
            search_model = self.llm_service.store.user_preferences(user_id).search_model
            if is_valid_search_model(search_model):
                return search_model
        return DEFAULT_SEARCH_MODEL

    @staticmethod
    def _extract_chat_completion_text(response_payload: dict[str, object]) -> str:
        choices = response_payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("Chat Completions response did not contain choices.")
        first = choices[0]
        if not isinstance(first, dict):
            raise RuntimeError("Chat Completions response did not contain a valid choice.")
        message = first.get("message")
        if not isinstance(message, dict):
            raise RuntimeError("Chat Completions response did not contain a valid message.")
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            texts: list[str] = []
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    texts.append(str(item["text"]))
            if texts:
                return "\n".join(texts).strip()
        raise RuntimeError("Chat Completions response did not contain text content.")

    @staticmethod
    def _extract_json(raw_text: str) -> dict[str, object]:
        stripped = raw_text.strip()
        if stripped.startswith("```"):
            stripped = stripped.split("\n", 1)[1]
            if stripped.endswith("```"):
                stripped = stripped[:-3]
        stripped = stripped.strip()
        if not stripped.startswith("{"):
            start = stripped.find("{")
            end = stripped.rfind("}")
            if start != -1 and end != -1 and end > start:
                stripped = stripped[start : end + 1]
        return json.loads(stripped)

    @staticmethod
    def _slugify(value: str) -> str:
        return value.strip().lower().replace("-", "_").replace(" ", "_")

    @staticmethod
    def _optional_text(value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _normalized_effort_score(value: object) -> float:
        try:
            numeric = float(value or 0.4)
        except (TypeError, ValueError):
            return 0.4
        return max(0.0, min(numeric, 1.0))

    @staticmethod
    def _recipe_search_schema() -> dict[str, object]:
        ingredient_schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "name": {"type": "string"},
                "quantity": {"type": "number"},
                "unit": {"type": "string"},
                "optional": {"type": "boolean"},
            },
            "required": ["name", "quantity", "unit", "optional"],
        }
        recipe_schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "id": {"type": "string"},
                "name": {"type": "string"},
                "description": {"type": "string"},
                "ingredients": {"type": "array", "items": ingredient_schema},
                "instructions": {"type": "array", "items": {"type": "string"}},
                "tags": {"type": "array", "items": {"type": "string"}},
                "calories": {"type": "number"},
                "protein_g": {"type": "number"},
                "prep_minutes": {"type": "number"},
                "step_count": {"type": "number"},
                "effort_score": {"type": "number"},
                "suitable_when_tired": {"type": "boolean"},
                "cuisine": {"type": "string"},
                "source_url": {"type": "string"},
                "source_title": {"type": "string"},
            },
            "required": [
                "id",
                "name",
                "description",
                "ingredients",
                "instructions",
                "tags",
                "calories",
                "protein_g",
                "prep_minutes",
                "step_count",
                "effort_score",
                "suitable_when_tired",
                "cuisine",
                "source_url",
                "source_title",
            ],
        }
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "recipes": {
                    "type": "array",
                    "items": recipe_schema,
                }
            },
            "required": ["recipes"],
        }

    def _log_recipe_search_parse_failure(
        self,
        *,
        request_fingerprint: str,
        response_text: str,
        error: json.JSONDecodeError,
    ) -> None:
        window_start = max(error.pos - 120, 0)
        window_end = min(error.pos + 120, len(response_text))
        preview_window = response_text[window_start:window_end].replace("\n", "\\n")
        self.llm_service.debug_log.record(
            service="llm",
            direction="internal",
            status="error",
            summary="Recipe search response JSON parse failed.",
            metadata={
                "request_fingerprint": request_fingerprint,
                "error": str(error),
                "error_line": error.lineno,
                "error_column": error.colno,
                "error_position": error.pos,
                "response_chars": len(response_text),
                "response_preview_head": response_text[:240].replace("\n", "\\n"),
                "response_preview_window": preview_window,
            },
        )
