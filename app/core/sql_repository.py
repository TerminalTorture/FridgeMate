from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, joinedload, sessionmaker

from app.core.search_models import DEFAULT_SEARCH_MODEL, is_valid_search_model
from app.core.sql_models import (
    AppStateRow,
    AssistantInterventionRow,
    Base,
    BehaviourCounterRow,
    ConversationSessionRow,
    ConversationSummaryRow,
    ConversationTurnRow,
    DecisionProfileRow,
    DiagnosticsSnapshotRow,
    DislikedIngredientRow,
    GroceryOrderLineRow,
    GroceryOrderRow,
    HeartbeatPreferenceRow,
    InventoryBatchRow,
    InventoryItemRow,
    MealRecordRow,
    NutritionProfileRow,
    PendingGroceryItemRow,
    RecipeIngredientRow,
    RecipeRow,
    RuntimeEventRow,
    TemporaryStateOverrideRow,
    UserPreferenceRow,
    UtilityStateRow,
)
from app.core.time_utils import ensure_utc, normalize_shared_context_datetimes, utc_now
from app.models.domain import (
    AssistantIntervention,
    BehaviourProfile,
    ContextEvent,
    ConversationTurn,
    DecisionProfile,
    GroceryLine,
    GroceryOrder,
    InventoryBatch,
    InventoryItem,
    MealRecord,
    NutritionProfile,
    OverrideIntent,
    Recipe,
    RecipeIngredient,
    SharedContext,
    TemporaryStateOverride,
    UserPreferences,
    UserConversationMemory,
    UtilityLevels,
)


class SQLRepository:
    def __init__(self, *, database_url: str, echo: bool = False) -> None:
        self.database_url = database_url
        if database_url.startswith("sqlite:///"):
            db_path = Path(database_url.replace("sqlite:///", "", 1))
            db_path.parent.mkdir(parents=True, exist_ok=True)
        self._upgrade_schema()
        self.engine = create_engine(database_url, echo=echo, future=True)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(self.engine)

    def _upgrade_schema(self) -> None:
        config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
        previous_database_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = self.database_url
        try:
            command.upgrade(config, "head")
        finally:
            if previous_database_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = previous_database_url

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def is_empty(self) -> bool:
        with self.session() as session:
            return session.scalar(select(AppStateRow.id)) is None

    def load_snapshot(self) -> SharedContext:
        with self.session() as session:
            state_row = session.get(AppStateRow, 1)
            version = state_row.version if state_row else 1

            inventory_items: list[InventoryItem] = []
            inventory_batches: list[InventoryBatch] = []
            item_rows = session.scalars(
                select(InventoryItemRow).options(joinedload(InventoryItemRow.batches))
            ).unique().all()
            for row in sorted(item_rows, key=lambda value: value.name.lower()):
                active_batches = [batch for batch in row.batches if batch.active and batch.quantity > 0]
                if active_batches:
                    earliest_expiry = min(
                        (batch.expires_on for batch in active_batches if batch.expires_on is not None),
                        default=None,
                    )
                    latest_purchase = max(
                        (ensure_utc(batch.purchased_at) for batch in active_batches if batch.purchased_at is not None),
                        default=None,
                    )
                    inventory_items.append(
                        InventoryItem(
                            name=row.name,
                            quantity=round(sum(batch.quantity for batch in active_batches), 2),
                            unit=row.unit,
                            purchased_at=latest_purchase,
                            expires_on=earliest_expiry,
                            category=row.category,
                            min_desired_quantity=row.min_desired_quantity,
                        )
                    )
                for batch in sorted(row.batches, key=lambda value: value.id):
                    inventory_batches.append(
                        InventoryBatch(
                            item_name=row.name,
                            quantity=batch.quantity,
                            unit=row.unit,
                            purchased_at=ensure_utc(batch.purchased_at) if batch.purchased_at is not None else None,
                            expires_on=batch.expires_on,
                            category=row.category,
                            min_desired_quantity=row.min_desired_quantity,
                            source=batch.source,
                            location=batch.location,
                            confidence=batch.confidence,
                            active=batch.active,
                        )
                    )

            recipe_rows = session.scalars(
                select(RecipeRow).options(joinedload(RecipeRow.ingredients))
            ).unique().all()
            recipe_catalog = [
                Recipe(
                    id=row.id,
                    name=row.name,
                    description=row.description,
                    ingredients=[
                        RecipeIngredient(
                            name=ingredient.name,
                            quantity=ingredient.quantity,
                            unit=ingredient.unit,
                            optional=ingredient.optional,
                        )
                        for ingredient in sorted(row.ingredients, key=lambda value: value.id)
                    ],
                    instructions=list(row.instructions or []),
                    tags=list(row.tags or []),
                    calories=row.calories,
                    protein_g=row.protein_g,
                    prep_minutes=row.prep_minutes,
                    step_count=row.step_count,
                    effort_score=row.effort_score,
                    suitable_when_tired=row.suitable_when_tired,
                    cuisine=row.cuisine,
                    source_url=row.source_url,
                    source_title=row.source_title,
                )
                for row in sorted(recipe_rows, key=lambda value: value.name.lower())
            ]

            utility_row = session.get(UtilityStateRow, 1)
            utilities = UtilityLevels(
                water_level_percent=utility_row.water_level_percent if utility_row else 100,
                ice_level_percent=utility_row.ice_level_percent if utility_row else 100,
            )

            nutrition_row = session.get(NutritionProfileRow, 1)
            nutrition_profile = NutritionProfile(
                daily_calorie_target=nutrition_row.daily_calorie_target if nutrition_row else 2200,
                daily_protein_target_g=nutrition_row.daily_protein_target_g if nutrition_row else 110,
                hydration_target_l=nutrition_row.hydration_target_l if nutrition_row else 2.5,
                dietary_preferences=list(nutrition_row.dietary_preferences or []) if nutrition_row else [],
            )

            meal_history = [
                MealRecord(
                    recipe_id=row.recipe_id,
                    recipe_name=row.recipe_name,
                    cooked_at=ensure_utc(row.cooked_at),
                    calories=row.calories,
                    protein_g=row.protein_g,
                    tags=list(row.tags or []),
                    cuisine=row.cuisine,
                )
                for row in session.scalars(select(MealRecordRow).order_by(MealRecordRow.cooked_at.desc())).all()
            ]

            order_rows = session.scalars(
                select(GroceryOrderRow).options(joinedload(GroceryOrderRow.lines)).order_by(GroceryOrderRow.created_at.desc())
            ).unique().all()
            grocery_orders = [
                GroceryOrder(
                    id=row.id,
                    items=[
                        GroceryLine(
                            name=line.name,
                            quantity=line.quantity,
                            unit=line.unit,
                            reason=line.reason,
                        )
                        for line in sorted(row.lines, key=lambda value: value.id)
                    ],
                    created_at=ensure_utc(row.created_at),
                    status=row.status,
                    source=row.source,
                    vendor=row.vendor,
                    eta_minutes=row.eta_minutes,
                )
                for row in order_rows
            ]

            pending_grocery_list = [
                GroceryLine(
                    name=row.name,
                    quantity=row.quantity,
                    unit=row.unit,
                    reason=row.reason,
                )
                for row in session.scalars(select(PendingGroceryItemRow).order_by(PendingGroceryItemRow.id)).all()
            ]

            counters = session.scalars(select(BehaviourCounterRow)).all()
            behaviour = BehaviourProfile(
                command_usage=self._counter_map(counters, "command_usage"),
                recipe_requests=self._counter_map(counters, "recipe_requests"),
                cooked_recipes=self._counter_map(counters, "cooked_recipes"),
                favourite_ingredients=self._counter_map(counters, "favourite_ingredients"),
                active_periods=self._counter_map(counters, "active_periods"),
                preferred_cuisines=self._counter_map(counters, "preferred_cuisines"),
                disliked_ingredients=[
                    row.name
                    for row in session.scalars(select(DislikedIngredientRow).order_by(DislikedIngredientRow.name)).all()
                ],
            )

            session_rows = session.scalars(
                select(ConversationSessionRow)
                .options(joinedload(ConversationSessionRow.turns), joinedload(ConversationSessionRow.summaries))
            ).unique().all()
            conversation_memory = {
                row.user_id: UserConversationMemory(
                    user_id=row.user_id,
                    active_session_id=row.active_session_id,
                    session_started_at=ensure_utc(row.session_started_at),
                    last_activity_at=ensure_utc(row.last_activity_at),
                    carryover_context=row.carryover_context or "",
                    current_status=row.current_status or "",
                    turns=[
                        ConversationTurn(
                            role=turn.role,
                            text=turn.text,
                            timestamp=ensure_utc(turn.timestamp),
                        )
                        for turn in sorted(row.turns, key=lambda value: value.id)
                    ],
                    completed_session_summaries=[
                        summary.text
                        for summary in sorted(row.summaries, key=lambda value: value.id)
                    ],
                )
                for row in session_rows
            }

            recent_events = [
                ContextEvent(
                    timestamp=ensure_utc(row.timestamp),
                    agent=row.agent,
                    action=row.action,
                    summary=row.summary,
                    changes=dict(row.changes or {}),
                )
                for row in session.scalars(
                    select(RuntimeEventRow).order_by(RuntimeEventRow.timestamp.desc()).limit(50)
                ).all()
            ]

        state = SharedContext(
            version=version,
            inventory=inventory_items,
            inventory_batches=inventory_batches,
            recipe_catalog=recipe_catalog,
            utilities=utilities,
            nutrition_profile=nutrition_profile,
            meal_history=meal_history,
            grocery_orders=grocery_orders,
            pending_grocery_list=pending_grocery_list,
            behaviour=behaviour,
            conversation_memory=conversation_memory,
            recent_events=recent_events,
        )
        return normalize_shared_context_datetimes(state)

    def save_snapshot(self, state: SharedContext, *, previous_state: SharedContext | None = None) -> None:
        previous_state = previous_state or self.load_snapshot()
        now = utc_now()
        with self.session() as session:
            app_state = session.get(AppStateRow, 1) or AppStateRow(id=1, version=state.version)
            app_state.version = state.version
            session.merge(app_state)

            self._sync_inventory(session, previous_state, state, now)
            self._replace_recipes(session, state.recipe_catalog)
            self._replace_utilities(session, state.utilities)
            self._replace_nutrition_profile(session, state.nutrition_profile)
            self._replace_meal_history(session, state.meal_history)
            self._replace_grocery_orders(session, state.grocery_orders)
            self._replace_pending_grocery_list(session, state.pending_grocery_list)
            self._replace_behaviour(session, state.behaviour)
            self._replace_conversation_memory(session, state.conversation_memory)
            self._replace_recent_events(session, state.recent_events)

    def import_snapshot(self, state: SharedContext) -> None:
        empty_previous = SharedContext()
        self.save_snapshot(state, previous_state=empty_previous)

    def reset_all(self) -> None:
        with self.session() as session:
            for model in (
                DiagnosticsSnapshotRow,
                HeartbeatPreferenceRow,
                ConversationTurnRow,
                ConversationSummaryRow,
                ConversationSessionRow,
                BehaviourCounterRow,
                DislikedIngredientRow,
                PendingGroceryItemRow,
                GroceryOrderLineRow,
                GroceryOrderRow,
                MealRecordRow,
                RecipeIngredientRow,
                RecipeRow,
                InventoryBatchRow,
                InventoryItemRow,
                RuntimeEventRow,
                AssistantInterventionRow,
                TemporaryStateOverrideRow,
                UserPreferenceRow,
                DecisionProfileRow,
                UtilityStateRow,
                NutritionProfileRow,
                AppStateRow,
            ):
                session.execute(delete(model))

    def list_inventory_batches(self, *, include_inactive: bool = False) -> list[InventoryBatch]:
        snapshot = self.load_snapshot()
        if include_inactive:
            return snapshot.inventory_batches
        return [batch for batch in snapshot.inventory_batches if batch.active]

    def get_heartbeat_preference(self, user_id: str) -> dict[str, object]:
        with self.session() as session:
            row = session.scalar(
                select(HeartbeatPreferenceRow).where(HeartbeatPreferenceRow.user_id == str(user_id))
            )
            if row is None:
                return self._heartbeat_defaults(user_id=user_id)
            return self._heartbeat_row_to_dict(row)

    def set_heartbeat_preference(
        self,
        user_id: str,
        *,
        enabled: bool | None = None,
        interval_minutes: int | None = None,
        dinner_time: str | None = None,
        timezone: str | None = None,
        chat_id: str | None = None,
        last_checked_at: datetime | None = None,
        last_notified_at: datetime | None = None,
        last_alert_signature: str | None = None,
    ) -> dict[str, object]:
        with self.session() as session:
            row = session.scalar(
                select(HeartbeatPreferenceRow).where(HeartbeatPreferenceRow.user_id == str(user_id))
            )
            if row is None:
                defaults = self._heartbeat_defaults(user_id=user_id)
                row = HeartbeatPreferenceRow(
                    user_id=str(user_id),
                    chat_id=defaults["chat_id"],
                    enabled=defaults["enabled"],
                    interval_minutes=defaults["interval_minutes"],
                    dinner_time=defaults["dinner_time"],
                    timezone=defaults["timezone"],
                    last_alert_signature=defaults["last_alert_signature"],
                )
            if enabled is not None:
                row.enabled = enabled
            if interval_minutes is not None:
                row.interval_minutes = interval_minutes
            if dinner_time is not None:
                row.dinner_time = dinner_time
            if timezone is not None:
                row.timezone = timezone
            if chat_id is not None:
                row.chat_id = chat_id
            if last_checked_at is not None:
                row.last_checked_at = last_checked_at
            if last_notified_at is not None:
                row.last_notified_at = last_notified_at
            if last_alert_signature is not None:
                row.last_alert_signature = last_alert_signature
            session.add(row)
            session.flush()
            return self._heartbeat_row_to_dict(row)

    def list_due_heartbeat_preferences(self, *, now: datetime) -> list[dict[str, object]]:
        with self.session() as session:
            rows = session.scalars(select(HeartbeatPreferenceRow).where(HeartbeatPreferenceRow.enabled.is_(True))).all()
            due: list[dict[str, object]] = []
            for row in rows:
                if row.last_checked_at is None:
                    due.append(self._heartbeat_row_to_dict(row))
                    continue
                delta_seconds = (ensure_utc(now) - ensure_utc(row.last_checked_at)).total_seconds()
                if delta_seconds >= row.interval_minutes * 60:
                    due.append(self._heartbeat_row_to_dict(row))
            return due

    def record_diagnostics_snapshot(
        self,
        *,
        user_id: str | None,
        overall_status: str,
        issues: list[dict[str, object]],
        recommended_actions: list[str],
    ) -> None:
        with self.session() as session:
            session.add(
                DiagnosticsSnapshotRow(
                    user_id=str(user_id) if user_id else None,
                    overall_status=overall_status,
                    issues=issues,
                    recommended_actions=recommended_actions,
                    created_at=utc_now(),
                )
            )

    def get_user_preferences(self, user_id: str) -> UserPreferences:
        with self.session() as session:
            row = session.scalar(select(UserPreferenceRow).where(UserPreferenceRow.user_id == str(user_id)))
            if row is None:
                return self._user_preference_defaults(user_id)
            return self._user_preferences_row_to_domain(row)

    def set_user_preferences(self, user_id: str, **kwargs) -> UserPreferences:
        if "search_model" in kwargs and kwargs["search_model"] is not None:
            search_model = str(kwargs["search_model"]).strip()
            if not is_valid_search_model(search_model):
                raise ValueError("search_model is not supported.")
            kwargs["search_model"] = search_model
        with self.session() as session:
            row = session.scalar(select(UserPreferenceRow).where(UserPreferenceRow.user_id == str(user_id)))
            if row is None:
                defaults = self._user_preference_defaults(user_id)
                row = UserPreferenceRow(
                    user_id=defaults.user_id,
                    mode=defaults.mode,
                    meal_window_start=defaults.meal_window_start,
                    meal_window_end=defaults.meal_window_end,
                    late_night_window_start=defaults.late_night_window_start,
                    late_night_window_end=defaults.late_night_window_end,
                    max_prep_minutes=defaults.max_prep_minutes,
                    notification_frequency=defaults.notification_frequency,
                    dietary_preferences=list(defaults.dietary_preferences),
                    search_model=defaults.search_model,
                    updated_at=utc_now(),
                )
            for field in (
                "mode",
                "meal_window_start",
                "meal_window_end",
                "late_night_window_start",
                "late_night_window_end",
                "max_prep_minutes",
                "notification_frequency",
                "dietary_preferences",
                "search_model",
            ):
                if field in kwargs and kwargs[field] is not None:
                    setattr(row, field, kwargs[field])
            row.updated_at = utc_now()
            session.add(row)
            session.flush()
            return self._user_preferences_row_to_domain(row)

    def list_active_temporary_states(self, user_id: str) -> list[TemporaryStateOverride]:
        with self.session() as session:
            now = utc_now()
            session.execute(
                delete(TemporaryStateOverrideRow).where(TemporaryStateOverrideRow.expires_at <= now)
            )
            rows = session.scalars(
                select(TemporaryStateOverrideRow)
                .where(TemporaryStateOverrideRow.user_id == str(user_id))
                .order_by(TemporaryStateOverrideRow.created_at.desc())
            ).all()
            return [
                TemporaryStateOverride(
                    id=row.id,
                    user_id=row.user_id,
                    state=row.state,
                    value=row.value,
                    expires_at=ensure_utc(row.expires_at),
                    source=row.source,
                    note=row.note,
                    created_at=ensure_utc(row.created_at),
                )
                for row in rows
            ]

    def set_temporary_state(
        self,
        user_id: str,
        *,
        state: str,
        value: str,
        expires_at: datetime,
        source: str = "telegram",
        note: str = "",
    ) -> TemporaryStateOverride:
        with self.session() as session:
            session.execute(
                delete(TemporaryStateOverrideRow).where(
                    TemporaryStateOverrideRow.user_id == str(user_id),
                    TemporaryStateOverrideRow.state == state,
                )
            )
            row = TemporaryStateOverrideRow(
                id=f"state_{uuid4().hex}",
                user_id=str(user_id),
                state=state,
                value=value,
                expires_at=ensure_utc(expires_at),
                source=source,
                note=note,
                created_at=utc_now(),
            )
            session.add(row)
            session.flush()
            return TemporaryStateOverride(
                id=row.id,
                user_id=row.user_id,
                state=row.state,
                value=row.value,
                expires_at=ensure_utc(row.expires_at),
                source=row.source,
                note=row.note,
                created_at=ensure_utc(row.created_at),
            )

    def clear_temporary_state(self, user_id: str, state: str) -> int:
        with self.session() as session:
            rows = session.scalars(
                select(TemporaryStateOverrideRow).where(
                    TemporaryStateOverrideRow.user_id == str(user_id),
                    TemporaryStateOverrideRow.state == state,
                )
            ).all()
            count = len(rows)
            for row in rows:
                session.delete(row)
            return count

    def get_decision_profile(self, user_id: str) -> DecisionProfile:
        with self.session() as session:
            row = session.scalar(select(DecisionProfileRow).where(DecisionProfileRow.user_id == str(user_id)))
            if row is None:
                return self._decision_profile_defaults(user_id)
            return self._decision_profile_row_to_domain(row)

    def set_decision_profile(self, user_id: str, **kwargs) -> DecisionProfile:
        with self.session() as session:
            row = session.scalar(select(DecisionProfileRow).where(DecisionProfileRow.user_id == str(user_id)))
            if row is None:
                defaults = self._decision_profile_defaults(user_id)
                row = DecisionProfileRow(
                    user_id=defaults.user_id,
                    ignore_nudge_rate=defaults.ignore_nudge_rate,
                    healthy_meal_acceptance_score=defaults.healthy_meal_acceptance_score,
                    quick_food_bias=defaults.quick_food_bias,
                    eat_at_home_likelihood=defaults.eat_at_home_likelihood,
                    stress_eating_signal=defaults.stress_eating_signal,
                    user_threshold=defaults.user_threshold,
                    updated_at=utc_now(),
                )
            for field in (
                "ignore_nudge_rate",
                "healthy_meal_acceptance_score",
                "quick_food_bias",
                "eat_at_home_likelihood",
                "stress_eating_signal",
                "user_threshold",
            ):
                if field in kwargs and kwargs[field] is not None:
                    setattr(row, field, float(kwargs[field]))
            row.updated_at = utc_now()
            session.add(row)
            session.flush()
            return self._decision_profile_row_to_domain(row)

    def create_assistant_intervention(self, intervention: AssistantIntervention) -> AssistantIntervention:
        with self.session() as session:
            row = AssistantInterventionRow(
                id=intervention.id,
                user_id=intervention.user_id,
                thread_key=intervention.thread_key,
                sequence_index=intervention.sequence_index,
                context_hash=intervention.context_hash,
                decision_type=intervention.decision_type,
                sent_at=ensure_utc(intervention.sent_at),
                status=intervention.status,
                resolved_at=ensure_utc(intervention.resolved_at) if intervention.resolved_at else None,
                mute_until=ensure_utc(intervention.mute_until) if intervention.mute_until else None,
                message=intervention.message,
                recommended_action=intervention.recommended_action,
                score=intervention.score,
                confidence=intervention.confidence,
                reason_codes=list(intervention.reason_codes),
                draft_items=[item.model_dump(mode="json") for item in intervention.draft_items],
                extra_metadata=dict(intervention.metadata),
            )
            session.add(row)
            session.flush()
            return intervention

    def list_assistant_interventions(
        self,
        user_id: str,
        *,
        thread_key: str | None = None,
        limit: int = 20,
    ) -> list[AssistantIntervention]:
        with self.session() as session:
            query = select(AssistantInterventionRow).where(AssistantInterventionRow.user_id == str(user_id))
            if thread_key:
                query = query.where(AssistantInterventionRow.thread_key == thread_key)
            rows = session.scalars(
                query.order_by(AssistantInterventionRow.sent_at.desc()).limit(limit)
            ).all()
            return [self._intervention_row_to_domain(row) for row in rows]

    def get_latest_intervention_for_thread(self, user_id: str, thread_key: str) -> AssistantIntervention | None:
        interventions = self.list_assistant_interventions(user_id, thread_key=thread_key, limit=1)
        return interventions[0] if interventions else None

    def get_assistant_intervention(self, intervention_id: str) -> AssistantIntervention | None:
        with self.session() as session:
            row = session.get(AssistantInterventionRow, intervention_id)
            if row is None:
                return None
            return self._intervention_row_to_domain(row)

    def record_intervention_feedback(
        self,
        *,
        user_id: str,
        status: str,
        intervention_id: str | None = None,
        thread_key: str | None = None,
        detail: str = "",
        mute_until: datetime | None = None,
    ) -> AssistantIntervention | None:
        with self.session() as session:
            row = None
            if intervention_id:
                row = session.get(AssistantInterventionRow, intervention_id)
            elif thread_key:
                row = session.scalar(
                    select(AssistantInterventionRow)
                    .where(
                        AssistantInterventionRow.user_id == str(user_id),
                        AssistantInterventionRow.thread_key == thread_key,
                    )
                    .order_by(AssistantInterventionRow.sent_at.desc())
                )
            if row is None:
                return None
            row.status = status
            if status in {"completed", "dismissed", "clicked"}:
                row.resolved_at = utc_now()
            if mute_until is not None:
                row.mute_until = ensure_utc(mute_until)
            if detail:
                metadata = dict(row.extra_metadata or {})
                metadata["detail"] = detail
                row.extra_metadata = metadata
            session.add(row)
            session.flush()
            return self._intervention_row_to_domain(row)

    def count_assistant_interventions(self) -> int:
        with self.session() as session:
            return len(session.scalars(select(AssistantInterventionRow.id)).all())

    def database_summary(self) -> dict[str, object]:
        snapshot = self.load_snapshot()
        return {
            "database_url": self.database_url,
            "inventory_items": len(snapshot.inventory),
            "inventory_batches": len(snapshot.inventory_batches),
            "recipes": len(snapshot.recipe_catalog),
            "meal_history": len(snapshot.meal_history),
            "grocery_orders": len(snapshot.grocery_orders),
            "pending_grocery_list": len(snapshot.pending_grocery_list),
            "conversation_users": len(snapshot.conversation_memory),
            "assistant_interventions": self.count_assistant_interventions(),
        }

    def dispose(self) -> None:
        self.engine.dispose()

    def _sync_inventory(
        self,
        session: Session,
        previous_state: SharedContext,
        state: SharedContext,
        now: datetime,
    ) -> None:
        current_rows = {
            row.name.lower(): row
            for row in session.scalars(
                select(InventoryItemRow).options(joinedload(InventoryItemRow.batches))
            ).unique().all()
        }
        previous = {item.name.lower(): item for item in previous_state.inventory}
        after = {item.name.lower(): item for item in state.inventory}

        for key in sorted(set(previous) | set(after)):
            previous_item = previous.get(key)
            after_item = after.get(key)
            row = current_rows.get(key)
            if after_item is None:
                if row is not None:
                    for batch in row.batches:
                        batch.active = False
                        batch.quantity = 0.0
                        batch.updated_at = now
                continue

            if row is None:
                row = InventoryItemRow(
                    name=after_item.name,
                    category=after_item.category,
                    unit=after_item.unit,
                    min_desired_quantity=after_item.min_desired_quantity,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
                session.flush()
                current_rows[key] = row

            row.category = after_item.category
            row.unit = after_item.unit
            row.min_desired_quantity = after_item.min_desired_quantity
            row.updated_at = now

            previous_quantity = previous_item.quantity if previous_item is not None else 0.0
            delta = round(after_item.quantity - previous_quantity, 4)
            if delta > 0:
                session.add(
                    InventoryBatchRow(
                        item_id=row.id,
                        quantity=delta,
                        purchased_at=after_item.purchased_at or now,
                        expires_on=after_item.expires_on,
                        added_by="system",
                        source="snapshot_sync",
                        confidence=1.0,
                        location="fridge",
                        active=True,
                        created_at=now,
                        updated_at=now,
                    )
                )
            elif delta < 0:
                self._consume_batches(session, row, abs(delta), now)
            else:
                active_batches = [batch for batch in row.batches if batch.active and batch.quantity > 0]
                if active_batches:
                    latest_batch = sorted(
                        active_batches,
                        key=lambda value: (value.purchased_at or now, value.id),
                        reverse=True,
                    )[0]
                    latest_batch.expires_on = after_item.expires_on
                    if after_item.purchased_at is not None:
                        latest_batch.purchased_at = after_item.purchased_at
                    latest_batch.updated_at = now

        active_keys = set(after)
        state.inventory_batches = [
            batch
            for batch in state.inventory_batches
            if batch.item_name.lower() in active_keys or batch.active is False
        ]

    def _consume_batches(self, session: Session, row: InventoryItemRow, amount: float, now: datetime) -> None:
        remaining = amount
        batches = sorted(
            [batch for batch in row.batches if batch.active and batch.quantity > 0],
            key=lambda value: (
                value.expires_on is None,
                value.expires_on or date.max,
                value.purchased_at or now,
                value.id,
            ),
        )
        for batch in batches:
            if remaining <= 0:
                break
            if batch.quantity <= remaining + 1e-6:
                remaining -= batch.quantity
                batch.quantity = 0.0
                batch.active = False
            else:
                batch.quantity = round(batch.quantity - remaining, 4)
                remaining = 0.0
            batch.updated_at = now

    def _replace_recipes(self, session: Session, recipes: list[Recipe]) -> None:
        session.execute(delete(RecipeIngredientRow))
        session.execute(delete(RecipeRow))
        for recipe in recipes:
            row = RecipeRow(
                id=recipe.id,
                name=recipe.name,
                description=recipe.description,
                instructions=list(recipe.instructions),
                tags=list(recipe.tags),
                calories=recipe.calories,
                protein_g=recipe.protein_g,
                prep_minutes=recipe.prep_minutes,
                step_count=recipe.step_count,
                effort_score=recipe.effort_score,
                suitable_when_tired=recipe.suitable_when_tired,
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

    def _replace_utilities(self, session: Session, utilities: UtilityLevels) -> None:
        row = session.get(UtilityStateRow, 1) or UtilityStateRow(id=1)
        row.water_level_percent = utilities.water_level_percent
        row.ice_level_percent = utilities.ice_level_percent
        session.merge(row)

    def _replace_nutrition_profile(self, session: Session, nutrition: NutritionProfile) -> None:
        row = session.get(NutritionProfileRow, 1) or NutritionProfileRow(id=1)
        row.daily_calorie_target = nutrition.daily_calorie_target
        row.daily_protein_target_g = nutrition.daily_protein_target_g
        row.hydration_target_l = nutrition.hydration_target_l
        row.dietary_preferences = list(nutrition.dietary_preferences)
        session.merge(row)

    def _replace_meal_history(self, session: Session, meals: list[MealRecord]) -> None:
        session.execute(delete(MealRecordRow))
        for meal in meals:
            session.add(
                MealRecordRow(
                    recipe_id=meal.recipe_id,
                    recipe_name=meal.recipe_name,
                    cooked_at=meal.cooked_at,
                    calories=meal.calories,
                    protein_g=meal.protein_g,
                    tags=list(meal.tags),
                    cuisine=meal.cuisine,
                )
            )

    def _replace_grocery_orders(self, session: Session, orders: list[GroceryOrder]) -> None:
        session.execute(delete(GroceryOrderLineRow))
        session.execute(delete(GroceryOrderRow))
        for order in orders:
            row = GroceryOrderRow(
                id=order.id,
                created_at=order.created_at,
                status=order.status,
                source=order.source,
                vendor=order.vendor,
                eta_minutes=order.eta_minutes,
            )
            session.add(row)
            session.flush()
            for item in order.items:
                session.add(
                    GroceryOrderLineRow(
                        order_id=order.id,
                        name=item.name,
                        quantity=item.quantity,
                        unit=item.unit,
                        reason=item.reason,
                    )
                )

    def _replace_pending_grocery_list(self, session: Session, items: list[GroceryLine]) -> None:
        session.execute(delete(PendingGroceryItemRow))
        for item in items:
            session.add(
                PendingGroceryItemRow(
                    name=item.name,
                    quantity=item.quantity,
                    unit=item.unit,
                    reason=item.reason,
                )
            )

    def _replace_behaviour(self, session: Session, behaviour: BehaviourProfile) -> None:
        session.execute(delete(BehaviourCounterRow))
        session.execute(delete(DislikedIngredientRow))
        mapping = {
            "command_usage": behaviour.command_usage,
            "recipe_requests": behaviour.recipe_requests,
            "cooked_recipes": behaviour.cooked_recipes,
            "favourite_ingredients": behaviour.favourite_ingredients,
            "active_periods": behaviour.active_periods,
            "preferred_cuisines": behaviour.preferred_cuisines,
        }
        for category, values in mapping.items():
            for key, value in values.items():
                session.add(BehaviourCounterRow(category=category, key=key, value=value))
        for name in behaviour.disliked_ingredients:
            session.add(DislikedIngredientRow(name=name))

    def _replace_conversation_memory(
        self,
        session: Session,
        conversation_memory: dict[str, UserConversationMemory],
    ) -> None:
        session.execute(delete(ConversationTurnRow))
        session.execute(delete(ConversationSummaryRow))
        session.execute(delete(ConversationSessionRow))
        for memory in conversation_memory.values():
            row = ConversationSessionRow(
                user_id=memory.user_id,
                active_session_id=memory.active_session_id,
                session_started_at=memory.session_started_at,
                last_activity_at=memory.last_activity_at,
                carryover_context=memory.carryover_context,
                current_status=memory.current_status,
            )
            session.add(row)
            session.flush()
            for turn in memory.turns:
                session.add(
                    ConversationTurnRow(
                        session_row_id=row.id,
                        role=turn.role,
                        text=turn.text,
                        timestamp=turn.timestamp,
                    )
                )
            for summary in memory.completed_session_summaries:
                session.add(ConversationSummaryRow(session_row_id=row.id, text=summary))

    def _replace_recent_events(self, session: Session, events: list[ContextEvent]) -> None:
        session.execute(delete(RuntimeEventRow))
        for event in events[:50]:
            session.add(
                RuntimeEventRow(
                    timestamp=event.timestamp,
                    agent=event.agent,
                    action=event.action,
                    summary=event.summary,
                    changes=event.changes,
                )
            )

    @staticmethod
    def _counter_map(rows: list[BehaviourCounterRow], category: str) -> dict[str, int]:
        return {
            row.key: row.value
            for row in rows
            if row.category == category
        }

    @staticmethod
    def _heartbeat_defaults(*, user_id: str) -> dict[str, object]:
        return {
            "user_id": str(user_id),
            "chat_id": None,
            "enabled": False,
            "interval_minutes": 60,
            "dinner_time": "19:00",
            "timezone": "Asia/Singapore",
            "last_checked_at": None,
            "last_notified_at": None,
            "last_alert_signature": "",
            "quiet_hours_start": None,
            "quiet_hours_end": None,
        }

    @staticmethod
    def _user_preference_defaults(user_id: str) -> UserPreferences:
        return UserPreferences(user_id=str(user_id))

    @staticmethod
    def _user_preferences_row_to_domain(row: UserPreferenceRow) -> UserPreferences:
        return UserPreferences(
            user_id=row.user_id,
            mode=row.mode,
            meal_window_start=row.meal_window_start,
            meal_window_end=row.meal_window_end,
            late_night_window_start=row.late_night_window_start,
            late_night_window_end=row.late_night_window_end,
            max_prep_minutes=row.max_prep_minutes,
            notification_frequency=row.notification_frequency,
            dietary_preferences=list(row.dietary_preferences or []),
            search_model=row.search_model or DEFAULT_SEARCH_MODEL,
        )

    @staticmethod
    def _decision_profile_defaults(user_id: str) -> DecisionProfile:
        return DecisionProfile(user_id=str(user_id), updated_at=utc_now())

    @staticmethod
    def _decision_profile_row_to_domain(row: DecisionProfileRow) -> DecisionProfile:
        return DecisionProfile(
            user_id=row.user_id,
            ignore_nudge_rate=row.ignore_nudge_rate,
            healthy_meal_acceptance_score=row.healthy_meal_acceptance_score,
            quick_food_bias=row.quick_food_bias,
            eat_at_home_likelihood=row.eat_at_home_likelihood,
            stress_eating_signal=row.stress_eating_signal,
            user_threshold=row.user_threshold,
            updated_at=ensure_utc(row.updated_at),
        )

    @staticmethod
    def _intervention_row_to_domain(row: AssistantInterventionRow) -> AssistantIntervention:
        return AssistantIntervention(
            id=row.id,
            user_id=row.user_id,
            thread_key=row.thread_key,
            sequence_index=row.sequence_index,
            context_hash=row.context_hash,
            decision_type=row.decision_type,
            sent_at=ensure_utc(row.sent_at),
            status=row.status,
            resolved_at=ensure_utc(row.resolved_at) if row.resolved_at else None,
            mute_until=ensure_utc(row.mute_until) if row.mute_until else None,
            message=row.message,
            recommended_action=row.recommended_action,
            score=row.score,
            confidence=row.confidence,
            reason_codes=list(row.reason_codes or []),
            draft_items=[GroceryLine(**item) for item in (row.draft_items or [])],
            metadata=dict(row.extra_metadata or {}),
        )

    @staticmethod
    def _heartbeat_row_to_dict(row: HeartbeatPreferenceRow) -> dict[str, object]:
        return {
            "user_id": row.user_id,
            "chat_id": row.chat_id,
            "enabled": row.enabled,
            "interval_minutes": row.interval_minutes,
            "dinner_time": row.dinner_time,
            "timezone": row.timezone,
            "last_checked_at": row.last_checked_at.isoformat() if row.last_checked_at else None,
            "last_notified_at": row.last_notified_at.isoformat() if row.last_notified_at else None,
            "last_alert_signature": row.last_alert_signature or "",
            "quiet_hours_start": row.quiet_hours_start,
            "quiet_hours_end": row.quiet_hours_end,
        }
