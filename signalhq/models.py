"""SQLAlchemy ORM models. Table shapes follow CLAUDE.md §5.

Additions to the brief, each for a stated reason:
* ``feedback_items.meta_json``: connector metadata (speaker for calls, planted labels for
  the synthetic set, needed for the adjusted Rand index).
* ``enrichments.product_area_raw`` and ``quote_verified``: keep Spark's original answer and
  whether its quote was found verbatim, so normalisation and quote checks are auditable.
* ``jobs.dedupe_key`` (+ partial unique index), ``started_at``, ``finished_at``: singleton
  jobs (one pending re-cluster at a time) and per-stage latency measurement.
* ``llm_cache`` and ``kv`` tables: the gateway's response cache and the replay clock.
* ``eval_labels.content_hash``: human labels survive a replay reset (new item ids).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_ref: Mapped[str] = mapped_column(String(128), unique=True)
    plan: Mapped[str] = mapped_column(String(32))
    mrr_cents: Mapped[int] = mapped_column(Integer)
    simulated: Mapped[bool] = mapped_column(Boolean, default=True)


class FeedbackItem(Base):
    __tablename__ = "feedback_items"
    # AUTOINCREMENT: ids are never reused after a replay reset, so a worker still holding
    # an old item id can never write onto a new item that happens to get the same id.
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True)
    state: Mapped[str] = mapped_column(String(16), index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class Enrichment(Base):
    __tablename__ = "enrichments"

    item_id: Mapped[int] = mapped_column(ForeignKey("feedback_items.id"), primary_key=True)
    type: Mapped[str] = mapped_column(String(32), index=True)
    product_area: Mapped[str] = mapped_column(String(64), index=True)
    product_area_raw: Mapped[str] = mapped_column(String(128))
    severity: Mapped[str] = mapped_column(String(16))
    sentiment: Mapped[str] = mapped_column(String(16))
    churn_signal: Mapped[bool] = mapped_column(Boolean)
    key_quote: Mapped[str] = mapped_column(Text)
    quote_verified: Mapped[bool] = mapped_column(Boolean)
    language: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(32))
    raw_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class Embedding(Base):
    __tablename__ = "embeddings"

    item_id: Mapped[int] = mapped_column(ForeignKey("feedback_items.id"), primary_key=True)
    model: Mapped[str] = mapped_column(String(128))
    dim: Mapped[int] = mapped_column(Integer)
    vector: Mapped[bytes] = mapped_column(LargeBinary)


class Theme(Base):
    __tablename__ = "themes"
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    centroid: Mapped[bytes] = mapped_column(LargeBinary)
    size: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)
    named_at_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    merged_into: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ThemeMember(Base):
    __tablename__ = "theme_members"
    __table_args__ = (Index("uq_theme_members_item", "item_id", unique=True),)

    theme_id: Mapped[int] = mapped_column(ForeignKey("themes.id"), primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("feedback_items.id"), primary_key=True)
    similarity: Mapped[float] = mapped_column(Float)
    assigned_at: Mapped[datetime] = mapped_column(DateTime)


class ThemeScore(Base):
    __tablename__ = "theme_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    theme_id: Mapped[int] = mapped_column(ForeignKey("themes.id"), index=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime)
    window_end: Mapped[datetime] = mapped_column(DateTime)
    mentions_recent: Mapped[int] = mapped_column(Integer)
    mentions_prev: Mapped[int] = mapped_column(Integer)
    growth: Mapped[float] = mapped_column(Float)
    revenue_at_risk_cents: Mapped[int] = mapped_column(Integer)
    severity_weight: Mapped[float] = mapped_column(Float)
    score: Mapped[float] = mapped_column(Float)
    inputs_json: Mapped[str] = mapped_column(Text)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_claim", "state", "run_after"),
        # At most one pending job per dedupe key (e.g. one queued re-cluster at a time).
        Index(
            "uq_jobs_dedupe_pending",
            "dedupe_key",
            unique=True,
            sqlite_where=text("state IN ('queued', 'running', 'failed')"),
        ),
        {"sqlite_autoincrement": True},  # job ids are never reused (fencing relies on it)
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(32))
    payload_json: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(16))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer)
    run_after: Mapped[datetime] = mapped_column(DateTime)
    locked_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class LLMCall(Base):
    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model: Mapped[str] = mapped_column(String(64))
    purpose: Mapped[str] = mapped_column(String(32), index=True)
    prompt_hash: Mapped[str] = mapped_column(String(64))
    cache_hit: Mapped[bool] = mapped_column(Boolean)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(32))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class LLMCache(Base):
    __tablename__ = "llm_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    model: Mapped[str] = mapped_column(String(64))
    purpose: Mapped[str] = mapped_column(String(32))
    response_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class EvalLabel(Base):
    """Human labels. Keyed by item id as in the brief, plus the item's content hash so the
    labels survive a replay reset that re-creates items with new ids."""

    __tablename__ = "eval_labels"

    item_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    type: Mapped[str] = mapped_column(String(32))
    product_area: Mapped[str] = mapped_column(String(64))
    theme_label: Mapped[str] = mapped_column(String(200))
    labeller: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime)


class KV(Base):
    """Small key-value store for process-wide state such as the replay clock."""

    __tablename__ = "kv"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
