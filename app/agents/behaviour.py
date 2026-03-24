from __future__ import annotations

from datetime import datetime

from app.core.context_store import ContextStore
from app.core.time_utils import utc_now
from app.models.domain import GroceryLine, Recipe


class BehaviourAgent:
    def __init__(self, store: ContextStore) -> None:
        self.store = store

    def record_command(self, command: str) -> dict[str, str | int]:
        period = self._period_bucket(utc_now())

        def mutator(state):
            state.behaviour.command_usage[command] = (
                state.behaviour.command_usage.get(command, 0) + 1
            )
            state.behaviour.active_periods[period] = (
                state.behaviour.active_periods.get(period, 0) + 1
            )
            return {"command": command, "period": period}

        self.store.update(
            agent="behaviour_agent",
            action="record_command",
            summary=f"Recorded user command: {command}.",
            mutator=mutator,
        )
        return {"command": command, "period": period}

    def record_recipe_interest(self, recipe: Recipe) -> None:
        def mutator(state):
            state.behaviour.recipe_requests[recipe.id] = (
                state.behaviour.recipe_requests.get(recipe.id, 0) + 1
            )
            return {"recipe_id": recipe.id}

        self.store.update(
            agent="behaviour_agent",
            action="record_recipe_interest",
            summary=f"Tracked interest in recipe {recipe.name}.",
            mutator=mutator,
        )

    def record_meal(self, recipe: Recipe) -> dict[str, str]:
        period = self._period_bucket(utc_now())

        def mutator(state):
            state.behaviour.cooked_recipes[recipe.id] = (
                state.behaviour.cooked_recipes.get(recipe.id, 0) + 1
            )
            state.behaviour.preferred_cuisines[recipe.cuisine] = (
                state.behaviour.preferred_cuisines.get(recipe.cuisine, 0) + 1
            )
            state.behaviour.active_periods[period] = (
                state.behaviour.active_periods.get(period, 0) + 1
            )
            for ingredient in recipe.ingredients:
                key = ingredient.name.lower()
                state.behaviour.favourite_ingredients[key] = (
                    state.behaviour.favourite_ingredients.get(key, 0) + 1
                )
            return {"recipe_id": recipe.id, "period": period}

        self.store.update(
            agent="behaviour_agent",
            action="record_meal",
            summary=f"Learned from cooked recipe {recipe.name}.",
            mutator=mutator,
        )
        return {"recipe": recipe.name, "period": period}

    def predict_restock_candidates(self, limit: int = 3) -> list[GroceryLine]:
        snapshot = self.store.snapshot()
        inventory_map = {item.name.lower(): item for item in snapshot.inventory}
        ranked = sorted(
            snapshot.behaviour.favourite_ingredients.items(),
            key=lambda item: item[1],
            reverse=True,
        )

        candidates: list[GroceryLine] = []
        seen: set[str] = set()
        for ingredient, score in ranked:
            if score < 2 or ingredient in seen:
                continue

            item = inventory_map.get(ingredient)
            if item is not None and item.quantity > item.min_desired_quantity:
                continue

            quantity = 2.0
            unit = "unit"
            if item is not None:
                quantity = max(item.min_desired_quantity - item.quantity, 1.0)
                unit = item.unit

            candidates.append(
                GroceryLine(
                    name=ingredient,
                    quantity=round(quantity, 2),
                    unit=unit,
                    reason="predicted demand from past meals",
                )
            )
            seen.add(ingredient)
            if len(candidates) >= limit:
                break

        return candidates

    def get_summary(self) -> dict[str, object]:
        snapshot = self.store.snapshot()
        behaviour = snapshot.behaviour

        top_command = self._top_key(behaviour.command_usage)
        top_cuisine = self._top_key(behaviour.preferred_cuisines)
        top_period = self._top_key(behaviour.active_periods)
        favourite_ingredients = [
            ingredient
            for ingredient, _ in sorted(
                behaviour.favourite_ingredients.items(),
                key=lambda item: item[1],
                reverse=True,
            )[:5]
        ]

        observations: list[str] = []
        if top_command:
            observations.append(f"Most common action is {top_command}.")
        if top_cuisine:
            observations.append(f"Preferred cuisine currently trends toward {top_cuisine}.")
        if top_period:
            observations.append(f"Most activity happens in the {top_period}.")

        return {
            "top_command": top_command,
            "top_cuisine": top_cuisine,
            "top_active_period": top_period,
            "favourite_ingredients": favourite_ingredients,
            "predicted_restock_candidates": self.predict_restock_candidates(),
            "observations": observations,
        }

    @staticmethod
    def _period_bucket(now: datetime) -> str:
        hour = now.hour
        if hour < 11:
            return "morning"
        if hour < 17:
            return "afternoon"
        return "evening"

    @staticmethod
    def _top_key(values: dict[str, int]) -> str | None:
        if not values:
            return None
        return max(values, key=values.get)
