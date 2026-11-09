# Progress

## Needs you (in the order they're needed)
1. **Add a credit card** on the Meta Model API billing page (calls fail without it, even with
   credits) and set a spend-limit alert.
2. **`export MODEL_API_KEY=...`** and run `python -m signalhq check` on a machine that can reach
   `api.meta.ai`. Paste its output into the "S0.1" entry below. (This build ran in a cloud
   sandbox where `api.meta.ai`, `dev.meta.ai` and `huggingface.co` were blocked by the network
   policy and no key was set, so nothing here has touched the real API.)
3. **Verify the Voice Transcribe `request` field names** against
   https://dev.meta.ai/docs/speech-to-text and https://dev.meta.ai/docs/api-reference/voice/transcribe,
   then edit `build_request()` in `signalhq/llm/voice.py` if they differ (currently
   `{"model", "mode", "language"}`: a guess, marked UNVERIFIED).
4. **Download `twcs.csv`** from Kaggle (thoughtvector/customer-support-on-twitter) into
   `data/raw/`, check the licence on the Kaggle page, then run `python scripts/probe_twcs.py`.
5. Optional: `pip install -r requirements-embeddings.txt` (MiniLM; needs Hugging Face access),
   then `python scripts/tune_clustering.py --embedder minilm` and update `CLUSTER_DEFAULTS`
   in `signalhq/config.py` and ADR-005 with the measured values.
6. Record 2–3 short mock customer calls (30–60 s) into `data/audio/`, then
   `python -m signalhq ingest-audio data/audio/<file>.m4a`.
7. Real run (S3.3): `python -m signalhq --db signal-real.db ingest data/raw/twcs.csv --connector twcs --limit 5000`
   then `python -m signalhq --db signal-real.db worker --drain` (budget cap $15 is enforced).
8. Label about 150 items: `python -m signalhq --db signal-real.db eval sample`, then
   `python -m signalhq --db signal-real.db eval label` and open http://127.0.0.1:8000/label
   (about 45 min), then `python -m signalhq --db signal-real.db eval report`.
9. Make the GitHub repo **public**, record the 3-minute demo (`docs/DEMO_SCRIPT.md`), fill the
   measured numbers into README/SUBMISSION/RESUME, and submit by Nov 4, 23:00 IST.

## Log
- Project brief written (CLAUDE.md). Nothing built yet.
- Environment (Nov 3): Python 3.13, SQLAlchemy 2.1, OpenAI SDK 3.x, scikit-learn 1.9. Outbound
  access to `api.meta.ai`, `dev.meta.ai` and `huggingface.co` is denied by the sandbox network
  policy; PyPI works. `MODEL_API_KEY` is not set.
- S0.1 `check`: built. Output in this environment: "MODEL_API_KEY: not set ... Spark: NOT
  VERIFIED (no key)", exit code 2. Spark is **unverified**, not shown unreachable: the brief's
  stop condition (Spark unreachable because of the account) could not be tested, so work
  continued in offline demo mode, which the brief requires anyway (S1.6).
- S0.2 Voice Transcribe: client built to the verified facts in §3 (multipart `request` +
  `audio`, bearer auth, WAV mono/16-bit/16 or 24 kHz, response `turns[]`). Tested against a
  mocked HTTP endpoint with a generated fixture WAV. **No real response seen**: the docs pages
  and the API were unreachable. Field names in `request` are unverified (Needs you #3).
- S0.3 Dataset probe: `scripts/probe_twcs.py` + `TwcsConnector` (column validation, brand
  mentions or brand replies, URL/handle cleaning, most-recent `--limit`). `twcs.csv` is not
  present, so the synthetic sample is used; download is on the Needs you list.
- S0.4 `docs/PRODUCT.md` and `docs/BACKLOG.md` written.
- S1.1 Skeleton: settings (every threshold commented), SQLite WAL with separate deferred read
  and `BEGIN IMMEDIATE` write sessions, ORM models (§5 plus documented additions), repository
  layer, item state machine with tests.
- S1.2 Connectors (jsonl, csv, twcs, webhook, audio) + transactional ingest with SHA-256
  content-hash dedupe. Test: ingesting the sample twice creates zero new items, jobs or accounts.
- S1.3 DB-backed queue: atomic `UPDATE ... RETURNING` claim, lease expiry, fencing token on
  ack, retry with jittered backoff, dead-letter, dedupe keys. Tests cover crash recovery,
  dead-lettering and no double processing with two concurrent workers.
- S1.4 LLM gateway: cache, token buckets, retries with full jitter (Retry-After respected),
  circuit breaker, strict-schema validation + one repair round-trip, ledger, budget cap, optional
  fallback model. Transport drops rejected optional params (`reasoning_effort`). Tests use a
  scripted fake transport and a mocked HTTP server.
- S1.5 Enrich stage: verbatim-quote check (falls back to the first sentence), fuzzy product-area
  normalisation, idempotent. The "run on 50 real items" step is blocked on the key and data.
- S1.6 Synthetic seed set (300 items, 8 planted themes, late crash spike) + `demo` command,
  offline heuristic transport clearly labelled as such. `demo --no-serve` runs end to end.
- Found while testing: the rate limiter throttled the offline transport (30 s for 300 items) and
  its waits were counted as model latency. Fixed: limits apply only to billable transports, and
  latency now excludes limiter waits.

### Sprint 0–1 retrospective
- Went well: building the queue and gateway against fakes first made the failure paths testable
  before any real call; idempotent stages kept the worker simple.
- Didn't: the real API, its docs and Hugging Face were unreachable from the build sandbox, so
  nothing is verified online yet.
- Change: keep every unverified assumption in one place (`voice.build_request`, transport
  param dropping) and list it under "Needs you".
- Change: tune clustering with a sweep script rather than by eye (done in S2.1).
- Risk: the hashing embedder is weaker than MiniLM on paraphrases; measured, not assumed.

- S2.1 Embeddings + clustering: hashing embedder (MiniLM optional; Hugging Face blocked here).
  `scripts/tune_clustering.py` sweep on the synthetic set: quote+text alone peaks at ARI 0.668
  (15-19 themes for 8 planted); adding the product-area anchor token gives mean ARI 0.977 across
  four re-cluster cadences (worst 0.955) at tau 0.20 / link 0.10 / merge 0.70. Live pipeline:
  ARI 0.987, 8/8 themes. Caveat recorded in ADR-005: heuristic and templates share vocabulary.
- S2.2 Theme naming: names new themes, renames after 50% growth or a merge.
- S2.3 Scoring: pure functions, 12 unit tests (zero revenue, new theme, ties, one huge account,
  window edges).
- S2.4 FastAPI: ranked themes, theme detail with filled-in formula and verbatim evidence, item
  detail, summary, metrics, idempotent webhook (201 then 200), OpenAPI at /docs.
- S2.5 Replay: driver (data-time clock in `kv`), CLI, API start/stop (files restricted to
  `data/`), dashboard button. Measured on the synthetic replay (3 runs, default settings): the
  planted spike theme appears after 10-12 of its 40 messages and ranks #1 after 12-15.
  Data-hours depend on replay speed because the re-cluster delay is wall-clock time.
- S2.6 Voice path: `ingest-audio` (ffmpeg conversion, gateway-cached transcription, one item
  per speaker turn, `--transcript-json` to reuse a saved response). Tested with a generated WAV
  and a mocked endpoint; no real call made.
- S3.1 Dashboard (static, light theme): KPI row, ranked table with sparklines and "new" badges,
  detail panel with "why ranked here", trend split into previous/recent windows, plan mix,
  evidence; pipeline health. Playwright screenshots at 1280 px and 390 px; no horizontal scroll.
- S3.2 Eval: stratified seeded sample, keyboard-first /label page (hides model predictions),
  `eval report`. `eval/report-offline-synthetic.md` is the offline/synthetic run, labelled as such.
- S3.3 Real run: blocked on key + `twcs.csv` (Needs you #2, #4, #7).
- S3.4 Metrics endpoint + health panel: queue by state, stage p50/p95, throughput, cache hit
  rate, dead jobs.
- S3.5 Dockerfile + GitHub Actions (tests, offline demo, eval report on 3.11-3.13). The image was
  not built here (no Docker daemon in the sandbox).
- Bugs found in self-review and fixed: SQLite reused row ids after a replay reset, so a worker
  still processing an old item could write onto a new item with the same id (fixed with
  AUTOINCREMENT on items, jobs, themes + test); the dashboard Stop button could not stop a
  replay started by `demo --replay` (one shared controller now).
- Sprint 4: README, ARCHITECTURE (HLD, LLD, sequence, failure table), 7 ADRs, DEMO_SCRIPT,
  SUBMISSION and RESUME drafts. Model numbers are "not yet measured" / [fill] everywhere.
- Licence: MIT added (the repo must be public; change it if you prefer another licence).

### Sprint 2–3 retrospective
- Went well: measuring clustering with a sweep found the over-splitting quickly and gave a
  defensible threshold instead of a guess.
- Went well: Playwright screenshots caught a label collision on narrow charts.
- Didn't: every model-quality number still waits on the real API; the offline numbers only show
  that the plumbing works.
- Change: the product-area anchor makes clustering depend on enrichment quality; watch theme
  purity in the real eval and switch it off (`SIGNAL_EMBED_AREA_ANCHOR=0`) if it over-merges.
- Change: replay timing knobs are wall-clock; for the video, keep the default 90 s replay.

### Sprint 4 retrospective
- Went well: one source of truth per number (eval report, ledger, scripts), so docs could be
  written without inventing anything.
- Didn't: the submission and resume drafts still have [fill] slots for the real numbers.
- Change: after the real run, regenerate `eval/report.md`, then update README Results,
  SUBMISSION and RESUME from it in one pass.
- Final check (Nov 3): fresh clone + fresh virtualenv + `pip install -r requirements.txt` only,
  no `MODEL_API_KEY`, no Kaggle data: `pytest` 120 passed (1 live test deselected),
  `python -m signalhq demo --no-serve` processed the sample, `eval report` gave ARI 0.987 with
  8 of 8 planted themes (synthetic, offline mode).
- Nov 9 follow-ups: offline theme names no longer repeat the area; `ingest --limit` stops
  reading early; `check` lists likely causes when Spark fails (no payment method first);
  README troubleshooting table; first CLI tests and webhook/data-note tests. 128 tests pass.
