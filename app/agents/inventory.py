from __future__ import annotations

from datetime import date, timedelta

from app.core.context_store import ContextStore
from app.models.domain import GroceryLine, InventoryItem, Recipe


class InventoryAgent:
    def __init__(self, store: ContextStore) -> None:
        self.store = store

    def get_inventory(self) -> list[InventoryItem]:
        snapshot = self.store.snapshot()
        return sorted(snapshot.inventory, key=lambda item: item.name.lower())

    def add_or_refresh_item(self, item: InventoryItem) -> InventoryItem:
        normalized_name = item.name.lower()

        def mutator(state):
            for existing in state.inventory:
                if existing.name.lower() == normalized_name:
                    existing.quantity = round(existing.quantity + item.quantity, 2)
                    existing.unit = item.unit
                    existing.category = item.category
                    existing.min_desired_quantity = item.min_desired_quantity
                    if item.expires_on is not None:
                        existing.expires_on = item.expires_on
                    return {
                        "item": existing.name,
                        "quantity": existing.quantity,
                        "mode": "incremented",
                    }

            state.inventory.append(item)
            return {"item": item.name, "quantity": item.quantity, "mode": "created"}

        self.store.update(
            agent="inventory_agent",
            action="add_or_refresh_item",
            summary=f"Updated inventory for {item.name}.",
            mutator=mutator,
        )

        return self._get_item(normalized_name)

    def expiring_soon(self, days: int = 3) -> list[InventoryItem]:
        threshold = date.today() + timedelta(days=days)
        return [
            item
            for item in self.get_inventory()
            if item.expires_on is not None and item.expires_on <= threshold
        ]

    def low_stock_items(self) -> list[InventoryItem]:
        return [
            item
            for item in self.get_inventory()
            if item.quantity < item.min_desired_quantity
        ]

    def consume_for_recipe(self, recipe: Recipe) -> dict[str, object]:
        snapshot = self.store.snapshot()
        inventory_map = {item.name.lower(): item for item in snapshot.inventory}
        missing_items: list[GroceryLine] = []

        for ingredient in recipe.ingredients:
            item = inventory_map.get(ingredient.name.lower())
            available = 0.0 if item is None else item.quantity
            if ingredient.optional:
                continue
            if available < ingredient.quantity:
                missing_items.append(
                    GroceryLine(
                        name=ingredient.name,
                        quantity=round(max(ingredient.quantity - available, 0.0), 2),
                        unit=ingredient.unit,
                        reason=f"required for {recipe.name}",
                    )
                )

        if missing_items:
            return {"success": False, "missing_items": missing_items}

        def mutator(state):
            consumed_items: list[str] = []
            for ingredient in recipe.ingredients:
                if ingredient.optional:
                    continue
                for item in state.inventory:
                    if item.name.lower() == ingredient.name.lower():
                        item.quantity = round(max(item.quantity - ingredient.quantity, 0.0), 2)
                        consumed_items.append(item.name)
                        break

            state.inventory = [item for item in state.inventory if item.quantity > 0]
            return {"recipe_id": recipe.id, "consumed_count": len(consumed_items)}

        updated_state = self.store.update(
            agent="inventory_agent",
            action="consume_recipe_ingredients",
            summary=f"Consumed ingredients for {recipe.name}.",
            mutator=mutator,
        )

        return {
            "success": True,
            "remaining_inventory": sorted(
                updated_state.inventory, key=lambda item: item.name.lower()
            ),
        }

    def _get_item(self, normalized_name: str) -> InventoryItem:
        snapshot = self.store.snapshot()
        for item in snapshot.inventory:
            if item.name.lower() == normalized_name:
                return item
        raise LookupError(f"Inventory item {normalized_name} was not found.")
