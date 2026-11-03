"""ADR-007 measurement: per-item vs micro-batch enrichment on the same items.

Needs MODEL_API_KEY (the offline heuristic would make the comparison meaningless). Every
call goes through the LLM gateway, so the cache, budget cap and ledger apply. Reports, for
each mode: total cost, cost per 1,000 items, wall time, items whose JSON was valid, and how
often the two modes agree on type / product area / severity. If human labels exist for
these items, accuracy for both modes is printed too.

    python scripts/compare_batching.py --db signal-real.db --n 100 --batch 20
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from signalhq.config import get_settings  # noqa: E402
from signalhq.domain.schemas import (  # noqa: E402
    ENRICH_BATCH_SCHEMA,
    ENRICH_SCHEMA,
    ENRICH_SCHEMA_VERSION,
    EnrichmentOut,
)
from signalhq.domain.taxonomy import normalize_product_area  # noqa: E402
from signalhq.llm.prompts import enrich_batch_messages, enrich_messages  # noqa: E402
from signalhq.repositories import EvalRepo, ItemRepo  # noqa: E402
from signalhq.runtime import build_runtime  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--n", type=int, default=100)
    parser.add_argument("--batch", type=int, default=20)
    args = parser.parse_args()
    settings = get_settings().model_copy(update={"db_path": args.db})
    if settings.is_offline:
        print("Needs MODEL_API_KEY: comparing the offline heuristic with itself measures nothing.")
        return 2
    rt = build_runtime(settings)
    with rt.db.read() as s:
        items = [(i.id, i.text, i.source) for i in ItemRepo(s).first(args.n)]
        labels = {item_id: label for label, item_id in EvalRepo(s).labels_with_current_items()}

    def spent() -> float:
        return rt.gateway.spent_usd()

    results: dict[str, dict[int, EnrichmentOut]] = {"per_item": {}, "batch": {}}
    stats = {}
    start_cost, t0 = spent(), time.monotonic()
    for item_id, text, source in items:
        try:
            raw = rt.gateway.structured(purpose="compare:per_item", messages=enrich_messages(text, source),
                                        schema=ENRICH_SCHEMA, schema_name="feedback_enrichment",
                                        schema_version=ENRICH_SCHEMA_VERSION, max_tokens=settings.enrich_max_output_tokens)
            results["per_item"][item_id] = EnrichmentOut.model_validate(raw)
        except Exception as exc:  # noqa: BLE001
            print(f"per-item {item_id}: {type(exc).__name__}: {exc}")
    stats["per_item"] = (spent() - start_cost, time.monotonic() - t0)

    start_cost, t0 = spent(), time.monotonic()
    for i in range(0, len(items), args.batch):
        chunk = items[i: i + args.batch]
        try:
            raw = rt.gateway.structured(purpose="compare:batch", messages=enrich_batch_messages(chunk),
                                        schema=ENRICH_BATCH_SCHEMA, schema_name="feedback_enrichment_batch",
                                        schema_version=ENRICH_SCHEMA_VERSION,
                                        max_tokens=settings.enrich_max_output_tokens * len(chunk))
            for row in raw["items"]:
                row = dict(row)
                item_id = row.pop("id")
                results["batch"][item_id] = EnrichmentOut.model_validate(row)
        except Exception as exc:  # noqa: BLE001
            print(f"batch starting at {chunk[0][0]}: {type(exc).__name__}: {exc}")
    stats["batch"] = (spent() - start_cost, time.monotonic() - t0)

    n = len(items)
    print(f"items: {n}; batch size {args.batch}; model {settings.spark_model}")
    for mode in ("per_item", "batch"):
        cost, secs = stats[mode]
        print(f"{mode:9s} valid {len(results[mode])}/{n}  cost ${cost:.4f}  per 1,000 ${cost / max(1, n) * 1000:.4f}  wall {secs:.1f}s")
    both = set(results["per_item"]) & set(results["batch"])
    for field in ("type", "severity", "churn_signal"):
        agree = sum(getattr(results["per_item"][i], field) == getattr(results["batch"][i], field) for i in both)
        print(f"agreement on {field}: {agree}/{len(both)}")
    agree_area = sum(normalize_product_area(results["per_item"][i].product_area)
                     == normalize_product_area(results["batch"][i].product_area) for i in both)
    print(f"agreement on product_area (normalised): {agree_area}/{len(both)}")
    labelled = [i for i in both if i in labels]
    if labelled:
        for mode in ("per_item", "batch"):
            acc = sum(results[mode][i].type == labels[i].type for i in labelled) / len(labelled)
            print(f"{mode} type accuracy on {len(labelled)} human labels: {acc:.3f}")
    print("Record these numbers in docs/adr/ADR-007-enrichment-batching.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
