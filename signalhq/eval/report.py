"""``eval report``: accuracy on human labels plus cost, latency and throughput from the ledger.

Every number here is computed from the database at report time. Nothing is typed in by
hand. Sections without data say "not yet measured" instead of showing a placeholder value.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any

from sklearn.metrics import adjusted_rand_score, confusion_matrix, f1_score
from sqlalchemy.orm import Session

from signalhq.config import OFFLINE_MODEL_ID, Settings
from signalhq.db import utcnow
from signalhq.domain.schemas import FEEDBACK_TYPES
from signalhq.repositories import EvalRepo, ItemRepo, JobRepo, LedgerRepo, ThemeRepo
from signalhq.repositories.ledger import percentile

NOT_MEASURED = "not yet measured"


def _fmt(value: float | None, digits: int = 3, suffix: str = "") -> str:
    return NOT_MEASURED if value is None else f"{value:.{digits}f}{suffix}"


def purity(pred: list[int], truth: list[str]) -> float | None:
    """Share of items whose theme's majority human label matches their own label."""
    if not pred:
        return None
    groups: dict[int, Counter[str]] = defaultdict(Counter)
    for p, t in zip(pred, truth, strict=True):
        groups[p][t] += 1
    return sum(c.most_common(1)[0][1] for c in groups.values()) / len(pred)


def compute(s: Session, settings: Settings) -> dict[str, Any]:
    items = ItemRepo(s)
    enriched = items.enriched_rows()
    by_id = {i.id: (i, e) for i, e in enriched}
    assignments = ThemeRepo(s).assignments()
    models = sorted({e.model for _, e in enriched})

    # --- human labels -----------------------------------------------------------------
    labelled = [(label, item_id) for label, item_id in EvalRepo(s).labels_with_current_items() if item_id in by_id]
    human: dict[str, Any] = {"n": len(labelled)}
    if labelled:
        y_true = [label.type for label, _ in labelled]
        y_pred = [by_id[i][1].type for _, i in labelled]
        a_true = [label.product_area for label, _ in labelled]
        a_pred = [by_id[i][1].product_area for _, i in labelled]
        present = [t for t in FEEDBACK_TYPES if t in set(y_true) | set(y_pred)]
        human["type_accuracy"] = sum(t == p for t, p in zip(y_true, y_pred)) / len(y_true)
        human["type_macro_f1"] = float(f1_score(y_true, y_pred, labels=present, average="macro", zero_division=0))
        human["area_accuracy"] = sum(t == p for t, p in zip(a_true, a_pred)) / len(a_true)
        human["confusion_labels"] = present
        human["confusion"] = confusion_matrix(y_true, y_pred, labels=present).tolist()
        themed = [(label.theme_label.strip().lower(), assignments[i]) for label, i in labelled
                  if i in assignments and label.theme_label.strip()]
        human["theme_n"] = len(themed)
        human["theme_purity"] = purity([p for _, p in themed], [t for t, _ in themed]) if themed else None
        severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        errors = [
            {"item_id": i, "text": by_id[i][0].text, "label": label.type, "predicted": by_id[i][1].type,
             "label_area": label.product_area, "predicted_area": by_id[i][1].product_area,
             "predicted_severity": by_id[i][1].severity}
            for label, i in labelled
            if label.type != by_id[i][1].type or label.product_area != by_id[i][1].product_area
        ]
        # "Worst" = errors on items the model rated most severe: they move rankings most.
        errors.sort(key=lambda e: (severity_rank[e["predicted_severity"]], e["item_id"]))
        human["worst_errors"] = errors[:5]

    # --- synthetic ground truth -------------------------------------------------------
    synthetic: dict[str, Any] = {}
    planted = [(i, json.loads(i.meta_json)) for i, _ in enriched if i.meta_json and '"planted_theme"' in i.meta_json]
    if planted:
        truth = [m["planted_theme"] if m["planted_theme"] != "noise" else f"noise-{i.id}" for i, m in planted]
        pred = [assignments.get(i.id, -i.id) for i, _ in planted]
        synthetic = {
            "n": len(planted),
            "ari": float(adjusted_rand_score(truth, pred)),
            "themes_found": len({p for p in pred if p >= 0}),
            "themes_planted": len({m["planted_theme"] for _, m in planted} - {"noise"}),
            "assigned_share": sum(p >= 0 for p in pred) / len(pred),
            "type_agreement": sum(by_id[i.id][1].type == m["planted_type"] for i, m in planted) / len(planted),
            "area_agreement": sum(by_id[i.id][1].product_area == m["planted_area"] for i, m in planted) / len(planted),
        }

    # --- cost, latency, throughput ----------------------------------------------------
    ledger = LedgerRepo(s).summary()
    enrich_items = len(enriched)
    total_cost = ledger["cost_usd"]
    jobs = JobRepo(s)
    durations = jobs.durations_s()
    count, first, last = jobs.processing_span()
    minutes = (last - first).total_seconds() / 60 if first and last and last > first else None
    return {
        "generated_at": utcnow().replace(microsecond=0).isoformat(),
        "models": models,
        "offline": models == [OFFLINE_MODEL_ID],
        "embedders": sorted(_embedding_models(s)),
        "items": items.count(),
        "enriched": enrich_items,
        "sources": items.sources(),
        "human": human,
        "synthetic": synthetic,
        "ledger": ledger,
        # Only billable runs have a cost per item; offline runs report "not yet measured".
        "cost_per_1000": (total_cost / enrich_items * 1000) if enrich_items and models != [OFFLINE_MODEL_ID] else None,
        "stage_latency": {
            t: (percentile(v, 50), percentile(v, 95), len(v)) for t, v in sorted(durations.items()) if v
        },
        "throughput_per_min": (count / minutes) if minutes else None,
        "throughput_items": count,
    }


def _embedding_models(s: Session) -> set[str]:
    from sqlalchemy import select

    from signalhq.models import Embedding

    return set(s.execute(select(Embedding.model).distinct()).scalars())


def render(r: dict[str, Any]) -> str:
    lines: list[str] = ["# Signal evaluation report", ""]
    lines.append(f"Generated {r['generated_at']} UTC by `python -m signalhq eval report`. Every number below is computed")
    lines.append("from the database at that moment; nothing is entered by hand.")
    lines.append("")
    lines.append(f"- Enrichment model(s): {', '.join(r['models']) or 'none'}")
    lines.append(f"- Embedder(s): {', '.join(r['embedders']) or 'none'}")
    lines.append(f"- Items: {r['items']} ({r['enriched']} enriched); sources: {r['sources']}")
    if r["offline"]:
        lines += ["", "> **Offline demo mode.** Enrichment came from the keyword heuristic `offline-heuristic-v1`,",
                  "> not Muse Spark. Accuracy, cost and latency below describe that heuristic and the plumbing,",
                  "> not the model. Run with `MODEL_API_KEY` for model numbers."]
    lines.append("")

    h = r["human"]
    lines += ["## Enrichment accuracy on human labels", ""]
    if not h.get("n"):
        lines += [f"No human labels yet: {NOT_MEASURED}. Run `eval sample`, then label at `/label`.", ""]
    else:
        lines += [
            "| Metric | Value | n |", "|---|---|---|",
            f"| Type accuracy | {_fmt(h['type_accuracy'])} | {h['n']} |",
            f"| Type macro-F1 | {_fmt(h['type_macro_f1'])} | {h['n']} |",
            f"| Product-area accuracy | {_fmt(h['area_accuracy'])} | {h['n']} |",
            f"| Theme purity (labelled items in a theme) | {_fmt(h['theme_purity'])} | {h['theme_n']} |",
            "",
            "### Type confusion matrix (rows = human label, columns = prediction)", "",
            "| | " + " | ".join(h["confusion_labels"]) + " |",
            "|---|" + "---|" * len(h["confusion_labels"]),
        ]
        for label, row in zip(h["confusion_labels"], h["confusion"], strict=True):
            lines.append(f"| **{label}** | " + " | ".join(str(x) for x in row) + " |")
        lines += ["", "### Five worst errors (most severe predictions first)", ""]
        for e in h["worst_errors"]:
            text = e["text"].replace("\n", " ")[:220]
            lines.append(f"- Item {e['item_id']}: \"{text}\"  ")
            lines.append(f"  human: {e['label']} / {e['label_area']}; model: {e['predicted']} / {e['predicted_area']}"
                         f" (severity {e['predicted_severity']})  ")
            lines.append("  Analysis: _to be written after reading the item._")
        lines.append("")

    lines += ["## Cost, latency and throughput", ""]
    led = r["ledger"]
    cost_per_1000 = NOT_MEASURED if r["cost_per_1000"] is None else f"${r['cost_per_1000']:.4f}"
    lines += [
        "| Metric | Value |", "|---|---|",
        f"| Total model spend | ${led['cost_usd']:.4f} |",
        f"| Cost per 1,000 items | {cost_per_1000} |",
        f"| Model calls (incl. cache hits) | {led['calls']} |",
        f"| Cache hit rate | {_fmt(led['cache_hit_rate'])} |",
        f"| Throughput (items through clustering per minute) | {_fmt(r['throughput_per_min'], 1)} ({r['throughput_items']} items) |",
        "",
        "### Model call latency (excludes cache hits and rate-limiter waits)", "",
        "| Purpose | Calls | Errors | p50 ms | p95 ms | Cost |", "|---|---|---|---|---|---|",
    ]
    for purpose, p in sorted(led["purposes"].items()):
        lines.append(f"| {purpose} | {p['calls']} | {p['errors']} | {_fmt(p['p50_latency_ms'], 1)} | "
                     f"{_fmt(p['p95_latency_ms'], 1)} | ${p['cost_usd']:.4f} |")
    lines += ["", "### Stage processing time (job claim to ack)", "", "| Stage | Jobs | p50 ms | p95 ms |", "|---|---|---|---|"]
    for stage, (p50, p95, n) in r["stage_latency"].items():
        lines.append(f"| {stage} | {n} | {p50 * 1000:.1f} | {p95 * 1000:.1f} |")
    lines.append("")

    syn = r["synthetic"]
    lines += ["## Synthetic seed set (planted ground truth)", ""]
    if not syn:
        lines += [f"No synthetic items in this database: {NOT_MEASURED}.", ""]
    else:
        lines += [
            "SYNTHETIC DATA. These numbers check that the pipeline recovers themes that were planted on",
            "purpose; they are not evidence of accuracy on real feedback. The offline heuristic and the",
            "synthetic templates were written by the same author, so type/area agreement here is optimistic.",
            "",
            "| Metric | Value |", "|---|---|",
            f"| Adjusted Rand index vs planted themes | {syn['ari']:.3f} |",
            f"| Themes found / planted | {syn['themes_found']} / {syn['themes_planted']} |",
            f"| Items assigned to a theme | {syn['assigned_share']:.1%} |",
            f"| Type agreement with planted type | {syn['type_agreement']:.3f} |",
            f"| Area agreement with planted area | {syn['area_agreement']:.3f} |",
            f"| Items | {syn['n']} |",
            "",
        ]
    return "\n".join(lines)

