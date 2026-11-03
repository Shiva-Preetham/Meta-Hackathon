"""DB-backed job queue (ADR-003).

Claiming is one ``UPDATE ... WHERE id = (SELECT ... LIMIT 1) RETURNING`` statement inside a
``BEGIN IMMEDIATE`` transaction, so two workers can never claim the same job. Each claim
increments ``attempts``; ``attempts`` doubles as a fencing token: acknowledging a job only
succeeds if the job is still running under the same worker and the same attempt number. A
worker whose lease expired (and whose job was reclaimed) therefore cannot overwrite the new
owner's result.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from signalhq.domain.states import JobState
from signalhq.models import Job

PENDING_STATES = (JobState.QUEUED.value, JobState.FAILED.value, JobState.RUNNING.value)


@dataclass(frozen=True)
class NewJob:
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    dedupe_key: str | None = None
    delay_s: float = 0.0


@dataclass(frozen=True)
class ClaimedJob:
    id: int
    type: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int
    locked_by: str


class JobRepo:
    def __init__(self, session: Session) -> None:
        self.s = session

    # --- producing ----------------------------------------------------------------------
    def enqueue(self, job: NewJob, now: datetime, max_attempts: int) -> int | None:
        """Insert a job. Returns its id, or None if a pending job has the same dedupe key."""
        result = self.s.execute(
            insert(Job)
            .values(
                type=job.type,
                payload_json=json.dumps(job.payload, sort_keys=True),
                state=JobState.QUEUED.value,
                attempts=0,
                max_attempts=max_attempts,
                run_after=now + timedelta(seconds=job.delay_s),
                dedupe_key=job.dedupe_key,
                created_at=now,
                updated_at=now,
            )
            .prefix_with("OR IGNORE")
        )
        if result.rowcount != 1:
            return None
        return int(result.inserted_primary_key[0])

    def enqueue_many(self, jobs: list[NewJob], now: datetime, max_attempts: int) -> list[int]:
        return [i for j in jobs if (i := self.enqueue(j, now, max_attempts)) is not None]

    # --- consuming ----------------------------------------------------------------------
    def claim(
        self,
        worker_id: str,
        now: datetime,
        lease_s: float,
        types: list[str] | None = None,
    ) -> ClaimedJob | None:
        claimable = and_(
            Job.attempts < Job.max_attempts,
            or_(
                and_(Job.state.in_([JobState.QUEUED.value, JobState.FAILED.value]), Job.run_after <= now),
                # Lease expired: the worker that held it crashed or hung.
                and_(Job.state == JobState.RUNNING.value, Job.locked_until < now),
            ),
        )
        candidate = select(Job.id).where(claimable)
        if types:
            candidate = candidate.where(Job.type.in_(types))
        candidate = candidate.order_by(Job.run_after, Job.id).limit(1).scalar_subquery()
        row = self.s.execute(
            update(Job)
            .where(Job.id == candidate)
            .values(
                state=JobState.RUNNING.value,
                attempts=Job.attempts + 1,
                locked_by=worker_id,
                locked_until=now + timedelta(seconds=lease_s),
                started_at=now,
                updated_at=now,
            )
            .returning(Job.id, Job.type, Job.payload_json, Job.attempts, Job.max_attempts)
        ).first()
        if row is None:
            return None
        return ClaimedJob(
            id=row[0],
            type=row[1],
            payload=json.loads(row[2]),
            attempts=row[3],
            max_attempts=row[4],
            locked_by=worker_id,
        )

    def _owned(self, job: ClaimedJob):
        return and_(
            Job.id == job.id,
            Job.state == JobState.RUNNING.value,
            Job.locked_by == job.locked_by,
            Job.attempts == job.attempts,
        )

    def complete(self, job: ClaimedJob, now: datetime) -> bool:
        """Mark done. False means we lost the lease and must discard our results."""
        result = self.s.execute(
            update(Job)
            .where(self._owned(job))
            .values(
                state=JobState.DONE.value,
                finished_at=now,
                updated_at=now,
                locked_by=None,
                locked_until=None,
                last_error=None,
            )
        )
        return result.rowcount == 1

    def fail(self, job: ClaimedJob, now: datetime, error: str, retry_at: datetime | None) -> str | None:
        """Record a failure. ``retry_at=None`` dead-letters the job. Returns the new state,
        or None if we no longer own the job."""
        state = JobState.DEAD.value if retry_at is None else JobState.FAILED.value
        result = self.s.execute(
            update(Job)
            .where(self._owned(job))
            .values(
                state=state,
                run_after=retry_at if retry_at is not None else now,
                last_error=error[:2000],
                finished_at=now if retry_at is None else None,
                updated_at=now,
                locked_by=None,
                locked_until=None,
            )
        )
        return state if result.rowcount == 1 else None

    def release(self, job: ClaimedJob, now: datetime) -> bool:
        """Give a job back without counting the attempt (used when the budget stops us)."""
        result = self.s.execute(
            update(Job)
            .where(self._owned(job))
            .values(
                state=JobState.QUEUED.value,
                attempts=Job.attempts - 1,
                updated_at=now,
                locked_by=None,
                locked_until=None,
            )
        )
        return result.rowcount == 1

    def reap_expired(self, now: datetime) -> list[dict[str, Any]]:
        """Dead-letter jobs whose lease expired on their final attempt. Returns payloads."""
        rows = self.s.execute(
            update(Job)
            .where(
                Job.state == JobState.RUNNING.value,
                Job.locked_until < now,
                Job.attempts >= Job.max_attempts,
            )
            .values(
                state=JobState.DEAD.value,
                last_error="lease expired on final attempt (worker crashed or hung)",
                finished_at=now,
                updated_at=now,
                locked_by=None,
                locked_until=None,
            )
            .returning(Job.payload_json)
        ).all()
        return [json.loads(r[0]) for r in rows]

    # --- observability ------------------------------------------------------------------
    def counts(self) -> dict[str, dict[str, int]]:
        rows = self.s.execute(select(Job.type, Job.state, func.count()).group_by(Job.type, Job.state)).all()
        out: dict[str, dict[str, int]] = {}
        for job_type, state, n in rows:
            out.setdefault(job_type, {})[state] = n
        return out

    def counts_by_state(self) -> dict[str, int]:
        rows = self.s.execute(select(Job.state, func.count()).group_by(Job.state)).all()
        return {state: n for state, n in rows}

    def pending_count(self) -> int:
        return self.s.execute(select(func.count()).where(Job.state.in_(PENDING_STATES))).scalar_one()

    def next_run_after(self) -> datetime | None:
        return self.s.execute(
            select(func.min(Job.run_after)).where(
                Job.state.in_([JobState.QUEUED.value, JobState.FAILED.value])
            )
        ).scalar_one()

    def dead_jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.s.execute(
            select(Job).where(Job.state == JobState.DEAD.value).order_by(Job.id.desc()).limit(limit)
        ).scalars()
        return [
            {"id": j.id, "type": j.type, "attempts": j.attempts, "last_error": j.last_error, "payload": json.loads(j.payload_json)}
            for j in rows
        ]

    def requeue_dead(self, now: datetime) -> int:
        result = self.s.execute(
            update(Job)
            .where(Job.state == JobState.DEAD.value)
            .values(state=JobState.QUEUED.value, attempts=0, run_after=now, updated_at=now, last_error=None)
        )
        return result.rowcount

    def durations_s(self) -> dict[str, list[float]]:
        """Processing time of finished jobs per type (last attempt only)."""
        rows = self.s.execute(
            select(Job.type, Job.started_at, Job.finished_at).where(
                Job.state == JobState.DONE.value, Job.started_at.is_not(None), Job.finished_at.is_not(None)
            )
        ).all()
        out: dict[str, list[float]] = {}
        for job_type, started, finished in rows:
            out.setdefault(job_type, []).append((finished - started).total_seconds())
        return out

    def processing_span(self) -> tuple[int, datetime | None, datetime | None]:
        """(items through the cluster stage, first enrich start, last cluster finish)."""
        count, last = self.s.execute(
            select(func.count(), func.max(Job.finished_at)).where(
                Job.type == "cluster", Job.state == JobState.DONE.value
            )
        ).one()
        first = self.s.execute(
            select(func.min(Job.started_at)).where(Job.type == "enrich", Job.started_at.is_not(None))
        ).scalar_one()
        return int(count), first, last
