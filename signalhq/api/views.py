"""View builders: assemble API responses from repositories. No SQL here."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from signalhq.config import SIMULATED_REVENUE_NOTE, SYNTHETIC_DATA_NOTE, Settings
from signalhq.db import utcnow
from signalhq.domain.scoring import FORMULA, ScoreInputs, rank_key, revenue_at_risk_cents
from signalhq.models import ThemeScore
from signalhq.pipeline.score import data_now
from signalhq.repositories import ItemRepo, JobRepo, LedgerRepo, ThemeRepo
from signalhq.repositories.ledger import percentile

SPARK_DAYS = 14
TREND_DAYS = 28
SORTS = ("score", "revenue", "growth", "mentions", "size")


def _inputs(score: ThemeScore | None) -> ScoreInputs | None:
    if score is None:
        return None
    return ScoreInputs(
        window_end=score.window_end,
        window_days=json.loads(score.inputs_json).get("window_days", 7),
        mentions_recent=score.mentions_recent,
        mentions_prev=score.mentions_prev,
        growth=score.growth,
        revenue_at_risk_cents=score.revenue_at_risk_cents,
        at_risk_accounts=json.loads(score.inputs_json).get("at_risk_accounts", 0),
        severity_weight=score.severity_weight,
        score=score.score,
    )


def data_note(s: Session) -> str:
    items = ItemRepo(s)
    synthetic = items.synthetic_count()
    total = items.count()
    if total and synthetic == total:
        return SYNTHETIC_DATA_NOTE
    if synthetic:
        return "Revenue figures are simulated for demonstration; feedback text mixes synthetic sample data and real data."
    return SIMULATED_REVENUE_NOTE


def themes_view(s: Session, sort: str = "score", limit: int = 50) -> list[dict[str, Any]]:
    themes = ThemeRepo(s)
    active = themes.list_active()
    latest = themes.latest_scores()
    now = data_now(s)
    ids = [t.id for t in active]
    sparks = themes.daily_counts(ids, now, SPARK_DAYS) if now and ids else {}
    severities = themes.top_severity(ids) if ids else {}

    rows = []
    for theme in active:
        inputs = _inputs(latest.get(theme.id))
        rows.append((theme, inputs))

    def key(pair: tuple) -> tuple:
        theme, inputs = pair
        if inputs is None:  # not scored yet: after every scored theme
            return (1, 0, theme.id)
        if sort == "score":
            return (0, *rank_key(theme.id, inputs))
        value = {
            "revenue": inputs.revenue_at_risk_cents,
            "growth": inputs.growth,
            "mentions": inputs.mentions_recent,
            "size": theme.size,
        }[sort]
        return (0, -value, theme.id)

    rows.sort(key=key)
    out = []
    for rank, (theme, inputs) in enumerate(rows[:limit], start=1):
        out.append(
            {
                "rank": rank,
                "id": theme.id,
                "name": theme.name or f"Unnamed theme {theme.id}",
                "named": bool(theme.name),
                "summary": theme.summary,
                "size": theme.size,
                "score": None if inputs is None else inputs.score,
                "revenue_at_risk_dollars": None if inputs is None else inputs.revenue_at_risk_cents / 100,
                "at_risk_accounts": None if inputs is None else inputs.at_risk_accounts,
                "mentions_recent": None if inputs is None else inputs.mentions_recent,
                "mentions_prev": None if inputs is None else inputs.mentions_prev,
                "growth": None if inputs is None else inputs.growth,
                "severity_weight": None if inputs is None else inputs.severity_weight,
                "top_severity": severities.get(theme.id),
                "is_new": bool(inputs and inputs.mentions_prev == 0 and inputs.mentions_recent > 0),
                "sparkline": sparks.get(theme.id, []),
            }
        )
    return out


def theme_detail(s: Session, theme_id: int, settings: Settings) -> dict[str, Any] | None:
    themes = ThemeRepo(s)
    theme = themes.get(theme_id)
    if theme is None:
        return None
    latest = themes.latest_scores().get(theme_id)
    inputs = _inputs(latest)
    now = data_now(s)
    trend = themes.daily_counts([theme_id], now, TREND_DAYS)[theme_id] if now else []
    trend_start = (now - timedelta(days=TREND_DAYS - 1)).date() if now else None
    history = [
        {"computed_at": h.computed_at.isoformat(), "window_end": h.window_end.isoformat(), "score": h.score}
        for h in themes.score_history(theme_id)
    ]
    explanation = None
    if inputs is not None:
        d = inputs.to_dict()
        explanation = {
            **d,
            "formula": FORMULA,
            "filled": (
                f"log1p({d['revenue_at_risk_dollars']:.2f}) x sqrt({inputs.growth:.3f}) x "
                f"{inputs.severity_weight:.2f} x log1p({inputs.mentions_recent}) = {inputs.score:.3f}"
            ),
            "window": f"recent = the {inputs.window_days} days up to {inputs.window_end:%Y-%m-%d %H:%M}; "
                      f"previous = the {inputs.window_days} days before that",
        }
    return {
        "id": theme.id,
        "name": theme.name or f"Unnamed theme {theme.id}",
        "summary": theme.summary,
        "status": theme.status,
        "merged_into": theme.merged_into,
        "size": theme.size,
        "named_at_size": theme.named_at_size,
        "created_at": theme.created_at.isoformat(),
        "explanation": explanation,
        "quotes": themes.quotes(theme_id, limit=8),
        "breakdown": themes.breakdown(theme_id),
        "trend": {"start": trend_start.isoformat() if trend_start else None, "daily_counts": trend},
        "score_history": history,
        "revenue_simulated": True,
    }


def summary_view(s: Session, settings: Settings, embedder_name: str) -> dict[str, Any]:
    items = ItemRepo(s)
    themes = ThemeRepo(s)
    states = items.counts_by_state()
    now = data_now(s)
    # Total revenue at risk counts each account once across all themes (no double counting).
    total_rar = 0
    if now:
        start = now - timedelta(days=settings.window_days)
        recent = [m for facts in themes.member_facts().values() for m in facts if start < m.created_at <= now]
        total_rar, _ = revenue_at_risk_cents(recent)
    ledger = LedgerRepo(s).summary()
    return {
        "mode": "offline" if settings.is_offline else "online",
        "model": settings.effective_model,
        "embedder": embedder_name,
        "items_total": items.count(),
        "items_processed": states.get("clustered", 0) + states.get("embedded", 0),
        "items_by_state": states,
        "active_themes": themes.counts_by_status().get("active", 0),
        "revenue_at_risk_dollars": total_rar / 100,
        "revenue_simulated": True,
        "cost_usd": ledger["cost_usd"],
        "budget_usd": settings.budget_usd,
        "data_clock": now.isoformat() if now else None,
        "window_days": settings.window_days,
        "data_note": data_note(s),
        "sources": items.sources(),
    }


def metrics_view(s: Session, settings: Settings, workers_running: bool) -> dict[str, Any]:
    jobs = JobRepo(s)
    durations = jobs.durations_s()
    stage_latency = {
        job_type: {
            "count": len(values),
            "p50_ms": round(1000 * percentile(values, 50), 1),
            "p95_ms": round(1000 * percentile(values, 95), 1),
        }
        for job_type, values in durations.items()
        if values
    }
    count, first, last = jobs.processing_span()
    minutes = (last - first).total_seconds() / 60 if first and last and last > first else None
    ledger = LedgerRepo(s).summary()
    return {
        "queue": jobs.counts(),
        "queue_by_state": jobs.counts_by_state(),
        "dead_jobs": jobs.dead_jobs(10),
        "items_by_state": ItemRepo(s).counts_by_state(),
        "pool_size": ItemRepo(s).pool_size(),
        "stage_latency": stage_latency,
        "throughput_items_per_min": round(count / minutes, 1) if minutes else None,
        "llm": ledger,
        "budget_usd": settings.budget_usd,
        "workers_running": workers_running,
        "measured_at": utcnow().replace(microsecond=0).isoformat(),
    }
