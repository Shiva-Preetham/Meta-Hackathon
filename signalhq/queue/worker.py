"""Worker: claim -> run stage -> ack (with follow-ups) / retry with backoff / dead-letter.

Follow-up jobs are inserted in the same transaction that marks the job done, and only if
this worker still owns the job (fenced by ``locked_by`` and ``attempts``). If a worker
crashes after a stage wrote its results but before the ack, the job's lease expires,
another worker reclaims it, the stage sees its work is already done (stages are
idempotent) and returns the same follow-ups, which are then enqueued exactly once.
"""

from __future__ import annotations

import logging
import random
import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta

from pydantic import ValidationError

from signalhq.db import utcnow
from signalhq.domain.states import InvalidTransition, ItemState, can_transition
from signalhq.llm.errors import BudgetExceededError, PermanentError, TransientError
from signalhq.pipeline.cluster import ClusterStage, ReclusterStage
from signalhq.pipeline.embed import EmbedStage
from signalhq.pipeline.enrich import EnrichStage
from signalhq.pipeline.name_themes import NameThemeStage
from signalhq.pipeline.score import ScoreStage
from signalhq.pipeline.stage import CLUSTER, EMBED, ENRICH, Stage, StageContext
from signalhq.repositories import ClaimedJob, ItemRepo, JobRepo

log = logging.getLogger(__name__)

ITEM_STAGES = (ENRICH, EMBED, CLUSTER)
# Errors that will not go away by retrying the same job.
PERMANENT_ERRORS: tuple[type[BaseException], ...] = (PermanentError, ValidationError, InvalidTransition)


def default_stages() -> dict[str, Stage]:
    stages: list[Stage] = [
        EnrichStage(),
        EmbedStage(),
        ClusterStage(),
        ReclusterStage(),
        NameThemeStage(),
        ScoreStage(),
    ]
    return {stage.job_type: stage for stage in stages}


class BudgetStop(Exception):
    """Raised out of the worker loop when the spend cap is reached."""


def job_backoff(attempts: int, base_s: float, max_s: float, rng: random.Random) -> float:
    """Exponential backoff with equal jitter: half fixed, half random."""
    cap = min(max_s, base_s * 2 ** max(0, attempts - 1))
    return cap / 2 + rng.uniform(0, cap / 2)


class Worker:
    def __init__(
        self,
        ctx: StageContext,
        stages: dict[str, Stage] | None = None,
        worker_id: str | None = None,
        types: list[str] | None = None,
        rng: random.Random | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.ctx = ctx
        self.stages = stages or default_stages()
        self.worker_id = worker_id or f"w-{uuid.uuid4().hex[:8]}"
        self.types = types
        self.rng = rng or random.Random()
        self.now = clock or ctx.now
        self._last_reap = 0.0

    # --- one iteration ------------------------------------------------------------------
    def run_once(self) -> bool:
        """Claim and process one job. Returns False when nothing was claimable."""
        self._maybe_reap()
        settings = self.ctx.settings
        with self.ctx.db.write() as s:
            job = JobRepo(s).claim(self.worker_id, self.now(), settings.job_lease_s, self.types)
        if job is None:
            return False
        stage = self.stages.get(job.type)
        try:
            if stage is None:
                raise PermanentError(f"no stage handles job type {job.type!r}")
            follow_ups = stage.run(job, self.ctx)
        except BudgetExceededError as exc:
            with self.ctx.db.write() as s:
                JobRepo(s).release(job, self.now())
            raise BudgetStop(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - every failure is recorded on the job
            self._fail(job, exc)
            return True

        with self.ctx.db.write() as s:
            jobs = JobRepo(s)
            now = self.now()
            if jobs.complete(job, now):
                jobs.enqueue_many(follow_ups, now, settings.job_max_attempts)
            else:
                log.warning("job %s: lease lost before ack; results discarded", job.id)
        return True

    def _fail(self, job: ClaimedJob, exc: BaseException) -> None:
        settings = self.ctx.settings
        now = self.now()
        permanent = isinstance(exc, PERMANENT_ERRORS)
        dead = permanent or job.attempts >= job.max_attempts
        retry_at: datetime | None = None
        if not dead:
            delay = job_backoff(job.attempts, settings.job_backoff_base_s, settings.job_backoff_max_s, self.rng)
            if isinstance(exc, TransientError) and exc.retry_after:
                delay = max(delay, exc.retry_after)
            retry_at = now + timedelta(seconds=delay)
        message = f"{type(exc).__name__}: {exc}"
        log.warning("job %s (%s) attempt %s failed: %s", job.id, job.type, job.attempts, message)
        with self.ctx.db.write() as s:
            state = JobRepo(s).fail(job, now, message, retry_at)
            if state is not None and job.type in ITEM_STAGES and "item_id" in job.payload:
                self._mark_item(ItemRepo(s), int(job.payload["item_id"]), dead, message)

    @staticmethod
    def _mark_item(items: ItemRepo, item_id: int, dead: bool, message: str) -> None:
        item = items.get(item_id)
        if item is None:
            return
        target = ItemState.DEAD if dead else ItemState.FAILED
        if can_transition(item.state, target):
            items.set_state(item_id, target, error=message[:2000])

    def _maybe_reap(self) -> None:
        """Dead-letter jobs whose lease expired on their last attempt (every few seconds)."""
        if time.monotonic() - self._last_reap < 5.0:
            return
        self._last_reap = time.monotonic()
        with self.ctx.db.write() as s:
            for payload in JobRepo(s).reap_expired(self.now()):
                if "item_id" in payload:
                    self._mark_item(ItemRepo(s), int(payload["item_id"]), True, "lease expired on final attempt")

    # --- loops --------------------------------------------------------------------------
    def run_forever(self, stop: threading.Event) -> None:
        while not stop.is_set():
            if not self.run_once():
                stop.wait(self.ctx.settings.worker_poll_s)


class WorkerPool:
    """N worker threads sharing one stage context. Threads suit this workload: enrichment
    waits on the network, and clustering is serialised by the database anyway."""

    def __init__(self, ctx: StageContext, concurrency: int = 4, stages: dict[str, Stage] | None = None) -> None:
        self.ctx = ctx
        self.concurrency = concurrency
        self.stages = stages or default_stages()
        self.stop_event = threading.Event()
        self.budget_stopped: str | None = None
        self._threads: list[threading.Thread] = []

    def _loop(self, worker: Worker) -> None:
        try:
            worker.run_forever(self.stop_event)
        except BudgetStop as exc:
            self.budget_stopped = str(exc)
            log.error("Budget cap reached, stopping all workers: %s", exc)
            self.stop_event.set()

    def start(self) -> None:
        for i in range(self.concurrency):
            worker = Worker(self.ctx, self.stages, worker_id=f"w{i}-{uuid.uuid4().hex[:6]}")
            thread = threading.Thread(target=self._loop, args=(worker,), name=f"signal-worker-{i}", daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self, timeout: float = 10.0) -> None:
        self.stop_event.set()
        for thread in self._threads:
            thread.join(timeout)
        self._threads.clear()

    @property
    def running(self) -> bool:
        return any(t.is_alive() for t in self._threads)


def drain(ctx: StageContext, concurrency: int = 1, timeout_s: float = 600.0, stages: dict[str, Stage] | None = None) -> bool:
    """Process until no job is pending (including delayed ones). Returns True if drained."""
    pool = WorkerPool(ctx, concurrency, stages)
    pool.start()
    deadline = time.monotonic() + timeout_s
    try:
        while time.monotonic() < deadline:
            if pool.budget_stopped:
                raise BudgetStop(pool.budget_stopped)
            with ctx.db.read() as s:
                pending = JobRepo(s).pending_count()
            if pending == 0:
                return True
            time.sleep(0.1)
        return False
    finally:
        pool.stop()
