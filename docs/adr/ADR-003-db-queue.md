# ADR-003: Database-backed job queue

**Status:** Accepted

## Context
Each item passes through enrich -> embed -> cluster, plus singleton jobs (re-cluster,
name theme, score). Model calls fail transiently (429, 5xx, timeouts) and workers can crash.

## Decision
A `jobs` table is the queue:
- **Transactional enqueue (outbox style):** the item row and its first job are written in
  one transaction; follow-up jobs are written in the same transaction that marks the
  current job done.
- **Atomic claim:** one `UPDATE jobs ... WHERE id = (SELECT ... LIMIT 1) RETURNING ...`
  inside `BEGIN IMMEDIATE`. Two workers cannot claim the same job (tested with two
  concurrent workers over 60 jobs: each processed exactly once).
- **Leases:** `locked_until`; an expired lease is reclaimable (crash recovery, tested).
- **Fencing:** ack/fail only succeed if `locked_by` and `attempts` still match the claim,
  so a worker that lost its lease cannot overwrite the new owner's result.
- **Retries:** exponential backoff with jitter (`run_after`), at least the server's
  Retry-After; permanent errors (4xx, validation) dead-letter immediately.
- **Dead-letter:** state `dead` after `max_attempts`; `requeue-dead` brings them back.
- **Dedupe keys:** a partial unique index allows one pending job per key (one queued
  re-cluster, one score run, one rename per theme).
- **Idempotent stages:** re-running a job whose work is already stored is a no-op that
  returns the same follow-ups, which gives effectively-once processing.

## Alternatives rejected
- **Redis + Celery/RQ:** a second service to install and run, and enqueue would no longer
  be transactional with the insert (lost or phantom jobs on crash).

## When to switch
More than one machine, sustained throughput beyond what one SQLite writer handles, or a
need for priority lanes and per-tenant fairness: move to Postgres `SKIP LOCKED` first,
then a broker (SQS, Pub/Sub, Kafka) if fan-out grows.
