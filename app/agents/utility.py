from __future__ import annotations

from app.core.context_store import ContextStore
from app.models.domain import Recipe


class UtilityAgent:
    def __init__(self, store: ContextStore) -> None:
        self.store = store

    def get_status(self) -> dict[str, object]:
        snapshot = self.store.snapshot()
        return {
            "utilities": snapshot.utilities,
            "alerts": self._alerts(snapshot.utilities.water_level_percent, snapshot.utilities.ice_level_percent),
        }

    def update_levels(
        self,
        water_level_percent: int | None = None,
        ice_level_percent: int | None = None,
    ) -> dict[str, object]:
        if water_level_percent is None and ice_level_percent is None:
            raise ValueError("At least one utility level must be provided.")

        def mutator(state):
            if water_level_percent is not None:
                state.utilities.water_level_percent = water_level_percent
            if ice_level_percent is not None:
                state.utilities.ice_level_percent = ice_level_percent
            return {
                "water_level_percent": state.utilities.water_level_percent,
                "ice_level_percent": state.utilities.ice_level_percent,
            }

        updated_state = self.store.update(
            agent="utility_agent",
            action="update_levels",
            summary="Updated fridge utility levels.",
            mutator=mutator,
        )
        return {
            "utilities": updated_state.utilities,
            "alerts": self._alerts(
                updated_state.utilities.water_level_percent,
                updated_state.utilities.ice_level_percent,
            ),
        }

    def register_kitchen_activity(self, recipe: Recipe) -> dict[str, object]:
        water_drop = 6 if "refreshing" in recipe.tags else 4
        ice_drop = 8 if "refreshing" in recipe.tags else 2

        def mutator(state):
            state.utilities.water_level_percent = max(
                state.utilities.water_level_percent - water_drop,
                0,
            )
            state.utilities.ice_level_percent = max(
                state.utilities.ice_level_percent - ice_drop,
                0,
            )
            return {
                "recipe_id": recipe.id,
                "water_level_percent": state.utilities.water_level_percent,
                "ice_level_percent": state.utilities.ice_level_percent,
            }

        updated_state = self.store.update(
            agent="utility_agent",
            action="register_kitchen_activity",
            summary=f"Adjusted water and ice levels after preparing {recipe.name}.",
            mutator=mutator,
        )
        return {
            "utilities": updated_state.utilities,
            "alerts": self._alerts(
                updated_state.utilities.water_level_percent,
                updated_state.utilities.ice_level_percent,
            ),
        }

    @staticmethod
    def _alerts(water_level_percent: int, ice_level_percent: int) -> list[str]:
        alerts: list[str] = []
        if water_level_percent <= 35:
            alerts.append("Water reservoir is low.")
        if ice_level_percent <= 35:
            alerts.append("Ice level is low.")
        return alerts
