from __future__ import annotations

import json

from app.models.domain import Recipe, RecipeIngredient
from app.core.llm_service import LLMService


class RecipeDiscoveryService:
    def __init__(self, llm_service: LLMService) -> None:
        self.llm_service = llm_service

    def search_online_recipes(self, query: str, max_results: int = 3) -> list[Recipe]:
        query = query.strip()
        if not query:
            raise ValueError("query must not be empty.")

        payload = {
            "model": self.llm_service.settings.llm_model,
            "tools": [{"type": "web_search"}],
            "input": [
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "Search the public web for recipes relevant to the user's request. "
                                "Return only valid JSON with this structure: "
                                "{\"recipes\": [{\"id\": \"slug\", \"name\": \"...\", "
                                "\"description\": \"...\", \"ingredients\": [{\"name\": \"...\", "
                                "\"quantity\": 1, \"unit\": \"unit\", \"optional\": false}], "
                                "\"instructions\": [\"...\"], \"tags\": [\"...\"], "
                                "\"calories\": 0, \"protein_g\": 0, \"cuisine\": \"global\", "
                                "\"source_url\": \"https://...\", \"source_title\": \"...\"}]}. "
                                "Use short practical instructions. If nutrition is unknown, use 0."
                            ),
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": f"Find up to {max_results} online recipes for: {query}",
                        }
                    ],
                },
            ],
        }

        response_payload = self.llm_service.create_response(payload)
        response_text = self.llm_service._extract_output_text(response_payload)
        parsed = self._extract_json(response_text)

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
                    cuisine=str(item.get("cuisine") or "global"),
                    source_url=str(item.get("source_url")) if item.get("source_url") else None,
                    source_title=str(item.get("source_title")) if item.get("source_title") else None,
                )
            )

        return recipes

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
