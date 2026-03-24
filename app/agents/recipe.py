from __future__ import annotations

from datetime import date, timedelta

from app.core.context_store import ContextStore
from app.models.api import RecipeSuggestion
from app.models.domain import GroceryLine, Recipe


class RecipeAgent:
    def __init__(self, store: ContextStore) -> None:
        self.store = store

    def list_recipes(self) -> list[Recipe]:
        snapshot = self.store.snapshot()
        return sorted(snapshot.recipe_catalog, key=lambda recipe: recipe.name.lower())

    def get_recipe(self, recipe_ref: str) -> Recipe:
        normalized_ref = recipe_ref.strip().lower().replace("-", "_")
        for recipe in self.list_recipes():
            if recipe.id == normalized_ref:
                return recipe
            if recipe.name.lower() == recipe_ref.strip().lower():
                return recipe
        raise LookupError(f"Recipe {recipe_ref} was not found.")

    def match_recipe_from_text(self, text: str) -> Recipe | None:
        normalized_text = text.lower()
        for recipe in self.list_recipes():
            if recipe.id.replace("_", " ") in normalized_text:
                return recipe
            if recipe.name.lower() in normalized_text:
                return recipe
        return None

    def suggest_recipes(self, limit: int = 3) -> list[RecipeSuggestion]:
        suggestions = [self.evaluate_recipe(recipe) for recipe in self.list_recipes()]
        ranked = sorted(
            suggestions,
            key=lambda suggestion: (
                suggestion.can_make_now,
                suggestion.coverage,
                -len(suggestion.missing_items),
            ),
            reverse=True,
        )
        return ranked[:limit]

    def add_recipe(self, recipe: Recipe) -> Recipe:
        normalized_id = self._normalize_id(recipe.id or recipe.name)
        recipe = recipe.model_copy(update={"id": normalized_id})

        def mutator(state):
            for index, existing in enumerate(state.recipe_catalog):
                if existing.id == normalized_id or existing.name.lower() == recipe.name.lower():
                    state.recipe_catalog[index] = recipe
                    return {"recipe_id": normalized_id, "mode": "updated"}

            state.recipe_catalog.append(recipe)
            return {"recipe_id": normalized_id, "mode": "created"}

        self.store.update(
            agent="recipe_agent",
            action="add_recipe",
            summary=f"Added or updated recipe {recipe.name}.",
            mutator=mutator,
        )
        return self.get_recipe(normalized_id)

    def evaluate_recipe(self, recipe: Recipe) -> RecipeSuggestion:
        snapshot = self.store.snapshot()
        inventory_map = {item.name.lower(): item for item in snapshot.inventory}
        essential_ingredients = [item for item in recipe.ingredients if not item.optional]
        matched_count = 0
        expiring_bonus = 0.0
        missing_items: list[GroceryLine] = []
        dislikes = {value.lower() for value in snapshot.behaviour.disliked_ingredients}

        for ingredient in essential_ingredients:
            item = inventory_map.get(ingredient.name.lower())
            available = 0.0 if item is None else item.quantity
            if available >= ingredient.quantity:
                matched_count += 1
                if item and item.expires_on and item.expires_on <= date.today() + timedelta(days=3):
                    expiring_bonus += 0.15
            else:
                missing_items.append(
                    GroceryLine(
                        name=ingredient.name,
                        quantity=round(max(ingredient.quantity - available, 0.0), 2),
                        unit=ingredient.unit,
                        reason=f"missing for {recipe.name}",
                    )
                )

        coverage = matched_count / max(len(essential_ingredients), 1)
        score = min(1.0, coverage + expiring_bonus)
        contains_disliked = any(
            ingredient.name.lower() in dislikes for ingredient in recipe.ingredients
        )
        if contains_disliked:
            score = max(0.0, score - 0.35)

        can_make_now = len(missing_items) == 0
        rationale = (
            "Ready to cook with current inventory."
            if can_make_now
            else f"Needs {len(missing_items)} more ingredient(s)."
        )
        if expiring_bonus:
            rationale += " Uses ingredients that expire soon."
        if contains_disliked:
            rationale += " Includes a disliked ingredient."

        return RecipeSuggestion(
            recipe_id=recipe.id,
            name=recipe.name,
            description=recipe.description,
            can_make_now=can_make_now,
            coverage=round(score, 2),
            missing_items=missing_items,
            calories=recipe.calories,
            protein_g=recipe.protein_g,
            tags=recipe.tags,
            rationale=rationale,
        )

    @staticmethod
    def _normalize_id(value: str) -> str:
        return value.strip().lower().replace("-", "_").replace(" ", "_")
