# Signal: product brief

**Signal turns scattered customer feedback into a revenue-ranked list of what to fix next,
with the evidence behind every decision.**

## Problem

Software companies receive feedback through support tickets, social posts, app reviews and
calls. Product managers read a small sample by hand and prioritise by gut feel and by
whoever complained loudest. The costly failure mode: fixing what many free users mention
while a smaller group of high-revenue customers quietly churns over something else.

## Users

| Persona | Role | Needs |
|---|---|---|
| Priya | Product manager at a subscription software company | Answer "what should we fix next, and why?" with evidence leadership trusts |
| Arjun | Head of customer support | See emerging problems before they become a ticket flood; show product which issues cost the most |
| Buyer | VP of Product or Head of CX | Fewer churned accounts, faster detection of problems |

## Jobs to be done

1. When feedback piles up across channels, I want it grouped into themes automatically, so I
   stop reading thousands of messages by hand.
2. When I decide what to fix, I want each theme ranked by revenue at risk and how fast it is
   growing, so I prioritise by impact, not volume.
3. When I present a decision, I want the actual customer quotes behind it, so leadership
   trusts it.
4. When a new problem starts spiking, I want to see it within hours, not at month-end.

## How Signal answers each job

| Job | Feature | Where |
|---|---|---|
| 1 | Muse Spark enrichment (type, area, severity, churn signal, key quote) + online clustering into themes | `pipeline/enrich.py`, `pipeline/cluster.py` |
| 2 | Deterministic priority score: revenue at risk x growth x severity x volume | `domain/scoring.py`, ADR-006 |
| 3 | Evidence panel: verbatim quotes (checked against the source text) with source and plan | `domain/taxonomy.py`, dashboard |
| 4 | Replay mode and a growth term in the score; "new" badge for themes with no previous-window mentions | `replay.py`, dashboard |

## Positioning

This category exists. Enterpret, Unwrap, BuildBetter, Thematic and SentiSum sell feedback
analytics, which shows companies pay for it. Signal does not claim novelty. Its angles:

- **Explainable ranking.** The priority score is a documented formula, never an LLM opinion.
  Every number on screen traces to its inputs ("why ranked here").
- **Evidence-linked.** Every theme shows the quotes that formed it, and every quote is
  verified to appear verbatim in the original message.
- **Measured.** Accuracy, cost per 1,000 items and latency come from a real evaluation run,
  and the README says "not yet measured" until they do.
- **Replay mode.** Feedback streams in over time, so a demo can show a theme emerging and
  climbing the ranking.

## Non-goals

Ticket management, replying to customers, a CRM, auth/SSO, multi-tenant billing, Slack or
Jira integrations, mobile apps. These are listed under "Future work" in the README.

## Business model (hypothesis)

SaaS priced per tracked feedback volume per month. Unit economics come from the measured
cost per 1,000 items (`python -m signalhq eval report`), never from an invented number.

## Success metrics

| Metric | Source |
|---|---|
| Enrichment accuracy (type, product area) | `eval report` on human labels |
| Theme purity | `eval report` on human theme labels |
| Cost per 1,000 items, p50/p95 latency, throughput | `llm_calls` ledger and `jobs` timings |
| Time to surface a spiking theme | replay of the synthetic set |

## Data honesty

- Revenue is **simulated** (a seeded plan tier per customer id) and labelled as such in the UI
  and README.
- The bundled sample is **synthetic** (written by `scripts/make_sample.py`) and labelled.
- The real dataset is Kaggle's "Customer Support on Twitter" (`twcs.csv`); raw tweets are
  never committed.
