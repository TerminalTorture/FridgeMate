from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from app.models.domain import GroceryLine, Recipe, RecipeIngredient


class InventoryItemInput(BaseModel):
    name: str
    quantity: float = Field(gt=0)
    unit: str = "unit"
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
    tags: list[str] = Field(default_factory=list)
    rationale: str
