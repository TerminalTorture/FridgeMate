from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from app.models.domain import GroceryLine


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
