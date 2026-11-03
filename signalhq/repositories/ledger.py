"""Cost and latency ledger plus the gateway's response cache."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from signalhq.models import LLMCache, LLMCall


def percentile(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile; None for an empty list."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, int(round(q / 100 * len(ordered) + 0.5)))
    return ordered[min(rank, len(ordered)) - 1]


class LedgerRepo:
    def __init__(self, session: Session) -> None:
        self.s = session

    # --- ledger -------------------------------------------------------------------------
    def record(
        self,
        *,
        model: str,
        purpose: str,
        prompt_hash: str,
        cache_hit: bool,
        status: str,
        now: datetime,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cached_tokens: int = 0,
        cost_usd: float = 0.0,
        latency_ms: float = 0.0,
        error: str | None = None,
    ) -> None:
        self.s.add(
            LLMCall(
                model=model,
                purpose=purpose,
                prompt_hash=prompt_hash,
                cache_hit=cache_hit,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_tokens=cached_tokens,
                cost_usd=cost_usd,
                latency_ms=latency_ms,
                status=status,
                error=error[:2000] if error else None,
                created_at=now,
            )
        )

    def total_cost(self) -> float:
        return float(self.s.execute(select(func.coalesce(func.sum(LLMCall.cost_usd), 0.0))).scalar_one())

    def summary(self) -> dict[str, Any]:
        calls = self.s.execute(
            select(LLMCall.purpose, LLMCall.model, LLMCall.cache_hit, LLMCall.status, LLMCall.latency_ms,
                   LLMCall.cost_usd, LLMCall.input_tokens, LLMCall.output_tokens)
        ).all()
        by_purpose: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"calls": 0, "cache_hits": 0, "errors": 0, "cost_usd": 0.0,
                     "input_tokens": 0, "output_tokens": 0, "latencies": []}
        )
        models: set[str] = set()
        for purpose, model, hit, status, latency, cost, tin, tout in calls:
            p = by_purpose[purpose]
            models.add(model)
            p["calls"] += 1
            p["cache_hits"] += int(hit)
            p["errors"] += int(status != "ok")
            p["cost_usd"] += cost
            p["input_tokens"] += tin
            p["output_tokens"] += tout
            if status == "ok" and not hit:
                p["latencies"].append(latency)
        out: dict[str, Any] = {"models": sorted(models), "purposes": {}}
        total_calls = total_hits = 0
        total_cost = 0.0
        for purpose, p in by_purpose.items():
            lat = p.pop("latencies")
            p["p50_latency_ms"] = percentile(lat, 50)
            p["p95_latency_ms"] = percentile(lat, 95)
            p["cost_usd"] = round(p["cost_usd"], 6)
            out["purposes"][purpose] = p
            total_calls += p["calls"]
            total_hits += p["cache_hits"]
            total_cost += p["cost_usd"]
        out["calls"] = total_calls
        out["cache_hits"] = total_hits
        out["cache_hit_rate"] = round(total_hits / total_calls, 4) if total_calls else None
        out["cost_usd"] = round(total_cost, 6)
        return out

    # --- cache --------------------------------------------------------------------------
    def cache_get(self, key: str) -> str | None:
        return self.s.execute(select(LLMCache.response_json).where(LLMCache.key == key)).scalar_one_or_none()

    def cache_put(self, key: str, model: str, purpose: str, response_json: str, now: datetime) -> None:
        self.s.execute(
            insert(LLMCache)
            .values(key=key, model=model, purpose=purpose, response_json=response_json, created_at=now)
            .on_conflict_do_nothing(index_elements=["key"])
        )
