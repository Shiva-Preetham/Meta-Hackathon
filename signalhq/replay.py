"""Replay mode: stream a dataset in time order at N times real speed.

``speed`` is data-seconds per wall-second (3600 = one data-hour per second). The replay
clock is stored in the ``kv`` table so the scorer and the dashboard use the replay's
"now", which is what lets a theme visibly emerge and climb the ranking during a demo.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from signalhq.config import Settings
from signalhq.connectors import get_connector
from signalhq.connectors.files import sort_by_time
from signalhq.db import Database, utcnow
from signalhq.domain.schemas import RawFeedback
from signalhq.ingest import ingest
from signalhq.pipeline.score import REPLAY_CLOCK_KEY
from signalhq.pipeline.stage import SCORE
from signalhq.repositories import ItemRepo, JobRepo, KVRepo, NewJob

log = logging.getLogger(__name__)

STATUS_KEY = "replay_status"
PROGRESS_KEY = "replay_progress"


@dataclass
class ReplayStatus:
    status: str
    clock: str | None
    ingested: int
    total: int


def load_dataset(path: Path, connector: str = "jsonl", **options) -> list[RawFeedback]:
    return sort_by_time(list(get_connector(connector, **options).read(path)))


def speed_for_duration(raws: list[RawFeedback], seconds: float) -> float:
    """Speed that replays the whole dataset in about ``seconds`` of wall time."""
    if len(raws) < 2:
        return 3600.0
    span = (raws[-1].created_at - raws[0].created_at).total_seconds()
    return max(1.0, span / max(1.0, seconds))


def reset_processed_data(db: Database) -> None:
    with db.write() as s:
        ItemRepo(s).reset_processed_data()
        kv = KVRepo(s)
        for key in (REPLAY_CLOCK_KEY, STATUS_KEY, PROGRESS_KEY):
            kv.delete(key)


def read_status(db: Database) -> ReplayStatus:
    with db.read() as s:
        kv = KVRepo(s)
        status = kv.get(STATUS_KEY) or "idle"
        clock = kv.get(REPLAY_CLOCK_KEY)
        progress = kv.get(PROGRESS_KEY) or "0/0"
    ingested, total = (int(x) for x in progress.split("/"))
    return ReplayStatus(status=status, clock=clock, ingested=ingested, total=total)


class ReplayDriver:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        raws: list[RawFeedback],
        speed: float,
        *,
        tick_s: float = 0.25,
        score_every_s: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.db = db
        self.settings = settings
        self.raws = sort_by_time(raws)
        self.speed = speed
        self.tick_s = tick_s
        self.score_every_s = score_every_s
        self._clock = clock

    def _publish(self, clock: datetime, done: int, status: str) -> None:
        with self.db.write() as s:
            kv = KVRepo(s)
            kv.set(REPLAY_CLOCK_KEY, clock.isoformat())
            kv.set(PROGRESS_KEY, f"{done}/{len(self.raws)}")
            kv.set(STATUS_KEY, status)

    def _rescore(self) -> None:
        """Scores depend on the clock even when no item arrives (windows slide)."""
        with self.db.write() as s:
            JobRepo(s).enqueue(NewJob(SCORE, {}, dedupe_key="score"), utcnow(), self.settings.job_max_attempts)

    def run(self, stop: threading.Event | None = None) -> ReplayStatus:
        stop = stop or threading.Event()
        if not self.raws:
            return ReplayStatus("done", None, 0, 0)
        start, last = self.raws[0].created_at, self.raws[-1].created_at
        wall_start = self._clock()
        last_score = wall_start
        idx = 0
        self._publish(start, 0, "running")
        while idx < len(self.raws) and not stop.is_set():
            data_now = min(last, start + timedelta(seconds=(self._clock() - wall_start) * self.speed))
            end = idx
            while end < len(self.raws) and self.raws[end].created_at <= data_now:
                end += 1
            if end > idx:
                ingest(self.db, self.settings, self.raws[idx:end])
                idx = end
            self._publish(data_now, idx, "running")
            if self._clock() - last_score >= self.score_every_s:
                self._rescore()
                last_score = self._clock()
            if idx < len(self.raws):
                stop.wait(self.tick_s)
        status = "done" if idx >= len(self.raws) else "stopped"
        final_clock = last if status == "done" else data_now
        self._publish(final_clock, idx, status)
        self._rescore()
        log.info("replay %s: %s/%s items", status, idx, len(self.raws))
        return ReplayStatus(status, final_clock.isoformat(), idx, len(self.raws))


class ReplayController:
    """Runs one replay at a time in a background thread (used by the API)."""

    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, path: Path, *, speed: float | None = None, seconds: float = 90.0,
              reset: bool = True, connector: str = "jsonl") -> ReplayStatus:
        with self._lock:
            if self.running:
                raise RuntimeError("a replay is already running")
            raws = load_dataset(path, connector)
            if reset:
                reset_processed_data(self.db)
            driver = ReplayDriver(self.db, self.settings, raws, speed or speed_for_duration(raws, seconds))
            self._stop = threading.Event()
            self._thread = threading.Thread(target=driver.run, args=(self._stop,), name="signal-replay", daemon=True)
            self._thread.start()
        return read_status(self.db)

    def stop(self) -> ReplayStatus:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(10)
        return read_status(self.db)
