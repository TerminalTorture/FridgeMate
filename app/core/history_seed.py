from __future__ import annotations

import random
from collections import defaultdict
from datetime import date, datetime, time, timedelta

from app.core.sql_models import (
    AppStateRow,
    BehaviourCounterRow,
    InventoryBatchRow,
    InventoryItemRow,
    MealRecordRow,
    NutritionProfileRow,
    RecipeIngredientRow,
    RecipeRow,
    RuntimeEventRow,
    UtilityStateRow,
)
from app.core.sql_repository import SQLRepository
from app.core.time_utils import get_timezone, singapore_now, utc_now
from app.models.domain import InventoryItem, Recipe, SharedContext


class SyntheticHistorySeeder:
    def __init__(self, repository: SQLRepository) -> None:
        self.repository = repository

    def seed(
        self,
        *,
        days: int,
        seed: int,
        initial_state: SharedContext,
    ) -> None:
        rng = random.Random(seed)
        self.repository.reset_all()
        specs = self._build_item_specs(initial_state.inventory, initial_state.recipe_catalog)
        behaviour_counters: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        recent_events: list[RuntimeEventRow] = []
        singapore = get_timezone("Asia/Singapore")
        utc = get_timezone("UTC")
        today = singapore_now().date()
        start_day = today - timedelta(days=max(days - 1, 0))

        with self.repository.session() as session:
            session.add(AppStateRow(id=1, version=max(days, 1)))
            session.add(
                UtilityStateRow(
                    id=1,
                    water_level_percent=58,
                    ice_level_percent=42,
                )
            )
            session.add(
                NutritionProfileRow(
                    id=1,
                    daily_calorie_target=initial_state.nutrition_profile.daily_calorie_target,
                    daily_protein_target_g=initial_state.nutrition_profile.daily_protein_target_g,
                    hydration_target_l=initial_state.nutrition_profile.hydration_target_l,
                    dietary_preferences=list(initial_state.nutrition_profile.dietary_preferences),
                )
            )
            item_rows = self._insert_item_rows(session, specs)
            self._insert_recipes(session, initial_state.recipe_catalog)

            order_history: list[dict[str, object]] = []
            for offset in range(days):
                current_day = start_day + timedelta(days=offset)
                self._expire_old_batches(session, current_day)
                self._run_purchase_pattern(session, current_day, rng, item_rows, specs, recent_events)
                self._record_daily_commands(behaviour_counters, current_day, rng)
                self._maybe_cook_meals(
                    session,
                    current_day,
                    rng,
                    initial_state.recipe_catalog,
                    behaviour_counters,
                    recent_events,
                )
                self._maybe_order_essentials(
                    session,
                    current_day,
                    rng,
                    item_rows,
                    specs,
                    order_history,
                    recent_events,
                )

            self._top_up_current_inventory(session, today, item_rows, specs, recent_events)

            for category, values in behaviour_counters.items():
                for key, value in values.items():
                    session.add(BehaviourCounterRow(category=category, key=key, value=value))

            for event in recent_events[-50:]:
                session.add(event)

    @staticmethod
    def _build_item_specs(inventory: list[InventoryItem], recipes: list[Recipe]) -> dict[str, dict[str, object]]:
        specs: dict[str, dict[str, object]] = {}
        for item in inventory:
            specs[item.name.lower()] = {
                "name": item.name,
                "category": item.category,
                "unit": item.unit,
                "min_desired_quantity": item.min_desired_quantity,
                "shelf_life_days": SyntheticHistorySeeder._default_shelf_life(item.name),
                "essential": item.name.lower() in {"milk", "eggs", "rice", "pasta"},
            }
        for recipe in recipes:
            for ingredient in recipe.ingredients:
                key = ingredient.name.lower()
                if key not in specs:
                    specs[key] = {
                        "name": ingredient.name,
                        "category": "general",
                        "unit": ingredient.unit,
                        "min_desired_quantity": max(ingredient.quantity, 1.0),
                        "shelf_life_days": SyntheticHistorySeeder._default_shelf_life(ingredient.name),
                        "essential": key in {"milk", "eggs", "rice", "pasta"},
                    }
        return specs

    def _insert_item_rows(self, session, specs: dict[str, dict[str, object]]) -> dict[str, InventoryItemRow]:
        rows: dict[str, InventoryItemRow] = {}
        now = utc_now()
        for key, spec in specs.items():
            row = InventoryItemRow(
                name=str(spec["name"]),
                category=str(spec["category"]),
                unit=str(spec["unit"]),
                min_desired_quantity=float(spec["min_desired_quantity"]),
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.flush()
            rows[key] = row
        return rows

    @staticmethod
    def _insert_recipes(session, recipes: list[Recipe]) -> None:
        for recipe in recipes:
            row = RecipeRow(
                id=recipe.id,
                name=recipe.name,
                description=recipe.description,
                instructions=list(recipe.instructions),
                tags=list(recipe.tags),
                calories=recipe.calories,
                protein_g=recipe.protein_g,
                cuisine=recipe.cuisine,
                source_url=recipe.source_url,
                source_title=recipe.source_title,
            )
            session.add(row)
            session.flush()
            for ingredient in recipe.ingredients:
                session.add(
                    RecipeIngredientRow(
                        recipe_id=recipe.id,
                        name=ingredient.name,
                        quantity=ingredient.quantity,
                        unit=ingredient.unit,
                        optional=ingredient.optional,
                    )
                )

    def _run_purchase_pattern(self, session, current_day: date, rng: random.Random, item_rows, specs, recent_events) -> None:
        singapore = get_timezone("Asia/Singapore")
        utc = get_timezone("UTC")
        weekly_patterns = []
        if current_day.weekday() in {0, 4}:
            weekly_patterns.extend(
                [
                    ("milk", round(rng.uniform(1.2, 2.0), 1)),
                    ("eggs", 12),
                    ("yogurt", 4),
                    ("bananas", 6),
                ]
            )
        if current_day.weekday() in {1, 5}:
            weekly_patterns.extend(
                [
                    ("spinach", 2),
                    ("tomatoes", 4),
                    ("onion", 3),
                    ("lettuce", 1),
                    ("chicken breast", 2),
                ]
            )
        if current_day.weekday() == 2:
            weekly_patterns.extend(
                [
                    ("broccoli", 1),
                    ("cucumber", 1),
                ]
            )
        if current_day.day in {1, 15}:
            weekly_patterns.extend(
                [
                    ("rice", 8),
                    ("pasta", 2),
                    ("olive oil", 1),
                    ("soy sauce", 1),
                ]
            )

        timestamp = datetime.combine(current_day, time(hour=18, minute=0), tzinfo=singapore).astimezone(utc)
        for item_name, quantity in weekly_patterns:
            spec = specs.get(item_name)
            if spec is None:
                continue
            self._add_batch(
                session,
                item_rows[item_name],
                quantity=float(quantity),
                purchased_at=timestamp,
                expires_on=(current_day + timedelta(days=int(spec["shelf_life_days"]))) if int(spec["shelf_life_days"]) > 0 else None,
                source="synthetic_purchase",
            )
            recent_events.append(
                RuntimeEventRow(
                    timestamp=timestamp,
                    agent="seed",
                    action="purchase",
                    summary=f"Purchased {quantity:g} {spec['unit']} of {spec['name']}.",
                    changes={"item": spec["name"], "quantity": quantity},
                )
            )

    @staticmethod
    def _record_daily_commands(counters, current_day: date, rng: random.Random) -> None:
        period = "evening" if current_day.weekday() < 5 else "afternoon"
        counters["command_usage"]["inventory_check"] += 1
        counters["active_periods"][period] += 1
        if rng.random() < 0.7:
            counters["command_usage"]["recipe_query"] += 1

    def _maybe_cook_meals(self, session, current_day: date, rng: random.Random, recipes, counters, recent_events) -> None:
        singapore = get_timezone("Asia/Singapore")
        utc = get_timezone("UTC")
        shuffled = list(recipes)
        rng.shuffle(shuffled)
        meals_today = 1 if rng.random() < 0.8 else 0
        if current_day.weekday() in {5, 6} and rng.random() < 0.4:
            meals_today += 1

        for index in range(meals_today):
            candidate = self._best_available_recipe(session, shuffled)
            if candidate is None:
                break
            self._consume_recipe(session, candidate)
            cooked_at = datetime.combine(
                current_day,
                time(hour=19 + index, minute=0),
                tzinfo=singapore,
            ).astimezone(utc)
            session.add(
                MealRecordRow(
                    recipe_id=candidate.id,
                    recipe_name=candidate.name,
                    cooked_at=cooked_at,
                    calories=candidate.calories,
                    protein_g=candidate.protein_g,
                    tags=list(candidate.tags),
                    cuisine=candidate.cuisine,
                )
            )
            counters["cooked_recipes"][candidate.id] += 1
            counters["preferred_cuisines"][candidate.cuisine] += 1
            counters["active_periods"]["evening"] += 1
            for ingredient in candidate.ingredients:
                counters["favourite_ingredients"][ingredient.name.lower()] += 1
            recent_events.append(
                RuntimeEventRow(
                    timestamp=cooked_at,
                    agent="seed",
                    action="cook_meal",
                    summary=f"Cooked {candidate.name}.",
                    changes={"recipe_id": candidate.id},
                )
            )

    def _maybe_order_essentials(self, session, current_day: date, rng: random.Random, item_rows, specs, order_history, recent_events) -> None:
        singapore = get_timezone("Asia/Singapore")
        utc = get_timezone("UTC")
        essentials_to_order: list[tuple[str, float]] = []
        for item_name in ("milk", "eggs"):
            available = self._available_quantity(session, item_name)
            min_quantity = float(specs[item_name]["min_desired_quantity"])
            if available < min_quantity or (current_day.weekday() == 6 and rng.random() < 0.45):
                essentials_to_order.append((item_name, max(min_quantity * 2 - available, 1.0)))

        if not essentials_to_order:
            return

        order_id = f"SEED-{current_day.strftime('%Y%m%d')}-{len(order_history)+1}"
        created_at = datetime.combine(current_day, time(hour=17, minute=30), tzinfo=singapore).astimezone(utc)
        order_history.append({"id": order_id, "items": essentials_to_order})
        from app.core.sql_models import GroceryOrderLineRow, GroceryOrderRow

        order_row = GroceryOrderRow(
            id=order_id,
            created_at=created_at,
            status="confirmed",
            source="synthetic_restock",
            vendor="PantryNow Mock",
            eta_minutes=35,
        )
        session.add(order_row)
        session.flush()
        for item_name, quantity in essentials_to_order:
            spec = specs[item_name]
            session.add(
                GroceryOrderLineRow(
                    order_id=order_id,
                    name=spec["name"],
                    quantity=quantity,
                    unit=spec["unit"],
                    reason="synthetic low-stock restock",
                )
            )
            self._add_batch(
                session,
                item_rows[item_name],
                quantity=quantity,
                purchased_at=created_at + timedelta(hours=1),
                expires_on=(current_day + timedelta(days=int(spec["shelf_life_days"]))) if int(spec["shelf_life_days"]) > 0 else None,
                source="synthetic_order",
            )
        recent_events.append(
            RuntimeEventRow(
                timestamp=created_at,
                agent="seed",
                action="order_essentials",
                summary=f"Ordered {', '.join(item for item, _ in essentials_to_order)}.",
                changes={"order_id": order_id},
            )
        )

    def _top_up_current_inventory(self, session, current_day: date, item_rows, specs, recent_events) -> None:
        singapore = get_timezone("Asia/Singapore")
        utc = get_timezone("UTC")
        timestamp = datetime.combine(current_day, time(hour=16, minute=30), tzinfo=singapore).astimezone(utc)
        topups = [
            ("milk", 1.5),
            ("eggs", 10),
            ("spinach", 1),
            ("tomatoes", 3),
            ("yogurt", 2),
            ("chicken breast", 1),
            ("rice", 4),
        ]
        for item_name, quantity in topups:
            if self._available_quantity(session, item_name) > 0:
                continue
            spec = specs[item_name]
            self._add_batch(
                session,
                item_rows[item_name],
                quantity=quantity,
                purchased_at=timestamp,
                expires_on=(current_day + timedelta(days=int(spec["shelf_life_days"]))) if int(spec["shelf_life_days"]) > 0 else None,
                source="synthetic_topup",
            )
            recent_events.append(
                RuntimeEventRow(
                    timestamp=timestamp,
                    agent="seed",
                    action="top_up_inventory",
                    summary=f"Topped up {spec['name']} for current fridge state.",
                    changes={"item": spec["name"], "quantity": quantity},
                )
            )

    def _best_available_recipe(self, session, recipes: list[Recipe]) -> Recipe | None:
        best_recipe: Recipe | None = None
        best_score = -1.0
        for recipe in recipes:
            essentials = [ingredient for ingredient in recipe.ingredients if not ingredient.optional]
            if not essentials:
                continue
            matched = 0
            for ingredient in essentials:
                if self._available_quantity(session, ingredient.name.lower()) >= ingredient.quantity:
                    matched += 1
            score = matched / len(essentials)
            if score == 1.0:
                return recipe
            if score > best_score:
                best_score = score
                best_recipe = recipe
        return best_recipe if best_score >= 0.75 else None

    def _consume_recipe(self, session, recipe: Recipe) -> None:
        for ingredient in recipe.ingredients:
            if ingredient.optional:
                continue
            self._consume_quantity(session, ingredient.name.lower(), ingredient.quantity)

    @staticmethod
    def _default_shelf_life(name: str) -> int:
        mapping = {
            "milk": 7,
            "eggs": 21,
            "spinach": 4,
            "lettuce": 5,
            "tomatoes": 7,
            "onion": 21,
            "bananas": 5,
            "chicken breast": 3,
            "yogurt": 10,
            "broccoli": 6,
            "cucumber": 7,
            "rice": 0,
            "pasta": 0,
            "olive oil": 0,
            "soy sauce": 0,
        }
        return mapping.get(name.lower(), 7)

    @staticmethod
    def _expire_old_batches(session, current_day: date) -> None:
        expired_batches = session.query(InventoryBatchRow).filter(
            InventoryBatchRow.active.is_(True),
            InventoryBatchRow.expires_on.is_not(None),
            InventoryBatchRow.expires_on < current_day,
        ).all()
        for batch in expired_batches:
            batch.active = False
            batch.updated_at = utc_now()

    @staticmethod
    def _add_batch(session, item_row: InventoryItemRow, *, quantity: float, purchased_at: datetime, expires_on: date | None, source: str) -> None:
        session.add(
            InventoryBatchRow(
                item_id=item_row.id,
                quantity=round(quantity, 2),
                purchased_at=purchased_at,
                expires_on=expires_on,
                added_by="seed",
                source=source,
                confidence=1.0,
                location="fridge",
                active=True,
                created_at=purchased_at,
                updated_at=purchased_at,
            )
        )

    @staticmethod
    def _available_quantity(session, item_name: str) -> float:
        rows = session.query(InventoryBatchRow).join(InventoryItemRow).filter(
            InventoryItemRow.name.ilike(item_name),
            InventoryBatchRow.active.is_(True),
            InventoryBatchRow.quantity > 0,
        ).all()
        return round(sum(row.quantity for row in rows), 2)

    @staticmethod
    def _consume_quantity(session, item_name: str, quantity: float) -> None:
        remaining = quantity
        rows = session.query(InventoryBatchRow).join(InventoryItemRow).filter(
            InventoryItemRow.name.ilike(item_name),
            InventoryBatchRow.active.is_(True),
            InventoryBatchRow.quantity > 0,
        ).order_by(
            InventoryBatchRow.expires_on.is_(None),
            InventoryBatchRow.expires_on.asc(),
            InventoryBatchRow.purchased_at.asc(),
            InventoryBatchRow.id.asc(),
        ).all()
        for row in rows:
            if remaining <= 0:
                break
            if row.quantity <= remaining + 1e-6:
                remaining -= row.quantity
                row.quantity = 0.0
                row.active = False
            else:
                row.quantity = round(row.quantity - remaining, 2)
                remaining = 0.0
            row.updated_at = utc_now()
