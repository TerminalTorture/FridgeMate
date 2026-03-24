from __future__ import annotations

from datetime import datetime, timedelta

from app.core.context_store import ContextStore
from app.models.domain import MealRecord, Recipe


class NutritionAgent:
    def __init__(self, store: ContextStore) -> None:
        self.store = store

    def record_meal(self, recipe: Recipe) -> dict[str, object]:
        meal = MealRecord(
            recipe_id=recipe.id,
            recipe_name=recipe.name,
            cooked_at=datetime.utcnow(),
            calories=recipe.calories,
            protein_g=recipe.protein_g,
            tags=recipe.tags,
            cuisine=recipe.cuisine,
        )

        def mutator(state):
            state.meal_history.insert(0, meal)
            return {"recipe_id": recipe.id, "meal_count": len(state.meal_history)}

        self.store.update(
            agent="nutrition_agent",
            action="record_meal",
            summary=f"Logged meal {recipe.name}.",
            mutator=mutator,
        )
        return self.get_summary()

    def get_summary(self) -> dict[str, object]:
        snapshot = self.store.snapshot()
        window_start = datetime.utcnow() - timedelta(days=7)
        recent_meals = [
            meal for meal in snapshot.meal_history if meal.cooked_at >= window_start
        ]

        meal_count = len(recent_meals)
        average_calories = (
            round(sum(meal.calories for meal in recent_meals) / meal_count, 1)
            if meal_count
            else 0.0
        )
        average_protein = (
            round(sum(meal.protein_g for meal in recent_meals) / meal_count, 1)
            if meal_count
            else 0.0
        )

        vegetable_meals = sum(
            1 for meal in recent_meals if "vegetable-forward" in meal.tags
        )
        protein_target_per_meal = snapshot.nutrition_profile.daily_protein_target_g / 3

        recommendations: list[str] = []
        if meal_count == 0:
            recommendations.append("No meals logged yet. Start by cooking a recipe to build a diet baseline.")
        if average_protein and average_protein < protein_target_per_meal:
            recommendations.append("Protein intake trends low. Prioritize chicken, eggs, yogurt, or beans.")
        if meal_count >= 3 and vegetable_meals < max(1, meal_count // 2):
            recommendations.append("Add more vegetable-forward meals to improve balance across the week.")
        if snapshot.utilities.water_level_percent < 40:
            recommendations.append("Water reservoir is low. Refill it to support hydration routines.")

        return {
            "meals_last_7_days": meal_count,
            "average_calories_per_meal": average_calories,
            "average_protein_g_per_meal": average_protein,
            "daily_targets": snapshot.nutrition_profile,
            "recommendations": recommendations,
        }
