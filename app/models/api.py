from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.domain import GroceryLine, Recipe, RecipeIngredient


class InventoryItemInput(BaseModel):
    name: str
    quantity: float = Field(gt=0)
    unit: str = "unit"
    purchased_at: datetime | None = None
    expires_on: date | None = None
    category: str = "general"
    min_desired_quantity: float = Field(default=1.0, ge=0)


class GroceryOrderLineInput(BaseModel):
    name: str
    quantity: float = Field(gt=0)
    unit: str = "unit"
    reason: str = "manual request"


class GroceryOrderRequest(BaseModel):
    recipe_id: str | None = None
    items: list[GroceryOrderLineInput] = Field(default_factory=list)
    source: str = "manual_api"


class UtilityUpdateRequest(BaseModel):
    water_level_percent: int | None = Field(default=None, ge=0, le=100)
    ice_level_percent: int | None = Field(default=None, ge=0, le=100)


class TelegramMessageRequest(BaseModel):
    user_id: str = "demo-user"
    message: str


class TelegramMessageResponse(BaseModel):
    user_id: str
    intent: str
    reply: str
    data: dict[str, Any] = Field(default_factory=dict)


class TelegramWebhookRegistrationRequest(BaseModel):
    url: str | None = None
    drop_pending_updates: bool = True


class TelegramSendTestRequest(BaseModel):
    chat_id: str | None = None
    text: str = "MCP Fridge test message."


class HeartbeatSettingsRequest(BaseModel):
    enabled: bool | None = None
    interval_minutes: int | None = Field(default=None, ge=1, le=1440)
    dinner_time: str | None = None
    chat_id: str | None = None


class SeedHistoryRequest(BaseModel):
    days: int = Field(default=180, ge=1, le=3650)
    seed: int = 4052


class RecipeIngredientInput(BaseModel):
    name: str
    quantity: float
    unit: str = "unit"
    optional: bool = False


class RecipeInput(BaseModel):
    id: str | None = None
    name: str
    description: str
    ingredients: list[RecipeIngredientInput]
    instructions: list[str]
    tags: list[str] = Field(default_factory=list)
    calories: int = 0
    protein_g: int = 0
    prep_minutes: int = Field(default=10, ge=1, le=240)
    step_count: int = Field(default=3, ge=1, le=99)
    effort_score: float = Field(default=0.4, ge=0.0, le=1.0)
    suitable_when_tired: bool = True
    cuisine: str = "global"
    source_url: str | None = None
    source_title: str | None = None

    def to_domain(self) -> Recipe:
        recipe_id = self.id or self.name.strip().lower().replace(" ", "_").replace("-", "_")
        return Recipe(
            id=recipe_id,
            name=self.name,
            description=self.description,
            ingredients=[RecipeIngredient(**ingredient.model_dump()) for ingredient in self.ingredients],
            instructions=self.instructions,
            tags=self.tags,
            calories=self.calories,
            protein_g=self.protein_g,
            prep_minutes=self.prep_minutes,
            step_count=self.step_count,
            effort_score=self.effort_score,
            suitable_when_tired=self.suitable_when_tired,
            cuisine=self.cuisine,
            source_url=self.source_url,
            source_title=self.source_title,
        )


class OnlineRecipeSearchRequest(BaseModel):
    query: str
    max_results: int = Field(default=3, ge=1, le=10)


class RecipeImportRequest(BaseModel):
    recipe: RecipeInput


class MCPToolCallRequest(BaseModel):
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class RecipeSuggestion(BaseModel):
    recipe_id: str
    name: str
    description: str
    can_make_now: bool
    coverage: float
    missing_items: list[GroceryLine] = Field(default_factory=list)
    calories: int
    protein_g: int
    prep_minutes: int
    step_count: int
    effort_score: float
    suitable_when_tired: bool
    tags: list[str] = Field(default_factory=list)
    rationale: str


class UserPreferencesRequest(BaseModel):
    mode: str | None = None
    meal_window_start: str | None = None
    meal_window_end: str | None = None
    late_night_window_start: str | None = None
    late_night_window_end: str | None = None
    max_prep_minutes: int | None = Field(default=None, ge=1, le=240)
    notification_frequency: str | None = None
    dietary_preferences: list[str] | None = None
    search_model: str | None = None


class TemporaryStateRequest(BaseModel):
    state: str
    duration_hours: int | None = Field(default=None, ge=1, le=168)
    value: str = "active"
    note: str = ""


class DecisionFeedbackRequest(BaseModel):
    user_id: str
    intervention_id: str | None = None
    thread_key: str | None = None
    status: str
    detail: str = ""
