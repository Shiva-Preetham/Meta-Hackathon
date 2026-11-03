# Signal evaluation report

Generated 2026-11-03T13:59:07 UTC by `python -m signalhq eval report`. Every number below is computed
from the database at that moment; nothing is entered by hand.

- Enrichment model(s): offline-heuristic-v1
- Embedder(s): hashing-bow-2048-v1
- Items: 300 (300 enriched); sources: {'app_review': 49, 'support_ticket': 161, 'twitter': 90}

> **Offline demo mode.** Enrichment came from the keyword heuristic `offline-heuristic-v1`,
> not Muse Spark. Accuracy, cost and latency below describe that heuristic and the plumbing,
> not the model. Run with `MODEL_API_KEY` for model numbers.

## Enrichment accuracy on human labels

No human labels yet: not yet measured. Run `eval sample`, then label at `/label`.

## Cost, latency and throughput

| Metric | Value |
|---|---|
| Total model spend | $0.0000 |
| Cost per 1,000 items | not yet measured |
| Model calls (incl. cache hits) | 308 |
| Cache hit rate | 0.019 |
| Throughput (items through clustering per minute) | 3541.8 (300 items) |

### Model call latency (excludes cache hits and rate-limiter waits)

| Purpose | Calls | Errors | p50 ms | p95 ms | Cost |
|---|---|---|---|---|---|
| enrich | 300 | 0 | 0.2 | 0.3 | $0.0000 |
| name_theme | 8 | 0 | 0.2 | 0.4 | $0.0000 |

### Stage processing time (job claim to ack)

| Stage | Jobs | p50 ms | p95 ms |
|---|---|---|---|
| cluster | 300 | 2.6 | 30.3 |
| embed | 300 | 6.5 | 58.7 |
| enrich | 300 | 12.7 | 96.3 |
| name_theme | 8 | 7.3 | 15.4 |
| recluster | 1 | 278.5 | 278.5 |
| score | 1 | 13.3 | 13.3 |

## Synthetic seed set (planted ground truth)

SYNTHETIC DATA. These numbers check that the pipeline recovers themes that were planted on
purpose; they are not evidence of accuracy on real feedback. The offline heuristic and the
synthetic templates were written by the same author, so type/area agreement here is optimistic.

| Metric | Value |
|---|---|
| Adjusted Rand index vs planted themes | 0.987 |
| Themes found / planted | 8 / 8 |
| Items assigned to a theme | 94.7% |
| Type agreement with planted type | 0.767 |
| Area agreement with planted area | 0.960 |
| Items | 300 |

