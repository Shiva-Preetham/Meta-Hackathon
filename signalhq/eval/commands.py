"""CLI actions for ``python -m signalhq eval sample|report`` (labelling is served at /label)."""

from __future__ import annotations

from pathlib import Path

from signalhq.config import Settings
from signalhq.db import Database
from signalhq.eval.labelling import stratified_sample, write_sample
from signalhq.eval.report import compute, render


def sample(settings: Settings, out: Path, size: int = 200, seed: int = 13) -> int:
    db = Database(settings.db_path)
    db.create_all()
    with db.read() as s:
        rows, note = stratified_sample(s, size, seed)
    if not rows:
        print("No enriched items yet. Ingest data and run the workers first.")
        return 1
    write_sample(rows, out)
    print(f"wrote {len(rows)} items ({note}) to {out}; label them at /label (python -m signalhq eval label)")
    return 0


def report(settings: Settings, out: Path) -> int:
    db = Database(settings.db_path)
    db.create_all()
    with db.read() as s:
        result = compute(s, settings)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(result) + "\n", encoding="utf-8")
    print(f"wrote {out} ({result['human'].get('n', 0)} human labels, {result['enriched']} enriched items)")
    return 0
