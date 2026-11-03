"""Ingestion: validate, dedupe, persist and enqueue in one transaction (outbox style).

Because the item row and its first job are written in the same transaction, an item can
never be stored without work queued for it, and a crash cannot enqueue work for an item
that was rolled back. Re-ingesting the same content is a no-op that returns the existing id.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime

from signalhq.config import Settings
from signalhq.connectors.base import content_hash
from signalhq.db import Database, utcnow
from signalhq.domain.schemas import RawFeedback
from signalhq.repositories import AccountRepo, ItemRepo, JobRepo, NewJob

ENRICH = "enrich"


@dataclass
class IngestResult:
    created: int = 0
    duplicates: int = 0
    item_ids: list[int] = field(default_factory=list)


def ingest(
    db: Database,
    settings: Settings,
    raws: Iterable[RawFeedback],
    *,
    batch_size: int = 500,
    now: Callable[[], datetime] = utcnow,
) -> IngestResult:
    result = IngestResult()
    batch: list[RawFeedback] = []
    for raw in raws:
        batch.append(raw)
        if len(batch) >= batch_size:
            _ingest_batch(db, settings, batch, result, now())
            batch = []
    if batch:
        _ingest_batch(db, settings, batch, result, now())
    return result


def _ingest_batch(db: Database, settings: Settings, batch: list[RawFeedback], result: IngestResult, now: datetime) -> None:
    with db.write() as s:
        items, accounts, jobs = ItemRepo(s), AccountRepo(s), JobRepo(s)
        for raw in batch:
            if not raw.text.strip():
                continue
            account_id = accounts.get_or_create(raw.account_ref, settings.simulation_seed) if raw.account_ref else None
            item_id, created = items.insert_if_new(
                raw, content_hash(raw.text, raw.source, raw.external_id), account_id, now
            )
            result.item_ids.append(item_id)
            if created:
                result.created += 1
                jobs.enqueue(NewJob(ENRICH, {"item_id": item_id}), now, settings.job_max_attempts)
            else:
                result.duplicates += 1
