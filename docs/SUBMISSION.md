# Submission draft

Draft answers for the submission form. Every number must match `eval/report.md` and the
README. Items marked **[fill]** need the real run or the human; do not submit them empty.

**Project name:** Signal

**One-liner:** Signal turns scattered customer feedback into a revenue-ranked list of what to
fix next, with the evidence behind every decision.

**Track:** Indie

**Repository:** https://github.com/Shiva-Preetham/Meta-Hackathon (must be public before submitting)

**Demo video:** **[fill]** (3 minutes, script in `docs/DEMO_SCRIPT.md`)

## Problem
Software companies get feedback from support tickets, social posts, app reviews and calls.
Product managers read a small sample by hand and prioritise by gut feel or by whoever
complained loudest. The expensive failure is fixing what many free users mention while a
smaller group of high-revenue customers quietly churns over something else.

## Solution
Signal ingests feedback from files, a webhook and call recordings, enriches each item with
Muse Spark (type, product area, severity, churn signal, a verbatim key quote), groups items
into themes with online clustering, names each theme with Muse Spark, and ranks themes with a
deterministic formula: revenue at risk, growth, severity and volume. The dashboard shows the
ranking, the formula filled in with each theme's numbers, the trend, and the quotes behind it.
A replay mode streams feedback over time so a new problem can be seen emerging and climbing.

## Meta models used and how
- **Muse Spark** (`muse-spark-1.3`): strict JSON-schema structured output for per-item
  enrichment, and theme naming/summaries. The model's key quote is verified to be verbatim
  before it is shown.
- **Muse Voice Transcribe** (`muse-voice-transcribe-1.0`): call recordings become speaker
  turns, each turn a feedback item (`source=call`).
- **Not used, on purpose:** SAM 3.1 and Muse Image; the product has no images to segment or
  generate (ADR-001).
- All calls go through one gateway with caching, rate limiting, retries, a circuit breaker,
  schema validation with a repair attempt, a cost ledger and a budget cap.

## Technical challenges
- Making model output trustworthy: strict schemas, local re-validation, one repair
  round-trip, verbatim-quote checks, taxonomy normalisation.
- Exactly-once effects on an at-least-once queue: transactional enqueue, atomic claims,
  leases, fencing tokens and idempotent stages, all in SQLite.
- Online clustering that does not need a nightly batch, with thresholds chosen by a
  measured sweep rather than by eye.
- Honest measurement: an eval harness with human labels (labelling UI hides model
  predictions), cost and latency from a per-call ledger.

## What is new in the approach (not "first ever")
Feedback analytics products exist (Enterpret, Unwrap, BuildBetter, Thematic, SentiSum).
Signal's approach differs in that the ranking is a deterministic, documented formula where
every number traces to its inputs; every theme is backed by quotes verified against the
source; revenue at risk counts distinct accounts with churn or high-severity signals; and
the results are published with measured cost and accuracy.

## Results
- Enrichment accuracy (human labels): **[fill from eval/report.md]**
- Cost per 1,000 items: **[fill]**
- p50/p95 enrichment latency: **[fill]**
- Synthetic sample, offline mode: 8 of 8 planted themes recovered, adjusted Rand index 0.987;
  the planted spike reaches rank 1 after 12–15 of its 40 messages in replay.

## What's next
Real revenue from billing/CRM data, Postgres and a broker for multi-machine workers,
per-area clustering, screenshot attachments through Spark's image input.

## Data note
Feedback text: Kaggle "Customer Support on Twitter" (real public data) and a synthetic sample.
Revenue figures are simulated for demonstration.
