# ADR-007: Per-item vs micro-batch enrichment

**Status:** Proposed. Per-item is the current default; the comparison has not been run.

## Context
Enrichment is the main model cost. Sending up to 20 items per call amortises the system
prompt (fewer input tokens per item) but couples items: one malformed answer can spoil the
batch, the cache works per batch rather than per item, and the model may drift between
items or mis-copy quotes.

## Decision (current)
Per-item calls, because they give per-item caching (re-ingest and replays are free),
per-item retry and dead-lettering, and simpler failure isolation. Both paths exist: the
batch schema (`ENRICH_BATCH_SCHEMA`) and prompt are implemented and validated.

## How it will be decided
`python scripts/compare_batching.py --db signal-real.db --n 100 --batch 20` runs both modes
on the same items through the gateway and prints cost per 1,000 items, wall time, valid
JSON rate, agreement between modes and, when human labels exist, type accuracy for each.
Switch to micro-batching only if it cuts cost per 1,000 items by more than 30% with no
measurable accuracy loss. **Numbers: not yet measured** (needs `MODEL_API_KEY`).
