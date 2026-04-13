from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class AppStateRow(Base):
    __tablename__ = "app_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    version: Mapped[int] = mapped_column(Integer, default=1)


class InventoryItemRow(Base):
    __tablename__ = "inventory_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    category: Mapped[str] = mapped_column(String(100), default="general")
    unit: Mapped[str] = mapped_column(String(50), default="unit")
    min_desired_quantity: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    batches: Mapped[list["InventoryBatchRow"]] = relationship(
        back_populates="item",
        cascade="all, delete-orphan",
    )


class InventoryBatchRow(Base):
    __tablename__ = "inventory_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("inventory_items.id"))
    quantity: Mapped[float] = mapped_column(Float, default=0.0)
    purchased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    added_by: Mapped[str] = mapped_column(String(100), default="system")
    source: Mapped[str] = mapped_column(String(100), default="manual")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    location: Mapped[str] = mapped_column(String(100), default="fridge")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    item: Mapped[InventoryItemRow] = relationship(back_populates="batches")


class RecipeRow(Base):
    __tablename__ = "recipes"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text)
    instructions: Mapped[list[str]] = mapped_column(JSON, default=list)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    calories: Mapped[int] = mapped_column(Integer, default=0)
    protein_g: Mapped[int] = mapped_column(Integer, default=0)
    prep_minutes: Mapped[int] = mapped_column(Integer, default=10)
    step_count: Mapped[int] = mapped_column(Integer, default=3)
    effort_score: Mapped[float] = mapped_column(Float, default=0.4)
    suitable_when_tired: Mapped[bool] = mapped_column(Boolean, default=True)
    cuisine: Mapped[str] = mapped_column(String(100), default="global")
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_title: Mapped[str | None] = mapped_column(Text, nullable=True)

    ingredients: Mapped[list["RecipeIngredientRow"]] = relationship(
        back_populates="recipe",
        cascade="all, delete-orphan",
    )


class RecipeIngredientRow(Base):
    __tablename__ = "recipe_ingredients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recipe_id: Mapped[str] = mapped_column(ForeignKey("recipes.id"))
    name: Mapped[str] = mapped_column(String(255))
    quantity: Mapped[float] = mapped_column(Float, default=0.0)
    unit: Mapped[str] = mapped_column(String(50), default="unit")
    optional: Mapped[bool] = mapped_column(Boolean, default=False)

    recipe: Mapped[RecipeRow] = relationship(back_populates="ingredients")


class MealRecordRow(Base):
    __tablename__ = "meal_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recipe_id: Mapped[str] = mapped_column(String(255))
    recipe_name: Mapped[str] = mapped_column(String(255))
    cooked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    calories: Mapped[int] = mapped_column(Integer, default=0)
    protein_g: Mapped[int] = mapped_column(Integer, default=0)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    cuisine: Mapped[str] = mapped_column(String(100), default="global")


class GroceryOrderRow(Base):
    __tablename__ = "grocery_orders"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(50), default="confirmed")
    source: Mapped[str] = mapped_column(String(100), default="manual")
    vendor: Mapped[str] = mapped_column(String(255), default="PantryNow Mock")
    eta_minutes: Mapped[int] = mapped_column(Integer, default=45)

    lines: Mapped[list["GroceryOrderLineRow"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
    )


class GroceryOrderLineRow(Base):
    __tablename__ = "grocery_order_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("grocery_orders.id"))
    name: Mapped[str] = mapped_column(String(255))
    quantity: Mapped[float] = mapped_column(Float, default=0.0)
    unit: Mapped[str] = mapped_column(String(50), default="unit")
    reason: Mapped[str] = mapped_column(String(255), default="manual request")

    order: Mapped[GroceryOrderRow] = relationship(back_populates="lines")


class PendingGroceryItemRow(Base):
    __tablename__ = "pending_grocery_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255))
    quantity: Mapped[float] = mapped_column(Float, default=0.0)
    unit: Mapped[str] = mapped_column(String(50), default="unit")
    reason: Mapped[str] = mapped_column(String(255), default="manual request")


class UtilityStateRow(Base):
    __tablename__ = "utility_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    water_level_percent: Mapped[int] = mapped_column(Integer, default=100)
    ice_level_percent: Mapped[int] = mapped_column(Integer, default=100)


class NutritionProfileRow(Base):
    __tablename__ = "nutrition_profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    daily_calorie_target: Mapped[int] = mapped_column(Integer, default=2200)
    daily_protein_target_g: Mapped[int] = mapped_column(Integer, default=110)
    hydration_target_l: Mapped[float] = mapped_column(Float, default=2.5)
    dietary_preferences: Mapped[list[str]] = mapped_column(JSON, default=list)


class BehaviourCounterRow(Base):
    __tablename__ = "behaviour_counters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(String(100), index=True)
    key: Mapped[str] = mapped_column(String(255), index=True)
    value: Mapped[int] = mapped_column(Integer, default=0)


class DislikedIngredientRow(Base):
    __tablename__ = "disliked_ingredients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)


class ConversationSessionRow(Base):
    __tablename__ = "conversation_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    active_session_id: Mapped[str] = mapped_column(String(255))
    session_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    carryover_context: Mapped[str] = mapped_column(Text, default="")
    current_status: Mapped[str] = mapped_column(Text, default="")
    chat_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    turns: Mapped[list["ConversationTurnRow"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ConversationTurnRow.id",
    )
    summaries: Mapped[list["ConversationSummaryRow"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ConversationSummaryRow.id",
    )


class ConversationTurnRow(Base):
    __tablename__ = "conversation_turns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_row_id: Mapped[int] = mapped_column(ForeignKey("conversation_sessions.id"))
    role: Mapped[str] = mapped_column(String(50))
    text: Mapped[str] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    session: Mapped[ConversationSessionRow] = relationship(back_populates="turns")


class ConversationSummaryRow(Base):
    __tablename__ = "conversation_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_row_id: Mapped[int] = mapped_column(ForeignKey("conversation_sessions.id"))
    text: Mapped[str] = mapped_column(Text)

    session: Mapped[ConversationSessionRow] = relationship(back_populates="summaries")


class RuntimeEventRow(Base):
    __tablename__ = "runtime_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    agent: Mapped[str] = mapped_column(String(100), index=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    summary: Mapped[str] = mapped_column(Text)
    changes: Mapped[dict] = mapped_column(JSON, default=dict)


class HeartbeatPreferenceRow(Base):
    __tablename__ = "heartbeat_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    chat_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    interval_minutes: Mapped[int] = mapped_column(Integer, default=60)
    dinner_time: Mapped[str] = mapped_column(String(10), default="19:00")
    timezone: Mapped[str] = mapped_column(String(100), default="Asia/Singapore")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_alert_signature: Mapped[str] = mapped_column(Text, default="")
    quiet_hours_start: Mapped[str | None] = mapped_column(String(10), nullable=True)
    quiet_hours_end: Mapped[str | None] = mapped_column(String(10), nullable=True)


class DiagnosticsSnapshotRow(Base):
    __tablename__ = "diagnostics_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    overall_status: Mapped[str] = mapped_column(String(50), default="healthy")
    issues: Mapped[list[dict]] = mapped_column(JSON, default=list)
    recommended_actions: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class UserPreferenceRow(Base):
    __tablename__ = "user_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    mode: Mapped[str] = mapped_column(String(20), default="lazy")
    meal_window_start: Mapped[str] = mapped_column(String(10), default="18:00")
    meal_window_end: Mapped[str] = mapped_column(String(10), default="21:00")
    late_night_window_start: Mapped[str] = mapped_column(String(10), default="22:30")
    late_night_window_end: Mapped[str] = mapped_column(String(10), default="00:30")
    max_prep_minutes: Mapped[int] = mapped_column(Integer, default=10)
    notification_frequency: Mapped[str] = mapped_column(String(20), default="normal")
    dietary_preferences: Mapped[list[str]] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class TemporaryStateOverrideRow(Base):
    __tablename__ = "temporary_state_overrides"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    state: Mapped[str] = mapped_column(String(50), index=True)
    value: Mapped[str] = mapped_column(String(255), default="active")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    source: Mapped[str] = mapped_column(String(50), default="telegram")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class DecisionProfileRow(Base):
    __tablename__ = "decision_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    ignore_nudge_rate: Mapped[float] = mapped_column(Float, default=0.0)
    healthy_meal_acceptance_score: Mapped[float] = mapped_column(Float, default=0.5)
    quick_food_bias: Mapped[float] = mapped_column(Float, default=0.5)
    eat_at_home_likelihood: Mapped[float] = mapped_column(Float, default=0.5)
    stress_eating_signal: Mapped[float] = mapped_column(Float, default=0.0)
    user_threshold: Mapped[float] = mapped_column(Float, default=0.6)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class AssistantInterventionRow(Base):
    __tablename__ = "assistant_interventions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    thread_key: Mapped[str] = mapped_column(String(255), index=True)
    sequence_index: Mapped[int] = mapped_column(Integer, default=1)
    context_hash: Mapped[str] = mapped_column(String(255), index=True)
    decision_type: Mapped[str] = mapped_column(String(100), index=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(50), default="sent", index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    mute_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    message: Mapped[str] = mapped_column(Text)
    recommended_action: Mapped[str] = mapped_column(String(255), default="")
    score: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    reason_codes: Mapped[list[str]] = mapped_column(JSON, default=list)
    draft_items: Mapped[list[dict]] = mapped_column(JSON, default=list)
    extra_metadata: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
