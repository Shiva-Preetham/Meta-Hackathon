# ADR-001: Scope of Meta models

**Status:** Accepted

## Context
The hackathon provides the Meta Model API: Muse Spark (chat, structured output, images),
Muse Voice Transcribe (speech to text with turns and diarization), SAM 3.1 (segmentation)
and Muse Image. Signal's input is text (tickets, posts, reviews) and call audio.

## Decision
- **Muse Spark** (`muse-spark-1.3`, configurable via `SIGNAL_SPARK_MODEL`) does two jobs:
  per-item enrichment with a strict JSON schema (type, product area, severity, sentiment,
  churn signal, verbatim key quote, language) and naming/summarising themes.
- **Voice Transcribe** (`muse-voice-transcribe-1.0`) turns call recordings into speaker
  turns; each turn becomes a feedback item with `source=call`.
- **SAM 3.1 and Muse Image are deliberately unused.** Signal has no images to segment or
  generate. Screenshots attached to tickets could one day go to Spark's image input, but
  that is future work, not a reason to add a model now.

## Consequences
- Every model call goes through `LLMGateway` (cache, rate limit, retry, breaker, schema
  check, ledger, budget cap).
- The model extracts and names; it never ranks (ADR-006).
- Without a key the same pipeline runs on a labelled offline heuristic, so the demo and
  tests never depend on the network.
- Unverified at the time of writing: the API itself (no key and no network access in the
  build environment), `reasoning_effort` support (the transport drops it if rejected) and
  the Voice Transcribe `request` field names (isolated in `voice.build_request`).
