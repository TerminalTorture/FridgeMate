from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


class InventoryItem(BaseModel):
    name: str
    quantity: float
    unit: str = "unit"
    expires_on: date | None = None
    category: str = "general"
    min_desired_quantity: float = 1.0


class RecipeIngredient(BaseModel):
    name: str
    quantity: float
    unit: str = "unit"
    optional: bool = False


class Recipe(BaseModel):
    id: str
    name: str
    description: str
    ingredients: list[RecipeIngredient]
    instructions: list[str]
    tags: list[str] = Field(default_factory=list)
    calories: int
    protein_g: int
    cuisine: str = "global"


class GroceryLine(BaseModel):
    name: str
    quantity: float
    unit: str = "unit"
    reason: str


class GroceryOrder(BaseModel):
    id: str
    items: list[GroceryLine]
    created_at: datetime
    status: str
    source: str
    vendor: str
    eta_minutes: int


class UtilityLevels(BaseModel):
    water_level_percent: int = 100
    ice_level_percent: int = 100


class NutritionProfile(BaseModel):
    daily_calorie_target: int = 2200
    daily_protein_target_g: int = 110
    hydration_target_l: float = 2.5
    dietary_preferences: list[str] = Field(default_factory=list)


class MealRecord(BaseModel):
    recipe_id: str
    recipe_name: str
    cooked_at: datetime
    calories: int
    protein_g: int
    tags: list[str] = Field(default_factory=list)
    cuisine: str = "global"


class BehaviourProfile(BaseModel):
    command_usage: dict[str, int] = Field(default_factory=dict)
    recipe_requests: dict[str, int] = Field(default_factory=dict)
    cooked_recipes: dict[str, int] = Field(default_factory=dict)
    favourite_ingredients: dict[str, int] = Field(default_factory=dict)
    active_periods: dict[str, int] = Field(default_factory=dict)
    preferred_cuisines: dict[str, int] = Field(default_factory=dict)
    disliked_ingredients: list[str] = Field(default_factory=list)


class ContextEvent(BaseModel):
    timestamp: datetime
    agent: str
    action: str
    summary: str
    changes: dict[str, Any] = Field(default_factory=dict)


class SharedContext(BaseModel):
    version: int = 1
    inventory: list[InventoryItem] = Field(default_factory=list)
    recipe_catalog: list[Recipe] = Field(default_factory=list)
    utilities: UtilityLevels = Field(default_factory=UtilityLevels)
    nutrition_profile: NutritionProfile = Field(default_factory=NutritionProfile)
    meal_history: list[MealRecord] = Field(default_factory=list)
    grocery_orders: list[GroceryOrder] = Field(default_factory=list)
    pending_grocery_list: list[GroceryLine] = Field(default_factory=list)
    behaviour: BehaviourProfile = Field(default_factory=BehaviourProfile)
    recent_events: list[ContextEvent] = Field(default_factory=list)

