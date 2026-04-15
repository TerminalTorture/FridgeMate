from __future__ import annotations

DEFAULT_SEARCH_MODEL = "gpt-4o-mini-search-preview"
ALLOWED_SEARCH_MODELS = (
    "gpt-5-search-api",
    "gpt-4o-search-preview",
    "gpt-4o-mini-search-preview",
)


def is_valid_search_model(value: str) -> bool:
    return value in ALLOWED_SEARCH_MODELS

