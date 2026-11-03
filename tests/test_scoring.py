import math
from datetime import datetime, timedelta

import pytest

from signalhq.domain.scoring import (
    MemberFact,
    compute_score,
    growth_ratio,
    priority_score,
    rank,
    revenue_at_risk_cents,
)

END = datetime(2026, 10, 25)


def fact(i, days_ago, account=None, mrr=0, severity="medium", churn=False):
    return MemberFact(i, END - timedelta(days=days_ago), account, mrr, severity, churn)


def test_zero_revenue_gives_zero_score():
    members = [fact(i, 1, account=i, mrr=0, severity="critical", churn=True) for i in range(5)]
    inputs = compute_score(members, END)
    assert inputs.revenue_at_risk_cents == 0
    assert inputs.score == 0.0
    assert inputs.mentions_recent == 5


def test_new_theme_with_no_previous_window():
    members = [fact(1, 1, account=1, mrr=10_000, severity="high")]
    inputs = compute_score(members, END)
    assert inputs.mentions_prev == 0
    assert inputs.growth == 2.0  # (1 + 1) / (0 + 1)
    expected = math.log1p(100.0) * math.sqrt(2.0) * 3.0 * math.log1p(1)
    assert inputs.score == pytest.approx(expected, rel=1e-6)


def test_growth_is_smoothed():
    assert growth_ratio(0, 0) == 1.0
    assert growth_ratio(9, 4) == 2.0


def test_one_huge_account_counts_once_and_is_dampened():
    members = [fact(i, 1, account=7, mrr=500_000, severity="critical") for i in range(10)]
    cents, accounts = revenue_at_risk_cents(members)
    assert (cents, accounts) == (500_000, 1)
    big = compute_score(members, END)
    small = compute_score([fact(i, 1, account=7, mrr=5_000, severity="critical") for i in range(10)], END)
    # 100x the revenue gives far less than 100x the score (log1p).
    assert big.score / small.score < 2.5


def test_only_churn_or_high_severity_items_put_revenue_at_risk():
    members = [
        fact(1, 1, account=1, mrr=1_000, severity="low"),
        fact(2, 1, account=2, mrr=2_000, severity="medium", churn=True),
        fact(3, 1, account=3, mrr=4_000, severity="high"),
        fact(4, 1, account=None, mrr=0, severity="critical"),
    ]
    cents, accounts = revenue_at_risk_cents(members)
    assert (cents, accounts) == (6_000, 2)


def test_windows_split_recent_and_previous():
    members = [fact(1, 1), fact(2, 6.9), fact(3, 7.1), fact(4, 13), fact(5, 20)]
    inputs = compute_score(members, END, window_days=7)
    assert (inputs.mentions_recent, inputs.mentions_prev) == (2, 2)


def test_items_after_window_end_are_ignored():
    inputs = compute_score([fact(1, -1, account=1, mrr=1000, severity="high")], END)
    assert inputs.mentions_recent == 0
    assert inputs.score == 0.0


def test_severity_weight_is_mean_of_recent_items():
    members = [fact(1, 1, severity="low"), fact(2, 1, severity="critical")]
    assert compute_score(members, END).severity_weight == 2.5


def test_ties_break_deterministically():
    same = [fact(1, 1, account=1, mrr=1_000, severity="high")]
    a = (5, compute_score(same, END))
    b = (2, compute_score(same, END))
    assert [tid for tid, _ in rank([a, b])] == [2, 5]
    assert [tid for tid, _ in rank([b, a])] == [2, 5]


def test_rank_orders_by_score_then_revenue():
    low = (1, compute_score([fact(1, 1, account=1, mrr=1_000, severity="high")], END))
    high = (2, compute_score([fact(1, 1, account=1, mrr=100_000, severity="high")], END))
    zero_loud = (3, compute_score([fact(i, 1) for i in range(50)], END))
    assert [tid for tid, _ in rank([zero_loud, low, high])] == [2, 1, 3]


def test_formula_is_the_documented_product():
    assert priority_score(0, 4.0, 3.0, 10) == 0.0
    assert priority_score(99, 4.0, 2.0, 0) == 0.0
    assert priority_score(math.e - 1, 4.0, 1.5, math.e - 1) == pytest.approx(2 * 1.5)


def test_inputs_are_serialisable_with_formula():
    d = compute_score([fact(1, 1, account=1, mrr=1234, severity="high")], END).to_dict()
    assert d["revenue_at_risk_dollars"] == 12.34
    assert "log1p" in d["formula"]
    assert d["window_end"] == END.isoformat()
