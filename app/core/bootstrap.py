from __future__ import annotations

from datetime import date, timedelta

from app.core.time_utils import utc_now
from app.models.domain import (
    BehaviourProfile,
    ContextEvent,
    InventoryItem,
    MealRecord,
    NutritionProfile,
    Recipe,
    RecipeIngredient,
    SharedContext,
    UtilityLevels,
)


def build_initial_context() -> SharedContext:
    today = date.today()

    return SharedContext(
        version=1,
        inventory=[
            InventoryItem(
                name="eggs",
                quantity=5,
                unit="pcs",
                expires_on=today + timedelta(days=5),
                category="protein",
                min_desired_quantity=6,
            ),
            InventoryItem(
                name="spinach",
                quantity=1,
                unit="bunch",
                expires_on=today + timedelta(days=2),
                category="vegetable",
                min_desired_quantity=1,
            ),
            InventoryItem(
                name="milk",
                quantity=0.8,
                unit="litre",
                expires_on=today + timedelta(days=4),
                category="dairy",
                min_desired_quantity=1.0,
            ),
            InventoryItem(
                name="rice",
                quantity=3,
                unit="cups",
                expires_on=None,
                category="grain",
                min_desired_quantity=2,
            ),
            InventoryItem(
                name="chicken breast",
                quantity=1,
                unit="pcs",
                expires_on=today + timedelta(days=2),
                category="protein",
                min_desired_quantity=2,
            ),
            InventoryItem(
                name="yogurt",
                quantity=2,
                unit="cups",
                expires_on=today + timedelta(days=6),
                category="dairy",
                min_desired_quantity=1,
            ),
            InventoryItem(
                name="bananas",
                quantity=4,
                unit="pcs",
                expires_on=today + timedelta(days=3),
                category="fruit",
                min_desired_quantity=3,
            ),
            InventoryItem(
                name="tomatoes",
                quantity=3,
                unit="pcs",
                expires_on=today + timedelta(days=4),
                category="vegetable",
                min_desired_quantity=2,
            ),
            InventoryItem(
                name="onion",
                quantity=2,
                unit="pcs",
                expires_on=today + timedelta(days=10),
                category="vegetable",
                min_desired_quantity=2,
            ),
            InventoryItem(
                name="pasta",
                quantity=1,
                unit="pack",
                expires_on=None,
                category="grain",
                min_desired_quantity=1,
            ),
            InventoryItem(
                name="lettuce",
                quantity=1,
                unit="head",
                expires_on=today + timedelta(days=2),
                category="vegetable",
                min_desired_quantity=1,
            ),
        ],
        recipe_catalog=_seed_recipes(),
        utilities=UtilityLevels(water_level_percent=58, ice_level_percent=32),
        nutrition_profile=NutritionProfile(
            daily_calorie_target=2200,
            daily_protein_target_g=110,
            hydration_target_l=2.5,
            dietary_preferences=["balanced", "high-protein"],
        ),
        meal_history=[
            MealRecord(
                recipe_id="veggie_omelette",
                recipe_name="Veggie Omelette",
                cooked_at=utc_now() - timedelta(days=1),
                calories=420,
                protein_g=27,
                tags=["quick", "high-protein", "vegetable-forward"],
                cuisine="comfort",
            ),
            MealRecord(
                recipe_id="banana_yogurt_smoothie",
                recipe_name="Banana Yogurt Smoothie",
                cooked_at=utc_now() - timedelta(days=2),
                calories=280,
                protein_g=14,
                tags=["quick", "refreshing"],
                cuisine="breakfast",
            ),
        ],
        behaviour=BehaviourProfile(
            command_usage={"inventory_check": 1, "recipe_query": 2},
            recipe_requests={"veggie_omelette": 1, "tomato_pasta": 1},
            cooked_recipes={"veggie_omelette": 1, "banana_yogurt_smoothie": 1},
            favourite_ingredients={
                "eggs": 1,
                "spinach": 1,
                "bananas": 1,
                "yogurt": 1,
                "milk": 1,
            },
            active_periods={"morning": 2, "afternoon": 1, "evening": 1},
            preferred_cuisines={"comfort": 1, "breakfast": 1},
            disliked_ingredients=[],
        ),
        recent_events=[
            ContextEvent(
                timestamp=utc_now(),
                agent="bootstrap",
                action="seed_context",
                summary="Loaded initial fridge inventory, recipes, and user profile.",
                changes={"inventory_items": 11, "recipes": 5},
            )
        ],
    )


def _seed_recipes() -> list[Recipe]:
    return [
        Recipe(
            id="banana_yogurt_smoothie",
            name="Banana Yogurt Smoothie",
            description="A quick breakfast smoothie that uses ripe bananas and yogurt.",
            ingredients=[
                RecipeIngredient(name="bananas", quantity=2, unit="pcs"),
                RecipeIngredient(name="yogurt", quantity=1, unit="cups"),
                RecipeIngredient(name="milk", quantity=0.3, unit="litre"),
            ],
            instructions=[
                "Blend bananas, yogurt, and milk until smooth.",
                "Serve immediately.",
            ],
            tags=["quick", "refreshing"],
            calories=280,
            protein_g=14,
            cuisine="breakfast",
        ),
        Recipe(
            id="chicken_rice_bowl",
            name="Chicken Rice Bowl",
            description="A protein-focused rice bowl with simple vegetables.",
            ingredients=[
                RecipeIngredient(name="chicken breast", quantity=1, unit="pcs"),
                RecipeIngredient(name="rice", quantity=1, unit="cups"),
                RecipeIngredient(name="broccoli", quantity=1, unit="head"),
                RecipeIngredient(name="soy sauce", quantity=0.1, unit="bottle"),
            ],
            instructions=[
                "Cook the rice and pan-sear the chicken.",
                "Steam the broccoli and finish with soy sauce.",
            ],
            tags=["high-protein"],
            calories=610,
            protein_g=42,
            cuisine="asian",
        ),
        Recipe(
            id="garden_salad",
            name="Garden Salad",
            description="A light salad designed to use the most perishable produce first.",
            ingredients=[
                RecipeIngredient(name="lettuce", quantity=1, unit="head"),
                RecipeIngredient(name="tomatoes", quantity=1, unit="pcs"),
                RecipeIngredient(name="cucumber", quantity=1, unit="pcs"),
                RecipeIngredient(name="yogurt", quantity=0.5, unit="cups", optional=True),
            ],
            instructions=[
                "Chop the vegetables.",
                "Toss together and add yogurt dressing if available.",
            ],
            tags=["vegetable-forward", "light"],
            calories=220,
            protein_g=8,
            cuisine="mediterranean",
        ),
        Recipe(
            id="tomato_pasta",
            name="Tomato Pasta",
            description="Simple pasta with tomatoes and onion.",
            ingredients=[
                RecipeIngredient(name="pasta", quantity=1, unit="pack"),
                RecipeIngredient(name="tomatoes", quantity=2, unit="pcs"),
                RecipeIngredient(name="onion", quantity=1, unit="pcs"),
                RecipeIngredient(name="olive oil", quantity=0.1, unit="bottle"),
            ],
            instructions=[
                "Cook the pasta.",
                "Saute onion and tomato in olive oil.",
                "Combine and serve.",
            ],
            tags=["comfort"],
            calories=540,
            protein_g=16,
            cuisine="italian",
        ),
        Recipe(
            id="veggie_omelette",
            name="Veggie Omelette",
            description="Fast, protein-rich omelette that helps use spinach before it expires.",
            ingredients=[
                RecipeIngredient(name="eggs", quantity=2, unit="pcs"),
                RecipeIngredient(name="spinach", quantity=1, unit="bunch"),
                RecipeIngredient(name="milk", quantity=0.2, unit="litre"),
                RecipeIngredient(name="onion", quantity=0.5, unit="pcs", optional=True),
            ],
            instructions=[
                "Whisk eggs and milk together.",
                "Cook spinach and onion briefly, then add egg mixture.",
                "Fold and serve.",
            ],
            tags=["quick", "high-protein", "vegetable-forward"],
            calories=420,
            protein_g=27,
            cuisine="comfort",
        ),
    ]
