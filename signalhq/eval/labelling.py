"""Stratified sampling and the labelling queue.

Labels must come from a human, never from Spark (that would be circular). The labelling
page therefore never shows the model's prediction for the item being labelled, so the
labeller is not anchored by it.
"""

from __future__ import annotations

import csv
import json
import random
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from signalhq.repositories import EvalRepo, ItemRepo

FIELDS = ("item_id", "content_hash", "source", "created_at", "text")


@dataclass(frozen=True)
class SampleRow:
    item_id: int
    content_hash: str
    source: str
    created_at: str
    text: str


def stratified_sample(s: Session, size: int, seed: int) -> tuple[list[SampleRow], str]:
    """Proportional allocation over (source, predicted type) strata, at least one item per
    stratum, fixed seed. Synthetic items are excluded whenever real items exist."""
    rows = ItemRepo(s).enriched_rows()
    real = [(i, e) for i, e in rows if not (i.meta_json and json.loads(i.meta_json).get("synthetic"))]
    note = "real items only"
    if not real:
        real, note = rows, "synthetic items (no real items in this database)"
    strata: dict[tuple[str, str], list] = defaultdict(list)
    for item, enrichment in sorted(real, key=lambda r: r[0].id):
        strata[(item.source, enrichment.type)].append(item)
    rng = random.Random(seed)
    total = sum(len(v) for v in strata.values())
    size = min(size, total)
    chosen = []
    for key in sorted(strata):
        members = strata[key]
        quota = max(1, round(size * len(members) / total)) if total else 0
        chosen.extend(rng.sample(members, min(quota, len(members))))
    rng.shuffle(chosen)
    chosen = chosen[:size]
    return [
        SampleRow(i.id, i.content_hash, i.source, i.created_at.isoformat(), i.text) for i in chosen
    ], note


def write_sample(rows: list[SampleRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.__dict__)


def read_sample(path: Path) -> list[SampleRow]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return [
            SampleRow(int(r["item_id"]), r["content_hash"], r["source"], r["created_at"], r["text"])
            for r in csv.DictReader(fh)
        ]


def next_to_label(s: Session, path: Path) -> tuple[SampleRow | None, int, int]:
    """(next unlabelled row, labelled so far, sample size)."""
    rows = read_sample(path)
    done = EvalRepo(s).labelled_hashes()
    labelled = sum(1 for r in rows if r.content_hash in done)
    nxt = next((r for r in rows if r.content_hash not in done), None)
    return nxt, labelled, len(rows)
