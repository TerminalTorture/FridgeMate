from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar, Token
from pathlib import Path
from time import perf_counter
from typing import Any, Iterator
from uuid import uuid4

from app.core.sanitization import redact_value
from app.core.settings import get_settings
from app.core.time_utils import utc_now

_TRACE_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar("trace_context", default=None)


def _enabled() -> bool:
    return get_settings().trace_mode


def _now_iso() -> str:
    return utc_now().isoformat()


def has_active_trace() -> bool:
    return _TRACE_CONTEXT.get() is not None


def current_trace_id() -> str | None:
    context = _TRACE_CONTEXT.get()
    if context is None:
        return None
    value = context.get("request_id")
    return str(value) if value else None


def begin_trace(*, channel: str, request_id: str | None = None, user_id: str | None = None, metadata: dict[str, Any] | None = None) -> Token:
    record = {
        "request_id": request_id or uuid4().hex,
        "channel": channel,
        "started_at": _now_iso(),
        "started_monotonic": perf_counter(),
        "user_id": user_id or "",
        "metadata": redact_value(metadata or {}),
        "memory_files": [],
        "prompt_sections": [],
        "json_records_consulted": [],
        "tools_exposed": [],
        "tools_called": [],
        "decision_rules": [],
        "events": [],
        "final": {},
    }
    return _TRACE_CONTEXT.set(record)


def update_trace_metadata(**kwargs: Any) -> None:
    context = _TRACE_CONTEXT.get()
    if context is None:
        return
    metadata = context.setdefault("metadata", {})
    for key, value in kwargs.items():
        metadata[str(key)] = redact_value(value)


def add_event(*, name: str, detail: dict[str, Any] | None = None) -> None:
    context = _TRACE_CONTEXT.get()
    if context is None:
        return
    context["events"].append(
        {
            "timestamp": _now_iso(),
            "name": name,
            "detail": redact_value(detail or {}),
        }
    )


def record_memory_file(*, path: str, section: str, source: str, chars: int, injected: bool = True, exists: bool = True) -> None:
    context = _TRACE_CONTEXT.get()
    if context is None:
        return
    context["memory_files"].append(
        {
            "timestamp": _now_iso(),
            "path": path,
            "section": section,
            "source": source,
            "exists": exists,
            "chars": chars,
            "injected": injected,
        }
    )


def record_prompt_section(*, section: str, chars: int) -> None:
    context = _TRACE_CONTEXT.get()
    if context is None:
        return
    context["prompt_sections"].append(
        {
            "timestamp": _now_iso(),
            "section": section,
            "chars": chars,
        }
    )


def record_json_consult(*, name: str, path: str, operation: str, records: int | None = None, chars: int | None = None) -> None:
    context = _TRACE_CONTEXT.get()
    if context is None:
        return
    payload: dict[str, Any] = {
        "timestamp": _now_iso(),
        "name": name,
        "path": path,
        "operation": operation,
    }
    if records is not None:
        payload["records"] = records
    if chars is not None:
        payload["chars"] = chars
    context["json_records_consulted"].append(payload)


def record_tools_exposed(tools: list[str]) -> None:
    context = _TRACE_CONTEXT.get()
    if context is None:
        return
    context["tools_exposed"] = sorted({*context.get("tools_exposed", []), *tools})


def record_tool_call(*, name: str, arguments: dict[str, Any], result: dict[str, Any] | None = None) -> None:
    context = _TRACE_CONTEXT.get()
    if context is None:
        return
    context["tools_called"].append(
        {
            "timestamp": _now_iso(),
            "name": name,
            "arguments": redact_value(arguments),
            "result": redact_value(result or {}),
        }
    )


def record_decision_rule(*, rule: str, triggered: bool, detail: dict[str, Any] | None = None) -> None:
    context = _TRACE_CONTEXT.get()
    if context is None:
        return
    context["decision_rules"].append(
        {
            "timestamp": _now_iso(),
            "rule": rule,
            "triggered": triggered,
            "detail": redact_value(detail or {}),
        }
    )


def finish_trace(*, response_summary: str | None = None, error: str | None = None) -> dict[str, Any] | None:
    context = _TRACE_CONTEXT.get()
    if context is None:
        return None

    elapsed_ms = round((perf_counter() - float(context["started_monotonic"])) * 1000, 2)
    memory_sources = context.get("memory_files", [])
    consulted = context.get("json_records_consulted", [])
    tools_called = context.get("tools_called", [])
    decision_rules = context.get("decision_rules", [])
    section_scores: dict[str, int] = {}
    for item in memory_sources:
        section = str(item.get("section") or "unknown")
        section_scores[section] = section_scores.get(section, 0) + int(item.get("chars") or 0)
    top_sections = sorted(section_scores.items(), key=lambda x: x[1], reverse=True)[:5]

    influence_summary = {
        "top_memory_sections_by_chars": [{"section": section, "chars": chars} for section, chars in top_sections],
        "json_sources_consulted": sorted({str(item.get("name") or "") for item in consulted if item.get("name")}),
        "tools_called": [str(item.get("name") or "") for item in tools_called],
        "triggered_decision_rules": [
            str(item.get("rule") or "") for item in decision_rules if bool(item.get("triggered"))
        ],
    }

    context["finished_at"] = _now_iso()
    context["latency_ms"] = elapsed_ms
    context["final"] = {
        "response_summary": response_summary or "",
        "error": error or "",
        "influence_summary": influence_summary,
    }
    context.pop("started_monotonic", None)

    if _enabled():
        settings = get_settings()
        trace_root = Path(settings.trace_log_path)
        trace_root.mkdir(parents=True, exist_ok=True)
        timestamp = utc_now().strftime("%Y%m%dT%H%M%S%fZ")
        file_path = trace_root / f"{timestamp}_{context['request_id']}.json"
        file_path.write_text(json.dumps(redact_value(context), indent=2), encoding="utf-8")
        context["trace_file"] = str(file_path)

    return context


def clear_trace(token: Token | None = None) -> None:
    if token is not None:
        _TRACE_CONTEXT.reset(token)
        return
    _TRACE_CONTEXT.set(None)


@contextmanager
def trace_scope(*, channel: str, request_id: str | None = None, user_id: str | None = None, metadata: dict[str, Any] | None = None) -> Iterator[dict[str, Any] | None]:
    if not _enabled():
        yield None
        return

    if has_active_trace():
        yield _TRACE_CONTEXT.get()
        return

    token = begin_trace(channel=channel, request_id=request_id, user_id=user_id, metadata=metadata)
    error_text: str | None = None
    try:
        yield _TRACE_CONTEXT.get()
    except Exception as exc:
        error_text = str(exc)
        raise
    finally:
        finish_trace(error=error_text)
        clear_trace(token)
