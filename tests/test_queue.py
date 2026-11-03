import threading
from collections import Counter
from datetime import datetime, timedelta

from signalhq.db import Database
from signalhq.domain.states import ItemState
from signalhq.llm.errors import PermanentError, TransientError
from signalhq.pipeline.stage import StageContext
from signalhq.queue.worker import Worker, WorkerPool, job_backoff
from signalhq.repositories import ClaimedJob, ItemRepo, JobRepo, NewJob

T0 = datetime(2026, 11, 1, 12, 0, 0)


def enqueue(db: Database, *jobs: NewJob, now=T0, max_attempts=3) -> list[int]:
    with db.write() as s:
        return JobRepo(s).enqueue_many(list(jobs), now, max_attempts)


def claim(db: Database, worker="w1", now=T0, lease=60.0, types=None):
    with db.write() as s:
        return JobRepo(s).claim(worker, now, lease, types)


def test_claim_ack_cycle(db):
    (job_id,) = enqueue(db, NewJob("x", {"a": 1}))
    job = claim(db)
    assert job.id == job_id and job.attempts == 1 and job.payload == {"a": 1}
    assert claim(db, "w2") is None  # running and leased
    with db.write() as s:
        assert JobRepo(s).complete(job, T0)
    with db.read() as s:
        assert JobRepo(s).counts_by_state() == {"done": 1}


def test_delayed_jobs_wait_for_run_after(db):
    enqueue(db, NewJob("x", delay_s=30))
    assert claim(db, now=T0) is None
    assert claim(db, now=T0 + timedelta(seconds=31)) is not None


def test_expired_lease_is_reclaimed_after_a_crash(db):
    enqueue(db, NewJob("x"))
    crashed = claim(db, "crashed-worker", lease=10)
    assert claim(db, "w2", now=T0 + timedelta(seconds=5)) is None
    rescued = claim(db, "w2", now=T0 + timedelta(seconds=11))
    assert rescued.id == crashed.id and rescued.attempts == 2 and rescued.locked_by == "w2"


def test_stale_worker_cannot_ack_after_losing_its_lease(db):
    enqueue(db, NewJob("x"))
    stale = claim(db, "w1", lease=10)
    fresh = claim(db, "w2", now=T0 + timedelta(seconds=11))
    with db.write() as s:
        assert JobRepo(s).complete(stale, T0 + timedelta(seconds=12)) is False
        assert JobRepo(s).fail(stale, T0, "late", None) is None
    with db.write() as s:
        assert JobRepo(s).complete(fresh, T0 + timedelta(seconds=12)) is True


def test_max_attempts_dead_letters(db):
    enqueue(db, NewJob("x"), max_attempts=2)
    job = claim(db)
    with db.write() as s:
        assert JobRepo(s).fail(job, T0, "boom", T0) == "failed"
    job = claim(db)
    assert job.attempts == 2
    with db.write() as s:
        assert JobRepo(s).fail(job, T0, "boom again", None) == "dead"
    assert claim(db, now=T0 + timedelta(hours=1)) is None
    with db.read() as s:
        dead = JobRepo(s).dead_jobs()
    assert dead[0]["last_error"] == "boom again"


def test_expired_lease_on_final_attempt_is_reaped_to_dead(db):
    enqueue(db, NewJob("x", {"item_id": 1}), max_attempts=1)
    claim(db, lease=1)
    later = T0 + timedelta(seconds=5)
    assert claim(db, "w2", now=later) is None  # attempts exhausted, not reclaimable
    with db.write() as s:
        assert JobRepo(s).reap_expired(later) == [{"item_id": 1}]
    with db.read() as s:
        assert JobRepo(s).counts_by_state() == {"dead": 1}


def test_dedupe_key_allows_one_pending_job(db):
    ids = enqueue(db, NewJob("recluster", dedupe_key="r"), NewJob("recluster", dedupe_key="r"))
    assert len(ids) == 1
    job = claim(db)
    with db.write() as s:
        JobRepo(s).complete(job, T0)
    assert len(enqueue(db, NewJob("recluster", dedupe_key="r"))) == 1  # allowed again once done


def test_job_backoff_grows_and_is_capped():
    import random

    rng = random.Random(1)
    delays = [job_backoff(a, 2.0, 30.0, rng) for a in range(1, 8)]
    assert 1.0 <= delays[0] <= 2.0
    assert all(d <= 30.0 for d in delays)
    assert delays[-1] >= 15.0


class RecordingStage:
    def __init__(self, job_type: str, fail_times: int = 0, exc: Exception | None = None, sleep_s: float = 0.0):
        self.job_type = job_type
        self.calls: list[int] = []
        self.fail_times = fail_times
        self.exc = exc or TransientError("flaky")
        self.sleep_s = sleep_s
        self._lock = threading.Lock()

    def run(self, job: ClaimedJob, ctx: StageContext):
        with self._lock:
            self.calls.append(job.id)
            failing = len(self.calls) <= self.fail_times
        if self.sleep_s:
            threading.Event().wait(self.sleep_s)
        if failing:
            raise self.exc
        return []


def test_no_double_processing_with_two_concurrent_workers(ctx):
    enqueue(ctx.db, *[NewJob("work", {"n": i}) for i in range(60)], now=ctx.now())
    stage = RecordingStage("work", sleep_s=0.002)
    pool = WorkerPool(ctx, concurrency=2, stages={"work": stage})
    pool.start()
    try:
        deadline = threading.Event()
        for _ in range(500):
            with ctx.db.read() as s:
                if JobRepo(s).pending_count() == 0:
                    break
            deadline.wait(0.02)
    finally:
        pool.stop()
    counts = Counter(stage.calls)
    assert len(counts) == 60
    assert set(counts.values()) == {1}


def test_worker_retries_transient_errors_then_succeeds(ctx):
    enqueue(ctx.db, NewJob("work"), now=ctx.now(), max_attempts=5)
    stage = RecordingStage("work", fail_times=2)
    worker = Worker(ctx, {"work": stage})
    for _ in range(200):
        worker.run_once()
        with ctx.db.read() as s:
            if JobRepo(s).counts_by_state() == {"done": 1}:
                break
        threading.Event().wait(0.01)
    assert len(stage.calls) == 3


def test_permanent_error_dead_letters_job_and_item(ctx, settings):
    from signalhq.domain.schemas import RawFeedback
    from signalhq.ingest import ingest

    result = ingest(ctx.db, settings, [RawFeedback(source="t", text="hello", created_at=T0)])
    stage = RecordingStage("enrich", fail_times=99, exc=PermanentError("400 bad request"))
    Worker(ctx, {"enrich": stage}).run_once()
    with ctx.db.read() as s:
        assert JobRepo(s).counts_by_state() == {"dead": 1}
        item = ItemRepo(s).get(result.item_ids[0])
        assert item.state == ItemState.DEAD.value
        assert "400 bad request" in item.error


def test_transient_error_marks_item_failed_and_keeps_job(ctx, settings):
    from signalhq.domain.schemas import RawFeedback
    from signalhq.ingest import ingest

    result = ingest(ctx.db, settings, [RawFeedback(source="t", text="hello", created_at=T0)])
    Worker(ctx, {"enrich": RecordingStage("enrich", fail_times=1)}).run_once()
    with ctx.db.read() as s:
        assert JobRepo(s).counts_by_state() == {"failed": 1}
        assert ItemRepo(s).get(result.item_ids[0]).state == ItemState.FAILED.value


def test_unknown_job_type_is_dead_lettered(ctx):
    enqueue(ctx.db, NewJob("mystery"), now=ctx.now())
    Worker(ctx, {}).run_once()
    with ctx.db.read() as s:
        assert JobRepo(s).counts_by_state() == {"dead": 1}
