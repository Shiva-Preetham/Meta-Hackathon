# Architecture decision records

| ADR | Decision | Status |
|---|---|---|
| [001](ADR-001-meta-models.md) | Muse Spark for enrichment and naming, Voice Transcribe for calls; SAM and Muse Image unused | Accepted |
| [002](ADR-002-sqlite.md) | SQLite in WAL mode + SQLAlchemy, all SQL behind repositories | Accepted |
| [003](ADR-003-db-queue.md) | Database-backed job queue instead of Redis/Celery | Accepted |
| [004](ADR-004-local-embeddings.md) | Local embeddings: MiniLM preferred, deterministic hashing fallback | Accepted |
| [005](ADR-005-online-clustering.md) | Online assignment + periodic re-clustering; thresholds from a measured sweep | Accepted (hashing measured; MiniLM pending) |
| [006](ADR-006-deterministic-scoring.md) | Deterministic priority score; the LLM never ranks | Accepted |
| [007](ADR-007-enrichment-batching.md) | Per-item enrichment now; micro-batch decided by measurement | Proposed (measurement pending) |
