# Backlog

MoSCoW tags: **M**ust, **S**hould, **C**ould, **W**on't (this time). Status reflects the code
in this repository; "needs human" items are on the "Needs you" list in `PROGRESS.md`.

## Sprint 0: verify before building

| ID | Tag | Story | Acceptance criteria | Status |
|---|---|---|---|---|
| S0.1 | M | `check` command | Lists models, makes one strict-JSON Spark call, prints cost, reports missing models | Built; real API not verified (no key, host blocked in build environment) |
| S0.2 | M | Voice Transcribe client | Request plumbing tested on a fixture WAV; real response seen or reason recorded | Built and tested with a mock; real response not seen (docs and API unreachable) |
| S0.3 | M | Dataset probe | Validate twcs columns, compute brand volumes, choose brand | Built (`scripts/probe_twcs.py`); waiting for `twcs.csv` |
| S0.4 | M | Product brief and backlog | `docs/PRODUCT.md`, `docs/BACKLOG.md` | Done |

## Sprint 1: core pipeline

| ID | Tag | Story | Acceptance criteria | Status |
|---|---|---|---|---|
| S1.1 | M | Skeleton, config, models, repositories, state machine | State machine tests | Done |
| S1.2 | M | Connectors jsonl/csv/twcs + dedupe | Ingesting the same file twice creates zero new rows (test) | Done |
| S1.3 | M | Job queue + worker | Tests: expired lease reclaimed, max-attempt dead-letter, no double processing with 2 workers | Done |
| S1.4 | M | LLM gateway | Fake-transport tests for 429, timeouts, invalid JSON, cache hits | Done |
| S1.5 | M | Enrich stage with quote verification | Run on 50 real items and inspect | Built and tested offline; real run waits for key + data |
| S1.6 | M | Synthetic seed + offline `demo` | Runs end to end with no key | Done |

## Sprint 2: intelligence + API

| ID | Tag | Story | Acceptance criteria | Status |
|---|---|---|---|---|
| S2.1 | M | Embeddings + online clustering + re-cluster | Recovers planted themes (ARI reported); tau justified in an ADR | Done (hashing embedder; MiniLM not measured) |
| S2.2 | M | Theme naming with change threshold | Renames only after growth | Done |
| S2.3 | M | Deterministic scoring + unit tests | Edge cases in §6 covered | Done |
| S2.4 | M | FastAPI read API + idempotent webhook | OpenAPI at `/docs` | Done |
| S2.5 | S | Replay driver | Theme emerges during replay | Done (CLI, API and dashboard button) |
| S2.6 | S | Voice ingestion path | Speaker turns become `source=call` items | Built and tested with a mock; needs recordings + key |

## Sprint 3: product surface + proof

| ID | Tag | Story | Acceptance criteria | Status |
|---|---|---|---|---|
| S3.1 | M | Dashboard + Playwright screenshots | Clean at 1280 px and phone width | Done (`docs/screenshots/`) |
| S3.2 | M | Eval tooling | Human labels, then `eval report` | Tooling done; labels need a human |
| S3.3 | M | Real run (3,000-10,000 items) | Measured throughput, cost, latency | Waits for key + `twcs.csv` |
| S3.4 | S | `/api/metrics` + pipeline health panel | Queue, dead jobs, p95, cache hit rate | Done |
| S3.5 | C | Dockerfile + GitHub Actions tests | CI runs `pytest` | Written; Dockerfile not built here (no Docker daemon); CI runs on push |

## Sprint 4: submission pack

README, ARCHITECTURE, DEMO_SCRIPT, SUBMISSION, RESUME drafted. Model numbers are marked
"not yet measured" / [fill] until the real run and labels exist.

## Won't (future work)

Ticket management, replying to customers, CRM, auth/SSO, multi-tenant billing, Slack/Jira
integrations, mobile apps, Postgres migration, a hosted deployment.
