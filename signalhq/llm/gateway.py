"""LLM gateway: the only path to any model (CLAUDE.md §4, §11 rule 4).

For every structured call it applies, in order:

1. response cache keyed by (model, prompt hash, schema name + version);
2. budget check against the ledger (stop at the configured cap);
3. token-bucket rate limiting (requests and tokens per minute);
4. circuit breaker (fail fast while the API is unhealthy);
5. retries with exponential backoff and full jitter on transient errors;
6. JSON parsing and schema validation, with one repair round-trip;
7. a ledger row for every attempt (tokens, cost, latency, status).
"""

from __future__ import annotations

import hashlib
import json
import random
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any, TypeVar

from signalhq.config import Settings
from signalhq.db import Database, utcnow
from signalhq.llm.errors import (
    BudgetExceededError,
    PermanentError,
    SchemaValidationError,
    TransientError,
)
from signalhq.llm.prompts import REPAIR_PROMPT
from signalhq.llm.resilience import CircuitBreaker, TokenBucket, backoff_delay
from signalhq.llm.transport import ChatRequest, ChatResult, ChatTransport
from signalhq.llm.validation import validate
from signalhq.llm.voice import VoiceTransport, inspect_wav
from signalhq.repositories import LedgerRepo

T = TypeVar("T")


def stable_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """Rough upper estimate (4 characters per token) used only for budgeting and limits."""
    return sum(len(str(m.get("content", ""))) for m in messages) // 4 + 16


class LLMGateway:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        transport: ChatTransport,
        voice_transport: VoiceTransport | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self.db = db
        self.settings = settings
        self.transport = transport
        self.voice_transport = voice_transport
        self._clock = clock
        self._sleep = sleep
        self._rng = rng or random.Random()
        self._now = now
        self.request_bucket = TokenBucket(
            settings.llm_requests_per_minute / 60, max(1.0, settings.llm_requests_per_minute / 60), clock, sleep
        )
        self.token_bucket = TokenBucket(
            settings.llm_tokens_per_minute / 60, max(1000.0, settings.llm_tokens_per_minute / 60), clock, sleep
        )
        self.breaker = CircuitBreaker(settings.breaker_failure_threshold, settings.breaker_reset_s, clock)
        self.voice_breaker = CircuitBreaker(settings.breaker_failure_threshold, settings.breaker_reset_s, clock)

    # --- public API ---------------------------------------------------------------------
    @property
    def model(self) -> str:
        return self.settings.effective_model

    def structured(
        self,
        *,
        purpose: str,
        messages: list[dict[str, Any]],
        schema: dict[str, Any],
        schema_name: str,
        schema_version: str = "v1",
        max_tokens: int = 400,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Return validated JSON for ``messages``, from cache when possible."""
        model = model or self.model
        try:
            return self._structured_for_model(purpose, messages, schema, schema_name, schema_version, max_tokens, model)
        except (TransientError, PermanentError, SchemaValidationError):
            fallback = self.settings.fallback_model
            if self.settings.is_offline or not fallback or fallback == model:
                raise
            return self._structured_for_model(purpose, messages, schema, schema_name, schema_version, max_tokens, fallback)

    def transcribe(self, wav: bytes, request: dict[str, Any]) -> dict[str, Any]:
        """Transcribe one WAV file through cache, budget, breaker, retry and ledger."""
        if self.voice_transport is None:
            raise PermanentError("no voice transport configured (offline mode needs a saved transcript)")
        info = inspect_wav(wav)
        model = self.settings.voice_model
        prompt_hash = hashlib.sha256(wav + json.dumps(request, sort_keys=True).encode()).hexdigest()
        key = stable_hash({"model": model, "prompt": prompt_hash, "schema": "asr-v1"})
        cached = self._cache_get(key)
        if cached is not None:
            self._record(model, "transcribe", prompt_hash, cache_hit=True, status="ok")
            return json.loads(cached)
        cost = info.duration_s / 3600 * self.settings.price_voice_per_hour
        self._check_budget(cost)

        def call() -> dict[str, Any]:
            return self.voice_transport.transcribe(wav, request, self.settings.llm_timeout_s)  # type: ignore[union-attr]

        payload, latency_ms = self._with_retries(call, model, "transcribe", prompt_hash, self.voice_breaker)
        self._record(model, "transcribe", prompt_hash, cache_hit=False, status="ok", cost_usd=cost, latency_ms=latency_ms)
        self._cache_put(key, model, "transcribe", json.dumps(payload))
        return payload

    def spent_usd(self) -> float:
        with self.db.read() as s:
            return LedgerRepo(s).total_cost()

    # --- internals ----------------------------------------------------------------------
    def _structured_for_model(
        self,
        purpose: str,
        messages: list[dict[str, Any]],
        schema: dict[str, Any],
        schema_name: str,
        schema_version: str,
        max_tokens: int,
        model: str,
    ) -> dict[str, Any]:
        prompt_hash = stable_hash(messages)
        key = stable_hash({"model": model, "prompt": prompt_hash, "schema": f"{schema_name}:{schema_version}"})
        started = self._clock()
        cached = self._cache_get(key)
        if cached is not None:
            latency = (self._clock() - started) * 1000
            self._record(model, purpose, prompt_hash, cache_hit=True, status="ok", latency_ms=latency)
            return json.loads(cached)

        request = ChatRequest(
            model=model,
            messages=messages,
            schema=schema,
            schema_name=schema_name,
            max_tokens=max_tokens,
            reasoning_effort=None if self.settings.is_offline else self.settings.reasoning_effort,
        )
        data = self._validated_call(purpose, prompt_hash, request)
        self._cache_put(key, model, purpose, json.dumps(data, sort_keys=True))
        return data

    def _validated_call(self, purpose: str, prompt_hash: str, request: ChatRequest) -> dict[str, Any]:
        result, latency = self._chat(purpose, prompt_hash, request)
        data, errors = _parse_and_validate(result.content, request.schema)
        self._record_result(request.model, purpose, prompt_hash, result, latency, errors)
        if not errors:
            return data  # type: ignore[return-value]

        # One repair round-trip: show the model its answer and the validation errors.
        repair = ChatRequest(
            model=request.model,
            messages=[
                *request.messages,
                {"role": "assistant", "content": result.content},
                {"role": "user", "content": REPAIR_PROMPT.format(errors="; ".join(errors[:10]))},
            ],
            schema=request.schema,
            schema_name=request.schema_name,
            max_tokens=request.max_tokens,
            reasoning_effort=request.reasoning_effort,
        )
        result, latency = self._chat(f"{purpose}:repair", prompt_hash, repair)
        data, errors = _parse_and_validate(result.content, request.schema)
        self._record_result(request.model, f"{purpose}:repair", prompt_hash, result, latency, errors)
        if errors:
            raise SchemaValidationError("; ".join(errors[:10]))
        return data  # type: ignore[return-value]

    def _chat(self, purpose: str, prompt_hash: str, request: ChatRequest) -> tuple[ChatResult, float]:
        est_in = estimate_tokens(request.messages)
        est_cost = (
            est_in * self.settings.price_input_per_m + request.max_tokens * self.settings.price_output_per_m
        ) / 1e6
        if self.transport.billable:
            self._check_budget(est_cost)

        def acquire() -> None:
            # Rate limits protect the remote API; the offline transport has none to respect.
            if self.transport.billable:
                self.request_bucket.acquire(1)
                self.token_bucket.acquire(est_in + request.max_tokens)

        def call() -> ChatResult:
            return self.transport.complete(request, self.settings.llm_timeout_s)

        return self._with_retries(call, request.model, purpose, prompt_hash, self.breaker, before=acquire)

    def _with_retries(
        self,
        call: Callable[[], T],
        model: str,
        purpose: str,
        prompt_hash: str,
        breaker: CircuitBreaker,
        before: Callable[[], None] | None = None,
    ) -> tuple[T, float]:
        """Run ``call`` with breaker and retries. Latency excludes rate-limiter waits."""
        max_retries = self.settings.llm_max_retries
        for attempt in range(max_retries + 1):
            breaker.before_call()  # raises CircuitOpenError (transient) while open
            if before is not None:
                before()
            started = self._clock()
            try:
                result = call()
            except TransientError as exc:
                latency = (self._clock() - started) * 1000
                breaker.record_failure()
                self._record(model, purpose, prompt_hash, cache_hit=False, status="transient_error",
                             latency_ms=latency, error=str(exc))
                if attempt >= max_retries:
                    raise
                self._sleep(
                    backoff_delay(attempt, self.settings.llm_backoff_base_s, self.settings.llm_backoff_max_s,
                                  exc.retry_after, self._rng)
                )
                continue
            except PermanentError as exc:
                breaker.record_neutral()
                self._record(model, purpose, prompt_hash, cache_hit=False, status="permanent_error",
                             latency_ms=(self._clock() - started) * 1000, error=str(exc))
                raise
            breaker.record_success()
            return result, (self._clock() - started) * 1000
        raise AssertionError("unreachable")  # pragma: no cover

    def _check_budget(self, estimated_cost: float) -> None:
        spent = self.spent_usd()
        if spent + estimated_cost > self.settings.budget_usd:
            raise BudgetExceededError(
                f"spend would exceed the ${self.settings.budget_usd:.2f} budget "
                f"(spent ${spent:.4f}, next call up to ${estimated_cost:.4f})"
            )

    def _cost(self, result: ChatResult) -> float:
        if not self.transport.billable:
            return 0.0
        uncached = max(0, result.input_tokens - result.cached_tokens)
        return (
            uncached * self.settings.price_input_per_m
            + result.cached_tokens * self.settings.price_cached_input_per_m
            + result.output_tokens * self.settings.price_output_per_m
        ) / 1e6

    def _record_result(
        self, model: str, purpose: str, prompt_hash: str, result: ChatResult, latency: float, errors: list[str]
    ) -> None:
        self._record(
            model,
            purpose,
            prompt_hash,
            cache_hit=False,
            status="ok" if not errors else "invalid_output",
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cached_tokens=result.cached_tokens,
            cost_usd=self._cost(result),
            latency_ms=latency,
            error="; ".join(errors[:5]) if errors else None,
        )

    def _record(self, model: str, purpose: str, prompt_hash: str, *, cache_hit: bool, status: str, **fields: Any) -> None:
        with self.db.write() as s:
            LedgerRepo(s).record(
                model=model, purpose=purpose, prompt_hash=prompt_hash, cache_hit=cache_hit,
                status=status, now=self._now(), **fields,
            )

    def _cache_get(self, key: str) -> str | None:
        with self.db.read() as s:
            return LedgerRepo(s).cache_get(key)

    def _cache_put(self, key: str, model: str, purpose: str, response_json: str) -> None:
        with self.db.write() as s:
            LedgerRepo(s).cache_put(key, model, purpose, response_json, self._now())


def _parse_and_validate(content: str, schema: dict[str, Any]) -> tuple[Any, list[str]]:
    text = content.strip()
    if text.startswith("```"):
        # Tolerate a fenced reply; strict mode should prevent it, but cheap to handle.
        text = text.strip("`")
        text = text[text.find("{"):] if "{" in text else text
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, [f"invalid JSON: {exc.msg} at position {exc.pos}"]
    return data, validate(data, schema)
