# ADR-002: SQLite (WAL) + SQLAlchemy instead of Postgres

**Status:** Accepted

## Context
A solo build with a one-day deadline, judged on whether it works on a reviewer's laptop.
Expected scale: 300 items (demo) to about 10,000 items (real run), a handful of writer
threads in one process.

## Decision
- SQLite in WAL mode (`synchronous=NORMAL`, `busy_timeout=30s`, foreign keys on) through
  SQLAlchemy 2.x ORM.
- Two session kinds: `Database.read()` uses a deferred transaction (WAL readers never block
  the writer); `Database.write()` uses `BEGIN IMMEDIATE`, taking the write lock up front so
  read-modify-write sequences (job claims, centroid updates) cannot hit `SQLITE_BUSY` on
  lock upgrade and are serialisable.
- All SQL lives in `signalhq/repositories/`. Pipeline code calls repository methods only.

## Alternatives rejected
- **Postgres:** better concurrency (row locks, `SKIP LOCKED`) but needs a server, which
  judges would have to run. Not worth it at this scale.
- **Raw `sqlite3`:** less code, but no ORM models to document the schema and no clean path
  to Postgres.

## Consequences and migration path
- Single-file deployment; `python -m signalhq demo` needs nothing else.
- One writer at a time. Fine for thousands of items per minute in one process; it is the
  first limit to hit at 100x.
- Moving to Postgres: change the engine URL, replace the SQLite-specific bits in the
  repositories (`INSERT OR IGNORE`, `ON CONFLICT`, partial index syntax, the
  `BEGIN IMMEDIATE` listener) and switch the job claim to `FOR UPDATE SKIP LOCKED`.
