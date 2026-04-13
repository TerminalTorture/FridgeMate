from __future__ import annotations

import json
import re
from dataclasses import dataclass

from app.models.domain import OverrideIntent


@dataclass(frozen=True)
class _PatternRule:
    pattern: re.Pattern[str]
    intent: OverrideIntent


class OverrideParser:
    def __init__(self) -> None:
        self.llm_service = None
        self._rules = self._build_rules()

    def bind_llm_service(self, llm_service) -> None:
        self.llm_service = llm_service

    def parse(self, text: str) -> OverrideIntent | None:
        stripped = text.strip()
        if not stripped:
            return None

        lowered = stripped.lower()
        for rule in self._rules:
            if rule.pattern.search(lowered):
                return rule.intent.model_copy(update={"source_text": stripped})

        minutes_match = re.search(r"\b(\d{1,3})\s*[- ]?minute meals?\b", lowered)
        if minutes_match:
            minutes = max(1, min(240, int(minutes_match.group(1))))
            return OverrideIntent(
                kind="preference",
                target="max_prep_minutes",
                value=str(minutes),
                confidence=0.96,
                source_text=stripped,
            )

        if "remind me earlier" in lowered:
            return OverrideIntent(
                kind="preference_adjustment",
                target="meal_window",
                value="earlier",
                confidence=0.92,
                source_text=stripped,
            )

        if "stop messaging me at night" in lowered or "don't message me at night" in lowered:
            return OverrideIntent(
                kind="preference",
                target="late_night_disabled",
                value="true",
                confidence=0.95,
                source_text=stripped,
            )

        if "give me easier meals this week" in lowered:
            return OverrideIntent(
                kind="preference",
                target="max_prep_minutes",
                value="7",
                duration_hours=24 * 7,
                confidence=0.88,
                source_text=stripped,
            )

        if not self._looks_like_steering_candidate(lowered):
            return None

        if self.llm_service is None or not self.llm_service.is_configured():
            return None
        return self._llm_parse(stripped)

    def _llm_parse(self, text: str) -> OverrideIntent | None:
        prompt = (
            "Extract one user steering intent for a food assistant.\n"
            "Only return JSON with keys: match, kind, target, value, duration_hours, confidence.\n"
            "Supported kinds: temporary_state, preference, preference_adjustment, mode.\n"
            "Supported targets: tired, busy, stressed, commuting, at_home, not_home, "
            "max_prep_minutes, meal_window, late_night_disabled, dietary_preferences, mode.\n"
            "If there is no clear supported steering intent, return {\"match\": false}.\n"
            f"User text: {text}"
        )
        payload = {
            "model": self.llm_service.settings.llm_model,
            "instructions": "Return only valid JSON. Do not add prose.",
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": prompt}],
                }
            ],
        }
        try:
            response = self.llm_service.create_response(payload)
            raw = self.llm_service._extract_output_text(response)
            parsed = json.loads(raw)
        except Exception:
            return None

        if not isinstance(parsed, dict) or not parsed.get("match"):
            return None

        kind = str(parsed.get("kind") or "").strip()
        target = str(parsed.get("target") or "").strip()
        value = str(parsed.get("value") or "").strip()
        if not kind or not target:
            return None

        duration = parsed.get("duration_hours")
        try:
            duration_hours = int(duration) if duration is not None else None
        except (TypeError, ValueError):
            duration_hours = None

        confidence = parsed.get("confidence")
        try:
            confidence_value = float(confidence)
        except (TypeError, ValueError):
            confidence_value = 0.6

        return OverrideIntent(
            kind=kind,
            target=target,
            value=value,
            duration_hours=duration_hours,
            confidence=max(0.0, min(confidence_value, 1.0)),
            source_text=text,
        )

    @staticmethod
    def _build_rules() -> list[_PatternRule]:
        intent = OverrideIntent
        return [
            _PatternRule(
                re.compile(r"\b(i('| a)?m|am)\s+(exhausted|tired|drained|worn out)\b|\bdamn tired\b"),
                intent(kind="temporary_state", target="tired", value="active", duration_hours=24, confidence=0.98),
            ),
            _PatternRule(
                re.compile(r"\b(i('| a)?m|am)\s+busy\b"),
                intent(kind="temporary_state", target="busy", value="active", duration_hours=6, confidence=0.95),
            ),
            _PatternRule(
                re.compile(r"\b(i('| a)?m|am)\s+stressed\b"),
                intent(kind="temporary_state", target="stressed", value="active", duration_hours=24, confidence=0.95),
            ),
            _PatternRule(
                re.compile(r"\b(i('| a)?m|am)\s+commuting\b|\bon my way home\b"),
                intent(kind="temporary_state", target="commuting", value="active", duration_hours=3, confidence=0.95),
            ),
            _PatternRule(
                re.compile(r"\bnot home tonight\b|\bwon't be home tonight\b|\bout tonight\b"),
                intent(kind="temporary_state", target="not_home", value="active", duration_hours=12, confidence=0.98),
            ),
            _PatternRule(
                re.compile(r"\bhome tonight\b|\bat home tonight\b"),
                intent(kind="temporary_state", target="at_home", value="active", duration_hours=6, confidence=0.9),
            ),
            _PatternRule(
                re.compile(r"\bstrict mode\b"),
                intent(kind="mode", target="mode", value="strict", confidence=0.98),
            ),
            _PatternRule(
                re.compile(r"\blazy mode\b"),
                intent(kind="mode", target="mode", value="lazy", confidence=0.98),
            ),
            _PatternRule(
                re.compile(r"\bsilent mode\b|\bbe quiet today\b|\bquiet mode\b"),
                intent(kind="mode", target="mode", value="silent", confidence=0.98),
            ),
            _PatternRule(
                re.compile(r"\bavoiding dairy\b|\bavoid dairy\b|\bno dairy\b"),
                intent(kind="preference", target="dietary_preferences", value="avoid dairy", confidence=0.94),
            ),
        ]

    @staticmethod
    def _looks_like_steering_candidate(lowered: str) -> bool:
        cues = (
            "i'm",
            "i am",
            "am ",
            "mode",
            "remind me",
            "stop messaging",
            "easier meals",
            "minute meals",
            "avoiding",
            "avoid ",
            "not home",
            "home tonight",
            "commuting",
            "tired",
            "busy",
            "stressed",
            "quiet",
        )
        return any(cue in lowered for cue in cues)
