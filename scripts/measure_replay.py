"""Measure time-to-surface for the planted spike in replay mode (SYNTHETIC DATA).

Replays data/sample/feedback_sample.jsonl with workers running and polls the ranking. It
reports, in data time, how long after the first "crash_on_launch" item the theme (a) exists
and (b) ranks first. Planted labels are used only to identify the theme afterwards; the
pipeline never sees them.

    python scripts/measure_replay.py [--seconds 60] [--workers 4] [--db path]
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from signalhq.api.views import themes_view  # noqa: E402
from signalhq.config import get_settings  # noqa: E402
from signalhq.models import FeedbackItem  # noqa: E402
from signalhq.pipeline.score import data_now  # noqa: E402
from signalhq.queue.worker import WorkerPool, drain  # noqa: E402
from signalhq.replay import ReplayDriver, load_dataset, speed_for_duration  # noqa: E402
from signalhq.repositories import ThemeRepo  # noqa: E402
from signalhq.runtime import build_runtime  # noqa: E402

SAMPLE = ROOT / "data" / "sample" / "feedback_sample.jsonl"
SPIKE = "crash_on_launch"


def spike_theme(s) -> int | None:
    """The theme whose members are mostly planted spike items (at least 3, at least 60%)."""
    planted = {i: json.loads(m)["planted_theme"] for i, m in s.execute(select(FeedbackItem.id, FeedbackItem.meta_json)).all()}
    groups: dict[int, list[str]] = {}
    for item_id, theme_id in ThemeRepo(s).assignments().items():
        groups.setdefault(theme_id, []).append(planted.get(item_id, ""))
    candidates = [(tid, ms.count(SPIKE) / len(ms)) for tid, ms in groups.items() if ms.count(SPIKE) >= 3]
    candidates = [c for c in candidates if c[1] >= 0.6]
    return max(candidates, key=lambda c: c[1])[0] if candidates else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--db")
    args = parser.parse_args()
    db_path = args.db or str(Path(tempfile.mkdtemp()) / "replay.db")
    settings = get_settings().model_copy(update={"db_path": db_path})
    runtime = build_runtime(settings)
    raws = load_dataset(SAMPLE)
    first_spike = min(r.created_at for r in raws if r.meta.get("planted_theme") == SPIKE)
    speed = speed_for_duration(raws, args.seconds)

    pool = WorkerPool(runtime.ctx, args.workers)
    pool.start()
    stop = threading.Event()
    thread = threading.Thread(target=ReplayDriver(runtime.db, settings, raws, speed).run, args=(stop,))
    started = time.monotonic()
    thread.start()
    exists_at = top_at = None
    spike_times = sorted(r.created_at for r in raws if r.meta.get("planted_theme") == SPIKE)
    while thread.is_alive() or (top_at is None and time.monotonic() - started < args.seconds + 30):
        time.sleep(0.25)
        with runtime.db.read() as s:
            clock = data_now(s)
            tid = spike_theme(s)
            ranking = themes_view(s, "score", 50)
        if tid is not None and exists_at is None:
            exists_at = clock
        if tid is not None and top_at is None and ranking and ranking[0]["id"] == tid and ranking[0]["score"]:
            top_at = clock
        if top_at is not None and not thread.is_alive():
            break
    thread.join()
    pool.stop()
    drain(runtime.ctx, timeout_s=60)

    def after(t: datetime | None) -> str:
        if t is None:
            return "never"
        arrived = sum(1 for x in spike_times if x <= t)
        return (f"{(t - first_spike).total_seconds() / 3600:.1f} data-hours, "
                f"when {arrived} of {len(spike_times)} spike messages had arrived")

    print(f"SYNTHETIC replay: {len(raws)} items, speed {speed:.0f}x, about {args.seconds:.0f} s wall time")
    print(f"first spike item at {first_spike:%Y-%m-%d %H:%M} (data time)")
    print(f"spike theme exists after: {after(exists_at)}")
    print(f"spike theme ranked #1 after: {after(top_at)}")
    print("Data-hours depend on replay speed: the re-cluster delay (SIGNAL_RECLUSTER_DELAY_S) is wall-clock")
    print("time, so faster replays surface themes later in data time. The message count is the steadier figure.")


if __name__ == "__main__":
    main()
