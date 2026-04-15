from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.core.search_models import DEFAULT_SEARCH_MODEL


class InventoryItem(BaseModel):
    name: str
    quantity: float
    unit: str = "unit"
    purchased_at: datetime | None = None
    expires_on: date | None = None
    category: str = "general"
    min_desired_quantity: float = 1.0


class InventoryBatch(BaseModel):
    item_name: str
    quantity: float
    unit: str = "unit"
    purchased_at: datetime | None = None
    expires_on: date | None = None
    category: str = "general"
    min_desired_quantity: float = 1.0
    source: str = "unknown"
    location: str = "fridge"
    confidence: float = 1.0
    active: bool = True


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
    prep_minutes: int = 10
    step_count: int = 3
    effort_score: float = 0.4
    suitable_when_tired: bool = True
    cuisine: str = "global"
    source_url: str | None = None
    source_title: str | None = None


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


class ConversationTurn(BaseModel):
    role: str
    text: str
    timestamp: datetime


class UserConversationMemory(BaseModel):
    user_id: str
    active_session_id: str
    session_started_at: datetime
    last_activity_at: datetime
    carryover_context: str = ""
    current_status: str = ""
    turns: list[ConversationTurn] = Field(default_factory=list)
    completed_session_summaries: list[str] = Field(default_factory=list)


class UserPreferences(BaseModel):
    user_id: str
    mode: str = "lazy"
    meal_window_start: str = "18:00"
    meal_window_end: str = "21:00"
    late_night_window_start: str = "22:30"
    late_night_window_end: str = "00:30"
    max_prep_minutes: int = 10
    notification_frequency: str = "normal"
    dietary_preferences: list[str] = Field(default_factory=list)
    search_model: str = DEFAULT_SEARCH_MODEL


class TemporaryStateOverride(BaseModel):
    id: str
    user_id: str
    state: str
    value: str = "active"
    expires_at: datetime
    source: str = "telegram"
    note: str = ""
    created_at: datetime


class DecisionProfile(BaseModel):
    user_id: str
    ignore_nudge_rate: float = 0.0
    healthy_meal_acceptance_score: float = 0.5
    quick_food_bias: float = 0.5
    eat_at_home_likelihood: float = 0.5
    stress_eating_signal: float = 0.0
    user_threshold: float = 0.6
    updated_at: datetime | None = None


class OverrideIntent(BaseModel):
    kind: str
    target: str
    value: str
    duration_hours: int | None = None
    confidence: float = 1.0
    source_text: str = ""


class AssistantIntervention(BaseModel):
    id: str
    user_id: str
    thread_key: str
    sequence_index: int
    context_hash: str
    decision_type: str
    sent_at: datetime
    status: str = "sent"
    resolved_at: datetime | None = None
    mute_until: datetime | None = None
    message: str
    recommended_action: str = ""
    score: float = 0.0
    confidence: float = 0.0
    reason_codes: list[str] = Field(default_factory=list)
    draft_items: list[GroceryLine] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DecisionResult(BaseModel):
    user_id: str
    intervene: bool
    confidence: float
    intervention_type: str | None = None
    score: float = 0.0
    reason_codes: list[str] = Field(default_factory=list)
    thread_key: str | None = None
    recommended_action: str = ""
    message: str = ""
    draft_items: list[GroceryLine] = Field(default_factory=list)
    recipe_id: str | None = None
    recipe_name: str | None = None
    context_hash: str = ""
    sequence_index: int = 0
    intervention_id: str | None = None
    quick_actions: list[dict[str, str]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SharedContext(BaseModel):
    version: int = 1
    inventory: list[InventoryItem] = Field(default_factory=list)
    inventory_batches: list[InventoryBatch] = Field(default_factory=list)
    recipe_catalog: list[Recipe] = Field(default_factory=list)
    utilities: UtilityLevels = Field(default_factory=UtilityLevels)
    nutrition_profile: NutritionProfile = Field(default_factory=NutritionProfile)
    meal_history: list[MealRecord] = Field(default_factory=list)
    grocery_orders: list[GroceryOrder] = Field(default_factory=list)
    pending_grocery_list: list[GroceryLine] = Field(default_factory=list)
    behaviour: BehaviourProfile = Field(default_factory=BehaviourProfile)
    conversation_memory: dict[str, UserConversationMemory] = Field(default_factory=dict)
    recent_events: list[ContextEvent] = Field(default_factory=list)
