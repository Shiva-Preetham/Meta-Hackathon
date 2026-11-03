# ADR-006: Deterministic priority scoring

**Status:** Accepted

## Context
The ranking is the product's main output and the thing leadership will question ("why is
this #1?"). An LLM-produced ranking cannot be audited, changes between runs and cannot be
unit-tested.

## Decision
The LLM extracts facts per item (severity, churn signal); a pure function ranks:

```
growth          = (mentions_recent + 1) / (mentions_prev + 1)
revenue_at_risk = sum of MRR over distinct accounts in the recent window whose item has
                  churn_signal = true or severity in {high, critical}
severity_weight = mean severity of recent items (low 1, medium 2, high 3, critical 4)
score           = log1p(revenue_at_risk_dollars) x sqrt(growth) x severity_weight
                  x log1p(mentions_recent)
```

Windows are 7 days of data time (the replay clock during a replay). Ties break on revenue,
then recent mentions, then growth, then the older theme id, so equal scores never reorder.
Every input is stored in `theme_scores.inputs_json` and shown in the UI with the formula
filled in.

## Why these terms
- **log1p(revenue):** money matters most, but one huge account should not drown everything
  (100x the revenue gives about 2x the score in the unit test).
- **sqrt(growth):** rewards acceleration without letting a 1 -> 3 jump dominate.
- **Distinct accounts:** a customer who complains ten times counts once.
- **log1p(mentions):** volume still matters, with diminishing returns.
- A theme with no revenue at risk scores 0 by design: Signal's thesis is that loud free
  users should not outrank quietly churning paying ones. Such themes still appear, sorted
  by the tie-breakers, and every other sort order is one click away.

## Consequences
- Fully unit-tested (zero revenue, new theme, ties, one huge account, window edges).
- Weights live in `config.py`. Changing them is a code change with a test, not a prompt tweak.
- Revenue is simulated in this project; a real deployment would join a CRM or billing system.
