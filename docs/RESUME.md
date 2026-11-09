# Resume bullets and interview notes

Fill the bracketed numbers from `eval/report.md` after the real run. Until then, use only
the numbers that are already measured (marked "measured").

## Resume bullets
- Built Signal, a feedback-prioritisation service (Python, FastAPI, SQLAlchemy/SQLite, Meta
  Muse Spark) that enriches support messages with strict-schema LLM output, clusters them
  online and ranks themes by a deterministic revenue-at-risk score; [type accuracy x on n
  human labels], [cost $c per 1,000 items].
- Designed a database-backed job queue with atomic claims, leases, fencing tokens,
  jittered retries and dead-lettering, plus an LLM gateway (cache, token-bucket rate limiter,
  circuit breaker, schema validation with repair, cost ledger, budget cap); 128 automated
  tests, including crash recovery and no double processing across concurrent workers
  (measured).
- Chose clustering thresholds with a reproducible sweep: 8 of 8 planted themes recovered on
  a synthetic set, adjusted Rand index 0.987 (measured; synthetic data, offline mode).

## Talking points: design decisions
- **The LLM never ranks.** It extracts facts per item; a pure, unit-tested function ranks.
  Auditable, stable between runs, explainable on screen.
- **Exactly-once effects on an at-least-once queue.** Transactional enqueue (outbox), atomic
  `UPDATE ... RETURNING` claims, leases for crash recovery, fencing on ack, idempotent stages.
- **`BEGIN IMMEDIATE` for writes.** Deferred SQLite transactions that read then write can
  fail on lock upgrade; taking the write lock first makes read-modify-write safe.
- **Gateway as a choke point.** One place for cost control, caching, retries and validation;
  every attempt is a ledger row, so cost and latency are measured, not estimated.
- **Verbatim quotes.** Model quotes are checked against the source text; evidence is never
  invented.
- **Offline mode.** The same pipeline runs with a labelled heuristic transport, so tests and
  the demo never depend on the network.

## Trade-offs rejected
- Postgres and Redis/Celery: better concurrency, but two extra services for a reviewer to run.
- LLM-produced rankings: cannot be audited or tested.
- Batch-only clustering: simpler, but slow to surface new themes and unstable theme ids.
- Hosted embeddings: per-item cost and a network dependency for little gain at this scale.

## At 100x scale
- Postgres with `SKIP LOCKED`, then a broker when workers span machines.
- Enrichment is the cost driver: micro-batching (ADR-007), caching, cheaper routing for
  low-value items.
- Clustering per product area and an approximate nearest-neighbour index for assignment.
- Incremental scoring per theme; read replicas or a materialised ranking for the API.
- Real revenue from billing/CRM, multi-tenancy and auth.
