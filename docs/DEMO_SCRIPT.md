# Demo script (3:00)

Setup before recording: run `python -m signalhq demo` once (so the cache is warm), open
http://127.0.0.1:8000 at 1280 px, and have `docs/ARCHITECTURE.md` (the HLD diagram) in a
second tab. If you have a real run with a key, use that database and say so; otherwise say
"offline demo mode" once, clearly. Replace every `[value]` with numbers from
`eval/report.md`. Never say a number that is not in that report.

## 0:00–0:20 The problem
- "Feedback is everywhere: tickets, tweets, reviews, calls. Teams read a sample and
  prioritise by gut, or by whoever is loudest."
- "The costly mistake: fixing what many free users mention while paying customers quietly
  churn over something else."
- "Signal turns that feedback into a ranked list of what to fix next, by revenue at risk,
  with the quotes behind every decision."

## 0:20–1:40 Replay: watch a theme emerge
- Press **Replay sample**. "This streams 60 days of feedback in about 90 seconds. The data
  here is a synthetic sample; revenue is simulated, and the UI says so." (With real data,
  say "real public support tweets".)
- Point at the KPI row filling up and themes appearing; names arrive a moment later.
- "Ads is the loudest theme: [n] mentions. It sits low because almost all of it comes from
  free accounts."
- When the crash theme gets its **new** badge and climbs to #1: "In the last days a crash
  after an update starts. Within [12–15] messages it is ranked first."
- Click it. Read **Why it is ranked here**: the formula with this theme's numbers filled in.
  "Revenue at risk counts each account once, only when the message signals churn or is
  high severity. Growth is recent week over previous week. No language model decides the
  ranking."
- Scroll the evidence: "Every quote is verbatim, checked against the original message, with
  its source and plan."

## 1:40–2:20 Architecture in one diagram
- Show the HLD. "One process, one SQLite file."
- "Ingestion dedupes by content hash and enqueues work in the same transaction."
- "A database-backed queue: atomic claims, leases, retries with backoff, dead-letter."
- "Muse Spark does two jobs, through one gateway: structured enrichment with a strict JSON
  schema, and naming themes. Voice Transcribe turns call recordings into items. The gateway
  adds caching, rate limits, retries, a circuit breaker, schema validation and a cost ledger
  with a budget cap."
- "Embeddings are local; clustering is online with periodic re-clustering; scoring is a pure
  function with unit tests."

## 2:20–2:50 Measured results
- From `eval/report.md`: type accuracy [x] and macro-F1 [y] on [n] human labels;
  product-area accuracy [z]; cost per 1,000 items $[c]; p50/p95 enrichment latency [a]/[b] ms;
  cache hit rate [h].
- If those are not measured yet, say so: "Model accuracy is not measured yet." Then the
  synthetic numbers, labelled: "On the synthetic set the pipeline recovers 8 of 8 planted
  themes, adjusted Rand index 0.987."

## 2:50–3:00 Limits and next
- "Revenue is simulated; next is joining real billing data."
- "Next: per-area clustering and Postgres for scale, and screenshot attachments through
  Spark's image input."
