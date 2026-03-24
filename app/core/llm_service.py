from __future__ import annotations

import json
from urllib import error, request

from app.core.context_store import ContextStore
from app.core.settings import Settings, get_settings
from app.core.system_prompt import SYSTEM_PROMPT


class LLMService:
    def __init__(
        self,
        store: ContextStore,
        settings: Settings | None = None,
    ) -> None:
        self.store = store
        self.settings = settings or get_settings()

    def is_configured(self) -> bool:
        return bool(self.settings.llm_api_key)

    def generate_reply(self, user_id: str, user_message: str) -> str:
        if not self.is_configured():
            raise RuntimeError("LLM_API_KEY is not configured.")

        payload = {
            "model": self.settings.llm_model,
            "instructions": SYSTEM_PROMPT,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": self._build_prompt(user_id=user_id, user_message=user_message),
                        }
                    ],
                }
            ],
        }

        response_payload = self._post_json(
            url=self._responses_url(),
            headers={
                "Authorization": f"Bearer {self.settings.llm_api_key}",
                "Content-Type": "application/json",
            },
            payload=payload,
        )
        return self._extract_output_text(response_payload)

    def _build_prompt(self, user_id: str, user_message: str) -> str:
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

        return (
            f"Telegram user id: {user_id}\n"
            f"User message: {user_message}\n\n"
            "Current MCP Fridge context:\n"
            f"- Inventory: {inventory or 'none'}\n"
            f"- Expiring-tracked items: {expiring}\n"
            f"- Low stock items: {low_stock}\n"
            f"- Recent meals: {recent_meals}\n"
            f"- Water level: {snapshot.utilities.water_level_percent}%\n"
            f"- Ice level: {snapshot.utilities.ice_level_percent}%\n"
            f"- Dietary preferences: {', '.join(snapshot.nutrition_profile.dietary_preferences) or 'none'}\n"
            "\nRespond only as MCP Fridge."
        )

    def _responses_url(self) -> str:
        base = self.settings.llm_base_url or "https://api.openai.com/v1"
        return base.rstrip("/") + "/responses"

    @staticmethod
    def _post_json(url: str, headers: dict[str, str], payload: dict[str, object]) -> dict[str, object]:
        raw_payload = json.dumps(payload).encode("utf-8")
        request_obj = request.Request(
            url=url,
            data=raw_payload,
            headers=headers,
            method="POST",
        )
        try:
            with request.urlopen(request_obj, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"LLM request failed with status {exc.code}: {body}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"LLM request failed: {exc.reason}") from exc

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

