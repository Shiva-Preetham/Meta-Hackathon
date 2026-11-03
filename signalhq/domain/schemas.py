"""Pydantic models and the strict JSON schemas sent to Muse Spark.

The schemas follow the strict structured-output rules in CLAUDE.md §3: plain-object root,
``additionalProperties: false`` on every object, every property in ``required`` and no
``allOf``/``oneOf``. ``tests/test_schemas.py`` checks this mechanically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

FEEDBACK_TYPES: tuple[str, ...] = (
    "bug",
    "feature_request",
    "usability",
    "billing",
    "performance",
    "account_access",
    "praise",
    "question",
    "other",
)
SEVERITIES: tuple[str, ...] = ("low", "medium", "high", "critical")
SENTIMENTS: tuple[str, ...] = ("negative", "neutral", "positive")

# Bump when a schema changes; it is part of the cache key so old answers are not reused.
ENRICH_SCHEMA_VERSION = "enrich-schema-v1"
THEME_SCHEMA_VERSION = "theme-schema-v1"

_ENRICH_PROPERTIES: dict[str, Any] = {
    "type": {"type": "string", "enum": list(FEEDBACK_TYPES)},
    "product_area": {"type": "string"},
    "severity": {"type": "string", "enum": list(SEVERITIES)},
    "sentiment": {"type": "string", "enum": list(SENTIMENTS)},
    "churn_signal": {"type": "boolean"},
    "key_quote": {"type": "string"},
    "language": {"type": "string"},
}

ENRICH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": list(_ENRICH_PROPERTIES),
    "properties": _ENRICH_PROPERTIES,
}

# Micro-batch variant, used by scripts/compare_batching.py for ADR-007.
ENRICH_BATCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", *_ENRICH_PROPERTIES],
                "properties": {"id": {"type": "integer"}, **_ENRICH_PROPERTIES},
            },
        }
    },
}

THEME_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "summary"],
    "properties": {
        "name": {"type": "string"},
        "summary": {"type": "string"},
    },
}


class EnrichmentOut(BaseModel):
    """Validated enrichment as returned by the model (before normalisation)."""

    type: Literal[
        "bug",
        "feature_request",
        "usability",
        "billing",
        "performance",
        "account_access",
        "praise",
        "question",
        "other",
    ]
    product_area: str
    severity: Literal["low", "medium", "high", "critical"]
    sentiment: Literal["negative", "neutral", "positive"]
    churn_signal: bool
    key_quote: str
    language: str


class ThemeNameOut(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    summary: str


class FeedbackIn(BaseModel):
    """Webhook payload for ``POST /api/feedback``."""

    text: str = Field(min_length=1, max_length=20_000)
    source: str = Field(default="webhook", max_length=32)
    external_id: str | None = Field(default=None, max_length=128)
    account_ref: str | None = Field(default=None, max_length=128)
    created_at: datetime | None = None


@dataclass(frozen=True)
class RawFeedback:
    """One piece of feedback as produced by a connector, before persistence."""

    source: str
    text: str
    created_at: datetime
    external_id: str | None = None
    account_ref: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
