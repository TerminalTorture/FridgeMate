from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, time, timedelta
from typing import Any, TypedDict
from uuid import uuid4

from app.agents.behaviour import BehaviourAgent
from app.agents.grocery import GroceryAgent
from app.agents.inventory import InventoryAgent
from app.agents.recipe import RecipeAgent
from app.core.context_store import ContextStore
from app.core.conversation_manager import ConversationManager
from app.core.override_parser import OverrideParser
from app.core.time_utils import ensure_utc, get_timezone, singapore_now, utc_now
from app.core.tracing import add_event, record_decision_rule
from app.models.api import RecipeSuggestion
from app.models.domain import (
    AssistantIntervention,
    DecisionProfile,
    DecisionResult,
    GroceryLine,
    InventoryItem,
    OverrideIntent,
    Recipe,
    SharedContext,
    TemporaryStateOverride,
    UserPreferences,
)


class CandidateMetadata(TypedDict, total=False):
    expiring_items: list[str]
    urgent: bool


class InterventionCandidate(TypedDict):
    intervention_type: str
    score: float
    confidence: float
    reason_codes: list[str]
    thread_key: str
    recommended_action: str
    recipe_id: str | None
    recipe_name: str | None
    draft_items: list[GroceryLine]
    metadata: CandidateMetadata


class DecisionContext(TypedDict):
    user_id: str
    snapshot: SharedContext
    preferences: UserPreferences
    states: list[TemporaryStateOverride]
    state_map: dict[str, TemporaryStateOverride]
    profile: DecisionProfile
    heartbeat: dict[str, object]
    now: datetime
    recipes: dict[str, Recipe]
    suggestions: list[RecipeSuggestion]
    low_stock: list[InventoryItem]
    expiring: list[InventoryItem]
    quick_mode: bool
    effective_prep_minutes: int


class DecisionEngine:
    RESOLVED_STATUSES = {"completed", "dismissed"}

    def __init__(
        self,
        *,
        store: ContextStore,
        recipe_agent: RecipeAgent,
        inventory_agent: InventoryAgent,
        grocery_agent: GroceryAgent,
        behaviour_agent: BehaviourAgent,
        conversation_manager: ConversationManager,
        override_parser: OverrideParser | None = None,
    ) -> None:
        self.store = store
        self.recipe_agent = recipe_agent
        self.inventory_agent = inventory_agent
        self.grocery_agent = grocery_agent
        self.behaviour_agent = behaviour_agent
        self.conversation_manager = conversation_manager
        self.override_parser = override_parser or OverrideParser()
        self.llm_service: Any | None = None

    def bind_llm_service(self, llm_service) -> None:
        self.llm_service = llm_service
        self.override_parser.bind_llm_service(llm_service)

    def parse_override_intent(self, text: str) -> OverrideIntent | None:
        return self.override_parser.parse(text)

    def apply_override_text(self, user_id: str, text: str) -> dict[str, object] | None:
        intent = self.parse_override_intent(text)
        if intent is None:
            return None
        return self.apply_override_intent(user_id, intent)

    def apply_override_intent(self, user_id: str, intent: OverrideIntent) -> dict[str, object]:
        if intent.kind == "temporary_state":
            state = self._apply_temporary_state(user_id, intent)
            return {
                "handled": True,
                "type": "temporary_state",
                "message": self._temporary_state_message(state),
                "state": state.model_dump(mode="json"),
                "public_state": self.public_state(user_id),
            }

        if intent.kind == "mode":
            preferences = self.store.set_user_preferences(user_id, mode=intent.value.lower())
            return {
                "handled": True,
                "type": "mode",
                "message": f"Mode set to {preferences.mode}. I’ll steer suggestions around that.",
                "preferences": preferences.model_dump(mode="json"),
                "public_state": self.public_state(user_id),
            }

        if intent.kind in {"preference", "preference_adjustment"}:
            preferences = self._apply_preference_intent(user_id, intent)
            return {
                "handled": True,
                "type": "preference",
                "message": self._preference_message(intent, preferences),
                "preferences": preferences.model_dump(mode="json"),
                "public_state": self.public_state(user_id),
            }

        return {"handled": False, "message": "I did not apply any steering change."}

    def public_state(self, user_id: str) -> dict[str, object]:
        preferences = self.store.user_preferences(user_id)
        states = self.store.temporary_states(user_id)
        heartbeat = self.store.heartbeat_preference(user_id)
        session = self.conversation_manager.session_status(user_id)
        last_interventions = [
            intervention.model_dump(mode="json")
            for intervention in self.store.list_assistant_interventions(user_id, limit=5)
        ]
        return {
            "user_id": user_id,
            "preferences": preferences.model_dump(mode="json"),
            "temporary_states": [state.model_dump(mode="json") for state in states],
            "heartbeat": heartbeat,
            "session": session,
            "recent_interventions": last_interventions,
        }

    def run_for_user(
        self,
        user_id: str,
        *,
        force: bool = False,
        prefer_easier: bool = False,
    ) -> DecisionResult:
        add_event(
            name="decision_run_start",
            detail={"user_id": user_id, "force": force, "prefer_easier": prefer_easier},
        )
        context = self._assemble_context(user_id, prefer_easier=prefer_easier)
        candidates = self._candidate_interventions(context)
        record_decision_rule(
            rule="candidates_available",
            triggered=bool(candidates),
            detail={"candidate_count": len(candidates)},
        )
        if not candidates:
            return DecisionResult(
                user_id=user_id,
                intervene=False,
                confidence=0.0,
                reason_codes=["no_high_value_action"],
                recommended_action="suppress",
                message="No proactive nudge is worth sending right now.",
            )

        best = self._llm_select_candidate(context, candidates) or max(
            candidates,
            key=lambda candidate: (candidate["score"], candidate["confidence"]),
        )
        thread_key = best["thread_key"]
        latest = self.store.latest_assistant_intervention(user_id, thread_key)
        sequence_index = self._next_sequence_index(best, latest)
        context_hash = self._build_context_hash(best)

        if latest and latest.mute_until and ensure_utc(latest.mute_until) > utc_now() and not force:
            record_decision_rule(
                rule="thread_muted",
                triggered=True,
                detail={"thread_key": thread_key},
            )
            return DecisionResult(
                user_id=user_id,
                intervene=False,
                confidence=best["confidence"],
                intervention_type=best["intervention_type"],
                score=best["score"],
                reason_codes=[*best["reason_codes"], "thread_muted"],
                thread_key=thread_key,
                recommended_action="suppress",
                message="That issue is muted for now.",
                context_hash=context_hash,
                sequence_index=sequence_index,
                metadata=dict(best["metadata"]),
            )

        cooldown_active = self._cooldown_active(latest, context, context_hash)
        threshold = self._threshold(context)
        intervene = (best["score"] >= threshold and best["confidence"] >= 0.45 and not cooldown_active) or force
        record_decision_rule(
            rule="threshold_check",
            triggered=best["score"] >= threshold,
            detail={"score": best["score"], "threshold": threshold},
        )
        record_decision_rule(
            rule="confidence_check",
            triggered=best["confidence"] >= 0.45,
            detail={"confidence": best["confidence"], "threshold": 0.45},
        )
        record_decision_rule(
            rule="cooldown_block",
            triggered=cooldown_active,
            detail={"thread_key": thread_key},
        )

        if context["preferences"].mode == "silent" and not best["metadata"].get("urgent") and not force:
            intervene = False
            record_decision_rule(
                rule="silent_mode_suppression",
                triggered=True,
                detail={"urgent": bool(best["metadata"].get("urgent"))},
            )

        message = self._generate_message(best, context, sequence_index)
        reason_codes = list(best["reason_codes"])
        if cooldown_active:
            reason_codes.append("cooldown_active")
        if best["score"] < threshold and not force:
            reason_codes.append("below_threshold")

        return DecisionResult(
            user_id=user_id,
            intervene=intervene,
            confidence=round(best["confidence"], 2),
            intervention_type=best["intervention_type"],
            score=round(best["score"], 2),
            reason_codes=reason_codes,
            thread_key=thread_key,
            recommended_action=best["recommended_action"],
            message=message,
            draft_items=list(best["draft_items"]),
            recipe_id=best["recipe_id"],
            recipe_name=best["recipe_name"],
            context_hash=context_hash,
            sequence_index=sequence_index,
            metadata=dict(best["metadata"]),
        )

    def materialize_intervention(self, result: DecisionResult) -> DecisionResult:
        if not result.intervene or not result.thread_key:
            return result
        intervention = AssistantIntervention(
            id=f"intv_{uuid4().hex}",
            user_id=result.user_id,
            thread_key=result.thread_key,
            sequence_index=result.sequence_index,
            context_hash=result.context_hash,
            decision_type=result.intervention_type or "unknown",
            sent_at=utc_now(),
            message=result.message,
            recommended_action=result.recommended_action,
            score=result.score,
            confidence=result.confidence,
            reason_codes=list(result.reason_codes),
            draft_items=list(result.draft_items),
            metadata=dict(result.metadata),
        )
        stored = self.store.create_assistant_intervention(intervention)
        quick_actions = self._build_quick_actions(stored)
        return result.model_copy(update={"intervention_id": stored.id, "quick_actions": quick_actions})

    def build_reply_markup(self, result: DecisionResult) -> dict[str, object] | None:
        if not result.intervention_id or not result.quick_actions:
            return None
        keyboard: list[list[dict[str, str]]] = []
        row: list[dict[str, str]] = []
        for action in result.quick_actions:
            row.append({"text": action["label"], "callback_data": action["callback_data"]})
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        return {"inline_keyboard": keyboard}

    def handle_callback(self, user_id: str, action: str, intervention_id: str) -> dict[str, object]:
        intervention = self.store.assistant_intervention(intervention_id)
        if intervention is None:
            return {"message": "That action is no longer available.", "reply_markup": None}

        if action == "cook":
            self.record_feedback(user_id=user_id, intervention_id=intervention_id, status="completed", detail="cook_now")
            reply = f"Go with {intervention.recommended_action.lower()}. I’ll keep later suggestions around the same effort."
            return {"message": reply, "reply_markup": None}

        if action == "easier":
            self.record_feedback(user_id=user_id, intervention_id=intervention_id, status="clicked", detail="show_easier_option")
            decision = self.materialize_intervention(self.run_for_user(user_id, force=True, prefer_easier=True))
            return {"message": decision.message, "reply_markup": self.build_reply_markup(decision)}

        if action == "draft":
            added_count = self._draft_items(intervention.draft_items)
            self.record_feedback(user_id=user_id, intervention_id=intervention_id, status="completed", detail="drafted_shopping_list")
            return {"message": f"Drafted {added_count} item(s) to your shopping list.", "reply_markup": None}

        if action == "ignore":
            mute_until = utc_now() + timedelta(days=3) if intervention.sequence_index >= 3 else None
            self.record_feedback(
                user_id=user_id,
                intervention_id=intervention_id,
                status="ignored",
                detail="ignore_tonight",
                mute_until=mute_until,
            )
            if mute_until:
                return {"message": "Okay. I’ll back off on this thread for a few days.", "reply_markup": None}
            return {"message": "Okay. I’ll leave tonight alone.", "reply_markup": None}

        if action == "not_home":
            intent = OverrideIntent(kind="temporary_state", target="not_home", value="active", duration_hours=12, confidence=1.0)
            self.apply_override_intent(user_id, intent)
            self.record_feedback(user_id=user_id, intervention_id=intervention_id, status="dismissed", detail="not_home")
            return {"message": "Noted. I’ll stop home-meal nudges for tonight.", "reply_markup": None}

        if action == "ordered_food":
            intent = OverrideIntent(kind="temporary_state", target="not_home", value="active", duration_hours=12, confidence=1.0)
            self.apply_override_intent(user_id, intent)
            self.record_feedback(user_id=user_id, intervention_id=intervention_id, status="dismissed", detail="ordered_food", action="ordered_food")
            return {"message": "Got it. I’ll keep tonight quiet and steer faster options next time.", "reply_markup": None}

        return {"message": "I did not recognise that action.", "reply_markup": None}

    def record_feedback(
        self,
        *,
        user_id: str,
        status: str,
        intervention_id: str | None = None,
        thread_key: str | None = None,
        detail: str = "",
        mute_until: datetime | None = None,
        action: str | None = None,
    ) -> AssistantIntervention | None:
        intervention = self.store.record_intervention_feedback(
            user_id=user_id,
            status=status,
            intervention_id=intervention_id,
            thread_key=thread_key,
            detail=detail,
            mute_until=mute_until,
        )
        profile = self.store.decision_profile(user_id)
        updates = self._profile_feedback_updates(profile, intervention, status=status, action=action)
        if updates:
            self.store.set_decision_profile(user_id, **updates)
        return intervention

    def _assemble_context(self, user_id: str, *, prefer_easier: bool) -> DecisionContext:
        snapshot = self.store.snapshot()
        preferences = self.store.user_preferences(user_id)
        states = self.store.temporary_states(user_id)
        profile = self.store.decision_profile(user_id)
        heartbeat = self.store.heartbeat_preference(user_id)
        timezone_name = str(heartbeat.get("timezone") or "Asia/Singapore")
        now = datetime.now(get_timezone(timezone_name))
        recipes = {recipe.id: recipe for recipe in self.recipe_agent.list_recipes()}
        suggestions = [self.recipe_agent.evaluate_recipe(recipe) for recipe in recipes.values()]
        low_stock = self.inventory_agent.low_stock_items()
        expiring = self.inventory_agent.expiring_soon(days=1)
        state_map = {state.state: state for state in states}
        quick_mode = preferences.mode == "lazy" or prefer_easier or any(
            key in state_map for key in ("tired", "busy", "stressed")
        )
        effective_prep = preferences.max_prep_minutes
        if quick_mode:
            effective_prep = min(effective_prep, 7 if preferences.mode == "lazy" or prefer_easier else 10)

        return {
            "user_id": user_id,
            "snapshot": snapshot,
            "preferences": preferences,
            "states": states,
            "state_map": state_map,
            "profile": profile,
            "heartbeat": heartbeat,
            "now": now,
            "recipes": recipes,
            "suggestions": suggestions,
            "low_stock": low_stock,
            "expiring": expiring,
            "quick_mode": quick_mode,
            "effective_prep_minutes": effective_prep,
        }

    def _candidate_interventions(self, context: DecisionContext) -> list[InterventionCandidate]:
        candidates: list[InterventionCandidate] = []
        expiring_candidate = self._expiring_candidate(context)
        if expiring_candidate:
            candidates.append(expiring_candidate)
        cook_candidate = self._cook_now_candidate(context)
        if cook_candidate:
            candidates.append(cook_candidate)
        pickup_candidate = self._pickup_candidate(context)
        if pickup_candidate:
            candidates.append(pickup_candidate)
        late_night_candidate = self._late_night_candidate(context)
        if late_night_candidate:
            candidates.append(late_night_candidate)
        return candidates

    def _cook_now_candidate(self, context: DecisionContext) -> InterventionCandidate | None:
        if self._has_state(context, "not_home"):
            return None
        if not self._in_window(context["now"], context["preferences"].meal_window_start, context["preferences"].meal_window_end):
            return None
        recipe_bundle = self._best_recipe(context, require_ready=True, prefer_expiring=False)
        if recipe_bundle is None:
            return None
        recipe, suggestion, expiring_hits = recipe_bundle
        score = 0.56 + (0.12 if suggestion.can_make_now else 0.0) + (0.08 if recipe.suitable_when_tired and context["quick_mode"] else 0.0)
        score += 0.12 * float(context["profile"].eat_at_home_likelihood)
        if expiring_hits:
            score += 0.1
        confidence = 0.82 if suggestion.can_make_now else 0.58
        return {
            "intervention_type": "cook_now",
            "score": min(score, 0.98),
            "confidence": confidence,
            "reason_codes": ["meal_window", "ready_to_cook"],
            "thread_key": f"cook:{recipe.id}",
            "recommended_action": f"Cook {recipe.name}",
            "recipe_id": recipe.id,
            "recipe_name": recipe.name,
            "draft_items": [],
            "metadata": {"expiring_items": expiring_hits, "urgent": bool(expiring_hits)},
        }

    def _expiring_candidate(self, context: DecisionContext) -> InterventionCandidate | None:
        expiring = context["expiring"]
        if not expiring or self._has_state(context, "not_home"):
            return None
        recipe_bundle = self._best_recipe(context, require_ready=True, prefer_expiring=True)
        expiring_names = [item.name for item in expiring]
        if recipe_bundle is None:
            return {
                "intervention_type": "use_expiring_items",
                "score": 0.72,
                "confidence": 0.7,
                "reason_codes": ["expiring_items", "no_ready_recipe"],
                "thread_key": "expire:" + ",".join(sorted(name.lower() for name in expiring_names[:3])),
                "recommended_action": "Use expiring items",
                "recipe_id": None,
                "recipe_name": None,
                "draft_items": [],
                "metadata": {"expiring_items": expiring_names, "urgent": True},
            }
        recipe, _, expiring_hits = recipe_bundle
        return {
            "intervention_type": "use_expiring_items",
            "score": 0.78,
            "confidence": 0.84,
            "reason_codes": ["expiring_items", "matching_recipe"],
            "thread_key": "expire:" + ",".join(sorted(name.lower() for name in expiring_hits)),
            "recommended_action": f"Use {', '.join(expiring_hits[:2])} in {recipe.name}",
            "recipe_id": recipe.id,
            "recipe_name": recipe.name,
            "draft_items": [],
            "metadata": {"expiring_items": expiring_hits, "urgent": True},
        }

    def _pickup_candidate(self, context: DecisionContext) -> InterventionCandidate | None:
        essentials = {value.lower() for value in context["preferences"].essentials_items}
        low_essentials = [item for item in context["low_stock"] if item.name.lower() in essentials]
        if not low_essentials:
            return None
        if not (
            self._has_state(context, "commuting")
            or self._in_window(context["now"], context["preferences"].meal_window_start, context["preferences"].meal_window_end)
        ):
            return None
        lines = [
            GroceryLine(
                name=item.name,
                quantity=round(max(item.min_desired_quantity - item.quantity, 1.0), 2),
                unit=item.unit,
                reason="low stock essential",
            )
            for item in low_essentials[:3]
        ]
        score = 0.5 + min(len(lines) * 0.08, 0.2)
        if self._has_state(context, "commuting"):
            score += 0.14
        return {
            "intervention_type": "buy_on_way_home",
            "score": min(score, 0.95),
            "confidence": 0.78,
            "reason_codes": ["low_stock_essentials"],
            "thread_key": "pickup:" + ",".join(sorted(line.name.lower() for line in lines)),
            "recommended_action": "Pick up " + " and ".join(line.name.lower() for line in lines[:2]),
            "recipe_id": None,
            "recipe_name": None,
            "draft_items": lines,
            "metadata": {"urgent": len(lines) >= 2},
        }

    def _late_night_candidate(self, context: DecisionContext) -> InterventionCandidate | None:
        if self._has_state(context, "not_home"):
            return None
        if not self._in_window(
            context["now"],
            context["preferences"].late_night_window_start,
            context["preferences"].late_night_window_end,
        ):
            return None
        recipe_bundle = self._best_recipe(context, require_ready=True, prefer_expiring=False, late_night=True)
        if recipe_bundle is None:
            return None
        recipe, suggestion, _ = recipe_bundle
        score = 0.62 + (0.1 if context["quick_mode"] else 0.0) + 0.1 * float(context["profile"].quick_food_bias)
        confidence = 0.8 if suggestion.can_make_now else 0.55
        return {
            "intervention_type": "late_night_rescue",
            "score": min(score, 0.96),
            "confidence": confidence,
            "reason_codes": ["late_night_window", "quick_option"],
            "thread_key": f"late:{recipe.id}",
            "recommended_action": f"Make {recipe.name}",
            "recipe_id": recipe.id,
            "recipe_name": recipe.name,
            "draft_items": [],
            "metadata": {"urgent": False},
        }

    def _best_recipe(
        self,
        context: DecisionContext,
        *,
        require_ready: bool,
        prefer_expiring: bool,
        late_night: bool = False,
    ) -> tuple[Recipe, RecipeSuggestion, list[str]] | None:
        expiring_names = {item.name.lower() for item in context["expiring"]}
        ranked: list[tuple[tuple[int | float, ...], Recipe, RecipeSuggestion, list[str]]] = []
        for suggestion in context["suggestions"]:
            recipe = context["recipes"].get(suggestion.recipe_id)
            if recipe is None:
                continue
            if self._dietary_blocked(recipe, context["preferences"]):
                continue
            if recipe.prep_minutes > int(context["effective_prep_minutes"]):
                continue
            if require_ready and not suggestion.can_make_now:
                continue
            if late_night and recipe.step_count > 4:
                continue
            expiring_hits = [
                ingredient.name
                for ingredient in recipe.ingredients
                if ingredient.name.lower() in expiring_names
            ]
            rank = (
                1 if expiring_hits and prefer_expiring else 0,
                1 if suggestion.can_make_now else 0,
                suggestion.coverage,
                -recipe.prep_minutes,
                -recipe.step_count,
                -recipe.effort_score,
            )
            ranked.append((rank, recipe, suggestion, expiring_hits))
        if not ranked:
            return None
        _, recipe, suggestion, expiring_hits = max(ranked, key=lambda item: item[0])
        return recipe, suggestion, expiring_hits

    def _threshold(self, context: DecisionContext) -> float:
        profile: DecisionProfile = context["profile"]
        preferences: UserPreferences = context["preferences"]
        threshold = profile.user_threshold
        if preferences.mode == "strict":
            threshold -= 0.08
        elif preferences.mode == "silent":
            threshold += 0.22
        if preferences.notification_frequency == "quiet":
            threshold += 0.08
        elif preferences.notification_frequency == "active":
            threshold -= 0.05
        return max(0.35, min(threshold, 0.95))

    @staticmethod
    def _next_sequence_index(candidate: InterventionCandidate, latest: AssistantIntervention | None) -> int:
        if latest is None or latest.status in DecisionEngine.RESOLVED_STATUSES:
            return 1
        if latest.context_hash == DecisionEngine._build_context_hash(candidate):
            return min(latest.sequence_index + 1, 4)
        return 1

    def _cooldown_active(self, latest: AssistantIntervention | None, context: DecisionContext, context_hash: str) -> bool:
        if latest is None or latest.context_hash != context_hash:
            return False
        base_minutes = 90
        if context["preferences"].notification_frequency == "quiet":
            base_minutes = 180
        elif context["preferences"].notification_frequency == "active":
            base_minutes = 45
        return (utc_now() - ensure_utc(latest.sent_at)) < timedelta(minutes=base_minutes)

    def _llm_select_candidate(
        self,
        context: DecisionContext,
        candidates: list[InterventionCandidate],
    ) -> InterventionCandidate | None:
        if self.llm_service is None or not getattr(self.llm_service, "is_configured", lambda: False)():
            return None
        if not candidates:
            return None
        try:
            registry = ""
            if getattr(self.llm_service, "mcp_tool_service", None) is not None:
                registry = str(self.llm_service.mcp_tool_service.prompt_tool_registry())
            payload = {
                "model": getattr(self.llm_service.settings, "llm_model", "gpt-5.1-mini"),
                "temperature": 0.2,
                "input": [
                    {
                        "role": "user",
                        "content": (
                            "Select exactly one intervention candidate as JSON with keys "
                            "thread_key (string), confidence (0-1), rationale (string).\n"
                            f"Current local time: {context['now'].isoformat()}\n"
                            f"Mode: {context['preferences'].mode}\n"
                            f"Notification frequency: {context['preferences'].notification_frequency}\n"
                            f"Quick mode: {context['quick_mode']}\n"
                            f"Candidates: {json.dumps([self._candidate_for_llm(item) for item in candidates], ensure_ascii=False)}\n"
                            f"MCP registry (reference only):\n{registry}\n"
                            "Return JSON only."
                        ),
                    }
                ],
            }
            response = self.llm_service.create_response(payload)
            parsed = self._extract_json_object(self._extract_response_text(response))
            thread_key = str(parsed.get("thread_key") or "").strip()
            if not thread_key:
                return None
            selected = next((candidate for candidate in candidates if candidate["thread_key"] == thread_key), None)
            if selected is None:
                return None
            llm_conf = parsed.get("confidence")
            if isinstance(llm_conf, (int, float)):
                selected["confidence"] = self._clamp(float(llm_conf))
            add_event(
                name="decision_llm_candidate_selected",
                detail={"thread_key": selected["thread_key"], "score": selected["score"], "confidence": selected["confidence"]},
            )
            return selected
        except Exception as exc:
            add_event(
                name="decision_llm_candidate_fallback",
                detail={"reason": str(exc)},
            )
            return None

    def _generate_message(self, candidate: InterventionCandidate, context: DecisionContext, sequence_index: int) -> str:
        fallback = self._format_message(candidate, context, sequence_index)
        if self.llm_service is None or not getattr(self.llm_service, "is_configured", lambda: False)():
            return fallback
        try:
            payload = {
                "model": getattr(self.llm_service.settings, "llm_model", "gpt-5.1-mini"),
                "temperature": 0.3,
                "input": [
                    {
                        "role": "user",
                        "content": (
                            "Rewrite this intervention message in concise plain text for Telegram. "
                            "Keep it factual, <= 3 lines, and avoid markdown.\n"
                            f"Candidate: {json.dumps(self._candidate_for_llm(candidate), ensure_ascii=False)}\n"
                            f"Sequence index: {sequence_index}\n"
                            f"Current mode: {context['preferences'].mode}\n"
                            f"Fallback message: {fallback}\n"
                            "Return JSON only: {\"message\": \"...\"}."
                        ),
                    }
                ],
            }
            response = self.llm_service.create_response(payload)
            parsed = self._extract_json_object(self._extract_response_text(response))
            generated = str(parsed.get("message") or "").strip()
            sanitized = self._sanitize_message(generated)
            if not sanitized:
                return fallback
            return sanitized
        except Exception:
            return fallback

    @staticmethod
    def _candidate_for_llm(candidate: InterventionCandidate) -> dict[str, object]:
        return {
            "thread_key": candidate["thread_key"],
            "intervention_type": candidate["intervention_type"],
            "score": candidate["score"],
            "confidence": candidate["confidence"],
            "recommended_action": candidate["recommended_action"],
            "recipe_name": candidate["recipe_name"],
            "reason_codes": candidate["reason_codes"],
            "draft_items": [item.model_dump(mode="json") for item in candidate["draft_items"]],
            "metadata": dict(candidate["metadata"]),
        }

    @staticmethod
    def _extract_response_text(response_payload: dict[str, object]) -> str:
        output_text = response_payload.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()
        output = response_payload.get("output")
        if isinstance(output, list):
            texts: list[str] = []
            for item in output:
                if not isinstance(item, dict) or item.get("type") != "message":
                    continue
                content = item.get("content")
                if not isinstance(content, list):
                    continue
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str):
                        texts.append(str(part["text"]))
            if texts:
                return "\n".join(texts).strip()
        raise RuntimeError("Decision LLM response did not contain output text.")

    @staticmethod
    def _extract_json_object(raw: str) -> dict[str, object]:
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?", "", text).strip()
            text = re.sub(r"```$", "", text).strip()
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            raise ValueError("No JSON object found in LLM response.")
        parsed = json.loads(match.group(0))
        if not isinstance(parsed, dict):
            raise ValueError("LLM JSON response is not an object.")
        return parsed

    @staticmethod
    def _sanitize_message(text: str) -> str:
        if not text:
            return ""
        cleaned = text.replace("*", "").replace("`", "").strip()
        lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
        if not lines:
            return ""
        return "\n".join(lines[:3])[:400]

    def _format_message(self, candidate: InterventionCandidate, context: DecisionContext, sequence_index: int) -> str:
        intervention_type = candidate["intervention_type"]
        recipe_name = candidate.get("recipe_name")
        if intervention_type == "cook_now":
            if sequence_index == 1:
                return f"You have a quick dinner ready: {recipe_name}. It fits tonight without extra shopping."
            if sequence_index == 2:
                return f"You still have a low-effort dinner sitting there: {recipe_name}. It is still the cleanest option tonight."
            return f"I have nudged {recipe_name} a couple of times because it still fits the fridge state. Want me to stop pushing this thread for a few days?"

        if intervention_type == "use_expiring_items":
            expiring_items = candidate["metadata"].get("expiring_items", [])
            names = ", ".join(expiring_items[:3])
            if recipe_name:
                if sequence_index == 1:
                    return f"{names} need attention soon. Use them in {recipe_name} tonight."
                if sequence_index == 2:
                    return f"{names} are still sitting there. {recipe_name} is still the easiest way to use them."
                return f"{names} have been hanging around for a while. Should I stop reminding you about them unless they get worse?"
            return f"{names} need to be used soon. I do not have a clean ready-to-cook match right now."

        if intervention_type == "buy_on_way_home":
            draft_names = [item.name.lower() for item in candidate["draft_items"]]
            if sequence_index == 1:
                return f"Pick up {' and '.join(draft_names[:2])} on the way home. That keeps dinner options easy tonight."
            if sequence_index == 2:
                return f"You are still light on {' and '.join(draft_names[:2])}. Grabbing them on the way home would remove friction later."
            return f"I have mentioned {' and '.join(draft_names[:2])} a few times. Want me to mute this pickup reminder for a few days?"

        if intervention_type == "late_night_rescue":
            return f"If you get home late, go with {recipe_name}. It is the lowest-effort option in the fridge right now."

        return "No intervention selected."

    def _build_quick_actions(self, intervention: AssistantIntervention) -> list[dict[str, str]]:
        actions = [
            {"label": "Cook this", "action": "cook"},
            {"label": "Show easier option", "action": "easier"},
            {"label": "Ignore tonight", "action": "ignore"},
        ]
        if intervention.draft_items:
            actions.insert(1, {"label": "Draft shopping list", "action": "draft"})
        if intervention.decision_type in {"cook_now", "use_expiring_items", "late_night_rescue"}:
            actions.append({"label": "Not home", "action": "not_home"})
            actions.append({"label": "Ordered food", "action": "ordered_food"})
        return [
            {"label": action["label"], "callback_data": f"fm:{action['action']}:{intervention.id}"}
            for action in actions
        ]

    def _draft_items(self, items: list[GroceryLine]) -> int:
        if not items:
            return 0

        def mutator(state: SharedContext) -> dict[str, object]:
            existing = {(line.name.lower(), line.reason.lower()) for line in state.pending_grocery_list}
            added = 0
            for item in items:
                key = (item.name.lower(), item.reason.lower())
                if key in existing:
                    continue
                state.pending_grocery_list.append(item)
                existing.add(key)
                added += 1
            return {"added_count": added}

        updated = self.store.update(
            agent="decision_engine",
            action="draft_shopping_list",
            summary="Drafted shopping items from assistant intervention.",
            mutator=mutator,
        )
        return int(updated.recent_events[0].changes.get("added_count", 0))

    def _apply_temporary_state(self, user_id: str, intent: OverrideIntent) -> TemporaryStateOverride:
        expires_at = self._temporary_state_expiry(intent)
        return self.store.set_temporary_state(
            user_id,
            state=intent.target,
            value=intent.value,
            expires_at=expires_at,
            source="telegram",
            note=intent.source_text,
        )

    def _apply_preference_intent(self, user_id: str, intent: OverrideIntent) -> UserPreferences:
        preferences = self.store.user_preferences(user_id)
        if intent.target == "max_prep_minutes":
            minutes = max(1, min(240, int(intent.value)))
            return self.store.set_user_preferences(user_id, max_prep_minutes=minutes)

        if intent.target == "dietary_preferences":
            existing = {value.lower(): value for value in preferences.dietary_preferences}
            existing[intent.value.lower()] = intent.value
            return self.store.set_user_preferences(user_id, dietary_preferences=list(existing.values()))

        if intent.target == "meal_window":
            start = self._parse_clock(preferences.meal_window_start)
            end = self._parse_clock(preferences.meal_window_end)
            shifted_start = (datetime.combine(singapore_now().date(), start) - timedelta(hours=1)).time()
            shifted_end = (datetime.combine(singapore_now().date(), end) - timedelta(hours=1)).time()
            return self.store.set_user_preferences(
                user_id,
                meal_window_start=shifted_start.strftime("%H:%M"),
                meal_window_end=shifted_end.strftime("%H:%M"),
            )

        if intent.target == "late_night_disabled":
            return self.store.set_user_preferences(
                user_id,
                late_night_window_start="00:00",
                late_night_window_end="00:00",
                notification_frequency="quiet",
            )

        if intent.target == "mode":
            return self.store.set_user_preferences(user_id, mode=intent.value.lower())

        return preferences

    def _profile_feedback_updates(
        self,
        profile: DecisionProfile,
        intervention: AssistantIntervention | None,
        *,
        status: str,
        action: str | None,
    ) -> dict[str, float]:
        ignore_rate = profile.ignore_nudge_rate
        threshold = profile.user_threshold
        quick_food_bias = profile.quick_food_bias
        eat_at_home = profile.eat_at_home_likelihood
        healthy_acceptance = profile.healthy_meal_acceptance_score
        stress_signal = profile.stress_eating_signal

        if status in {"ignored", "dismissed"}:
            ignore_rate += 0.08
            threshold += 0.04
        if status in {"clicked", "completed"}:
            ignore_rate -= 0.05
            threshold -= 0.03

        if intervention and intervention.decision_type in {"cook_now", "use_expiring_items", "late_night_rescue"}:
            if status in {"clicked", "completed"}:
                eat_at_home += 0.08
                healthy_acceptance += 0.03 if intervention.decision_type == "use_expiring_items" else 0.0
            if status == "ignored":
                eat_at_home -= 0.03

        if intervention and intervention.decision_type == "late_night_rescue" and status in {"clicked", "completed"}:
            quick_food_bias += 0.04

        if action == "ordered_food":
            quick_food_bias += 0.1
            stress_signal += 0.05
            eat_at_home -= 0.04

        return {
            "ignore_nudge_rate": self._clamp(ignore_rate),
            "healthy_meal_acceptance_score": self._clamp(healthy_acceptance),
            "quick_food_bias": self._clamp(quick_food_bias),
            "eat_at_home_likelihood": self._clamp(eat_at_home),
            "stress_eating_signal": self._clamp(stress_signal),
            "user_threshold": self._clamp(threshold, lower=0.35, upper=0.95),
        }

    @staticmethod
    def _temporary_state_message(state: TemporaryStateOverride) -> str:
        if state.state == "not_home":
            return "Noted. I’ll stop home-meal nudges for tonight."
        if state.state == "commuting":
            return "Noted. I’ll bias toward pickup reminders while you are commuting."
        return f"Noted. I’ll treat you as {state.state.replace('_', ' ')} for now."

    @staticmethod
    def _preference_message(intent: OverrideIntent, preferences: UserPreferences) -> str:
        if intent.target == "max_prep_minutes":
            return f"Okay. I’ll keep meal suggestions around {preferences.max_prep_minutes} minutes."
        if intent.target == "dietary_preferences":
            return f"Okay. I’ll keep {intent.value} in mind."
        if intent.target == "late_night_disabled":
            return "Okay. I’ll stop pushing late-night messages."
        if intent.target == "meal_window":
            return "Okay. I’ll move dinner nudges earlier."
        if intent.target == "mode":
            return f"Mode set to {preferences.mode}."
        return "Updated."

    @staticmethod
    def _build_context_hash(candidate: InterventionCandidate) -> str:
        raw = "|".join(
            [
                str(candidate.get("intervention_type") or ""),
                str(candidate.get("recommended_action") or ""),
                ",".join(item.name.lower() for item in candidate.get("draft_items", [])),
                ",".join(str(value) for value in candidate.get("metadata", {}).get("expiring_items", [])),
            ]
        )
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _parse_clock(raw: str) -> time:
        hour_text, minute_text = raw.split(":", 1)
        return time(hour=int(hour_text), minute=int(minute_text))

    @classmethod
    def _in_window(cls, now: datetime, start_raw: str, end_raw: str) -> bool:
        if start_raw == end_raw:
            return False
        start = cls._parse_clock(start_raw)
        end = cls._parse_clock(end_raw)
        current = now.time()
        if start <= end:
            return start <= current <= end
        return current >= start or current <= end

    @staticmethod
    def _clamp(value: float, *, lower: float = 0.0, upper: float = 1.0) -> float:
        return max(lower, min(value, upper))

    @staticmethod
    def _has_state(context: DecisionContext, state_name: str) -> bool:
        return state_name in context["state_map"]

    def _temporary_state_expiry(self, intent: OverrideIntent) -> datetime:
        now = singapore_now()
        if intent.target == "not_home":
            midnight = datetime.combine(now.date() + timedelta(days=1), time(0, 0), tzinfo=now.tzinfo)
            return midnight
        hours = intent.duration_hours or {
            "tired": 24,
            "busy": 6,
            "stressed": 24,
            "commuting": 3,
            "at_home": 6,
        }.get(intent.target, 24)
        return now + timedelta(hours=hours)

    def _dietary_blocked(self, recipe: Recipe, preferences: UserPreferences) -> bool:
        lowered_prefs = {value.lower() for value in preferences.dietary_preferences}
        if "avoid dairy" in lowered_prefs or "no dairy" in lowered_prefs:
            dairy_terms = {value.lower() for value in preferences.dairy_items}
            for ingredient in recipe.ingredients:
                if ingredient.name.lower() in dairy_terms:
                    return True
        return False
