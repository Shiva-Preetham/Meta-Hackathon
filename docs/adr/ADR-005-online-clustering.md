# ADR-005: Online clustering with periodic re-clustering

**Status:** Accepted for the hashing embedder; MiniLM values pending measurement

## Context
Feedback arrives continuously and the product promise is "see a new problem within
hours". Batch-only clustering (re-run everything nightly) would be simple but slow to
surface new themes and would reshuffle theme identities on every run.

## Decision
1. **Assign:** a new item joins the nearest active theme if cosine(item, centroid) >= tau;
   the centroid is updated incrementally (running mean).
2. **Pool:** otherwise the item waits in an unassigned pool.
3. **Re-cluster (singleton job):** when the pool has at least 12 items, average-linkage
   agglomerative clustering (cosine) groups the pool; groups of at least 4 become new
   themes; pool items that now clear tau join a theme.
4. **Merge:** active themes whose centroids are at least merge_tau similar are merged into
   the larger one, which is then renamed.
5. Clustering runs inside `BEGIN IMMEDIATE`, so it is serialised and centroid updates never
   interleave.

## Choosing the thresholds (measured)
`scripts/tune_clustering.py` replays the online algorithm over the synthetic set in time
order and scores the adjusted Rand index (ARI) against the planted themes, averaged over
four re-cluster cadences (after 1, 5 and 20 pooled items, and one batch re-cluster),
because live timing varies. Planted noise and unassigned items count as singletons.
SYNTHETIC DATA, offline heuristic enrichment, hashing embedder:

| Embedded text | Best setting (tau / link / merge) | Mean ARI | Worst cadence | Themes found (8 planted) |
|---|---|---|---|---|
| quote + text | 0.20 / 0.15 / any | 0.668 (single cadence) | n/a | 15-19 |
| quote + text + product-area anchor x2 | **0.20 / 0.10 / 0.70** | **0.977** | 0.955 | 8 at every cadence |

The live pipeline with these defaults (`demo --no-serve`) gave ARI 0.987 with 8 of 8
themes (see `eval/report-offline-synthetic.md`).

Why the anchor: themes live inside product areas, and the area comes from the structured
enrichment (Spark online). The anchor pulls same-area items together and keeps areas
apart; the words still separate themes inside one area. "other" is not anchored.

**Caveat, stated plainly:** the offline heuristic's area rules and the synthetic templates
were written by the same author, so they share vocabulary and the anchor helps more here
than it will on real data. Real-data theme purity comes only from the human-labelled eval.

merge_tau had no effect between 0.5 and 0.8 on this set (no centroid pairs that close),
so 0.70 is a conservative guard against duplicate themes, not a tuned value.

## MiniLM
Defaults in `config.CLUSTER_DEFAULTS["minilm"]` (tau 0.55, link 0.35, merge 0.85) are
starting points and **not measured**: the model could not be downloaded in the build
environment. Run `python scripts/tune_clustering.py --embedder minilm` and update both
the config and this ADR.

## Consequences
- New themes appear without a nightly batch; existing theme ids stay stable.
- Order dependence: results vary slightly with arrival order and worker timing (the sweep
  reports the worst cadence for that reason).
- Re-clustering is O(n^2) in the pool, so the pool is capped at the newest 3,000 items.
