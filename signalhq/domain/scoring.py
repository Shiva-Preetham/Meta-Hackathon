"""Deterministic priority scoring (CLAUDE.md §6, ADR-006).

Pure functions only: no database, no clock, no model. Given the same members and the same
window end, the score is always the same, and every input is returned so the UI can show
"why is this ranked here?".

    growth          = (mentions_recent + 1) / (mentions_prev + 1)
    revenue_at_risk = sum of MRR over distinct accounts in the recent window whose item has
                      churn_signal = true or severity in {high, critical}
    severity_weight = mean severity weight of recent items (low=1 .. critical=4)
    score           = log1p(revenue_at_risk_dollars) * sqrt(growth)
                      * severity_weight * log1p(mentions_recent)
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from signalhq.config import AT_RISK_SEVERITIES, SEVERITY_WEIGHTS

FORMULA = (
    "score = log1p(revenue_at_risk_dollars) x sqrt(growth) x severity_weight "
    "x log1p(mentions_recent)"
)


@dataclass(frozen=True)
class MemberFact:
    """The facts about one theme member that the score depends on."""

    item_id: int
    created_at: datetime
    account_id: int | None
    mrr_cents: int
    severity: str
    churn_signal: bool


@dataclass(frozen=True)
class ScoreInputs:
    window_end: datetime
    window_days: int
    mentions_recent: int
    mentions_prev: int
    growth: float
    revenue_at_risk_cents: int
    at_risk_accounts: int
    severity_weight: float
    score: float

    def to_dict(self) -> dict:
        d = asdict(self)
        d["window_end"] = self.window_end.isoformat()
        d["revenue_at_risk_dollars"] = self.revenue_at_risk_cents / 100
        d["formula"] = FORMULA
        return d


def growth_ratio(mentions_recent: int, mentions_prev: int) -> float:
    """Add-one smoothed growth, so a brand-new theme does not divide by zero."""
    return (mentions_recent + 1) / (mentions_prev + 1)


def severity_weight(
    severities: Iterable[str], weights: Mapping[str, float] = SEVERITY_WEIGHTS
) -> float:
    values = [weights[s] for s in severities]
    return sum(values) / len(values) if values else 0.0


def revenue_at_risk_cents(
    members: Iterable[MemberFact], at_risk_severities: frozenset[str] = AT_RISK_SEVERITIES
) -> tuple[int, int]:
    """Sum MRR once per distinct account with an at-risk item. Returns (cents, accounts)."""
    at_risk: dict[int, int] = {}
    for m in members:
        if m.account_id is None:
            continue
        if m.churn_signal or m.severity in at_risk_severities:
            at_risk[m.account_id] = m.mrr_cents
    return sum(at_risk.values()), len(at_risk)


def priority_score(
    revenue_at_risk_dollars: float, growth: float, sev_weight: float, mentions_recent: int
) -> float:
    return (
        math.log1p(revenue_at_risk_dollars)
        * math.sqrt(growth)
        * sev_weight
        * math.log1p(mentions_recent)
    )


def compute_score(
    members: Sequence[MemberFact],
    window_end: datetime,
    window_days: int = 7,
    weights: Mapping[str, float] = SEVERITY_WEIGHTS,
) -> ScoreInputs:
    """Score one theme. Recent window is (end - d, end]; previous is (end - 2d, end - d]."""
    window = timedelta(days=window_days)
    recent_start, prev_start = window_end - window, window_end - 2 * window
    recent = [m for m in members if recent_start < m.created_at <= window_end]
    prev = [m for m in members if prev_start < m.created_at <= recent_start]

    growth = growth_ratio(len(recent), len(prev))
    rar_cents, n_accounts = revenue_at_risk_cents(recent)
    sev = severity_weight((m.severity for m in recent), weights)
    score = priority_score(rar_cents / 100, growth, sev, len(recent))
    return ScoreInputs(
        window_end=window_end,
        window_days=window_days,
        mentions_recent=len(recent),
        mentions_prev=len(prev),
        growth=round(growth, 6),
        revenue_at_risk_cents=rar_cents,
        at_risk_accounts=n_accounts,
        severity_weight=round(sev, 6),
        score=round(score, 6),
    )


def rank_key(theme_id: int, inputs: ScoreInputs) -> tuple:
    """Total order used everywhere a ranking is shown. Ties break on revenue, then volume,
    then growth, then the older theme id, so equal scores never reorder at random."""
    return (
        -inputs.score,
        -inputs.revenue_at_risk_cents,
        -inputs.mentions_recent,
        -inputs.growth,
        theme_id,
    )


def rank(scored: Iterable[tuple[int, ScoreInputs]]) -> list[tuple[int, ScoreInputs]]:
    return sorted(scored, key=lambda pair: rank_key(*pair))
