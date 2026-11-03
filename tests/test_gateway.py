import json

import httpx
import pytest

from signalhq.domain.schemas import ENRICH_SCHEMA, THEME_SCHEMA
from signalhq.llm.errors import (
    BudgetExceededError,
    CircuitOpenError,
    PermanentError,
    SchemaValidationError,
    TransientError,
)
from signalhq.llm.resilience import CircuitBreaker, TokenBucket, backoff_delay
from signalhq.llm.transport import ChatRequest, ChatResult, OpenAICompatTransport
from signalhq.repositories import LedgerRepo

GOOD = {"name": "Downloads vanish", "summary": "Customers lose downloads."}
MESSAGES = [{"role": "user", "content": "name this"}]


def call(gw, messages=MESSAGES, schema=THEME_SCHEMA, name="theme_name"):
    return gw.structured(purpose="name_theme", messages=messages, schema=schema, schema_name=name, max_tokens=50)


def ledger(db):
    with db.read() as s:
        return LedgerRepo(s).summary()


def test_429_is_retried_with_backoff_and_ledgered(scripted_gateway, db):
    gw, transport, sleeps = scripted_gateway([TransientError("rate limited", retry_after=2.0, status=429), GOOD])
    assert call(gw) == GOOD
    assert len(transport.requests) == 2
    assert sleeps and sleeps[0] >= 2.0  # Retry-After respected
    p = ledger(db)["purposes"]["name_theme"]
    assert p["calls"] == 2 and p["errors"] == 1


def test_timeouts_exhaust_retries_then_raise(scripted_gateway):
    gw, transport, sleeps = scripted_gateway([TransientError("timeout")] * 3, llm_max_retries=2,
                                             breaker_failure_threshold=10)
    with pytest.raises(TransientError):
        call(gw)
    assert len(transport.requests) == 3
    assert len(sleeps) == 2


def test_invalid_json_gets_one_repair_round_trip(scripted_gateway, db):
    gw, transport, _ = scripted_gateway(["not json at all", GOOD])
    assert call(gw) == GOOD
    repair = transport.requests[1]
    assert repair.messages[-2] == {"role": "assistant", "content": "not json at all"}
    assert "invalid JSON" in repair.messages[-1]["content"]
    purposes = ledger(db)["purposes"]
    assert purposes["name_theme"]["errors"] == 1  # the invalid first answer
    assert purposes["name_theme:repair"]["calls"] == 1


def test_schema_violation_after_repair_raises(scripted_gateway):
    gw, _, _ = scripted_gateway([{"name": "x"}, {"name": "x", "extra": 1}])
    with pytest.raises(SchemaValidationError, match="summary"):
        call(gw)


def test_cache_hit_skips_the_transport_and_costs_nothing(scripted_gateway, db):
    gw, transport, _ = scripted_gateway([GOOD])
    assert call(gw) == GOOD
    assert call(gw) == GOOD
    assert len(transport.requests) == 1
    summary = ledger(db)
    assert summary["cache_hits"] == 1 and summary["cache_hit_rate"] == 0.5
    # cost: 1000 input tokens at $1.25/M + 100 output tokens at $4.25/M
    assert summary["cost_usd"] == pytest.approx(1000 * 1.25e-6 + 100 * 4.25e-6)


def test_cache_key_includes_schema_version(scripted_gateway):
    gw, transport, _ = scripted_gateway([GOOD, GOOD])
    gw.structured(purpose="p", messages=MESSAGES, schema=THEME_SCHEMA, schema_name="theme_name", schema_version="1")
    gw.structured(purpose="p", messages=MESSAGES, schema=THEME_SCHEMA, schema_name="theme_name", schema_version="2")
    assert len(transport.requests) == 2


def test_cached_tokens_are_priced_at_the_cached_rate(scripted_gateway, db):
    result = ChatResult(content=json.dumps(GOOD), input_tokens=1000, output_tokens=0, cached_tokens=800)
    gw, _, _ = scripted_gateway([result])
    call(gw)
    assert ledger(db)["cost_usd"] == pytest.approx((200 * 1.25 + 800 * 0.15) / 1e6)


def test_breaker_opens_after_consecutive_failures(scripted_gateway):
    gw, transport, _ = scripted_gateway([TransientError("503")] * 2, llm_max_retries=0, breaker_failure_threshold=2)
    for _ in range(2):
        with pytest.raises(TransientError):
            call(gw, messages=[{"role": "user", "content": f"m{_}"}])
    with pytest.raises(CircuitOpenError):
        call(gw, messages=[{"role": "user", "content": "m3"}])
    assert len(transport.requests) == 2  # the third call never reached the API


def test_permanent_errors_are_not_retried(scripted_gateway):
    gw, transport, sleeps = scripted_gateway([PermanentError("401 unauthorized")])
    with pytest.raises(PermanentError):
        call(gw)
    assert len(transport.requests) == 1 and sleeps == []


def test_budget_cap_stops_calls(scripted_gateway, db):
    gw, transport, _ = scripted_gateway([GOOD, GOOD], budget_usd=0.0018)
    call(gw)
    with pytest.raises(BudgetExceededError):
        call(gw, messages=[{"role": "user", "content": "another"}])
    assert len(transport.requests) == 1


def test_offline_gateway_produces_valid_enrichment(gateway):
    out = gateway.structured(
        purpose="enrich",
        messages=[{"role": "user", "content": "<feedback>\nI was charged twice, refund me.\n</feedback>"}],
        schema=ENRICH_SCHEMA,
        schema_name="feedback_enrichment",
    )
    assert out["type"] == "billing" and out["churn_signal"] is True
    assert out["key_quote"] in "I was charged twice, refund me."


# --- resilience primitives -------------------------------------------------------------


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_token_bucket_blocks_when_empty():
    clock = FakeClock()
    bucket = TokenBucket(rate_per_s=2.0, capacity=2.0, clock=clock, sleep=clock.sleep)
    assert bucket.acquire() == 0 and bucket.acquire() == 0
    assert bucket.acquire() == pytest.approx(0.5)
    assert clock.t == pytest.approx(0.5)


def test_breaker_half_open_trial_closes_or_reopens():
    clock = FakeClock()
    breaker = CircuitBreaker(failure_threshold=1, reset_timeout_s=10, clock=clock)
    breaker.before_call()
    breaker.record_failure()
    assert breaker.state == "open"
    with pytest.raises(CircuitOpenError):
        breaker.before_call()
    clock.t = 11
    breaker.before_call()  # trial allowed
    with pytest.raises(CircuitOpenError):
        breaker.before_call()  # only one trial at a time
    breaker.record_failure()
    assert breaker.state == "open"
    clock.t = 22
    breaker.before_call()
    breaker.record_success()
    assert breaker.state == "closed"


def test_backoff_respects_cap_and_retry_after():
    import random

    rng = random.Random(3)
    assert all(0 <= backoff_delay(a, 1.0, 8.0, None, rng) <= 8.0 for a in range(10))
    assert backoff_delay(0, 1.0, 8.0, 5.0, rng) >= 5.0


# --- OpenAI-compatible transport against a mocked HTTP server ------------------------


def _completion(content: str) -> dict:
    return {
        "id": "x", "object": "chat.completion", "created": 0, "model": "muse-spark-1.3",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
    }


def _transport_with(handler) -> OpenAICompatTransport:
    from openai import OpenAI

    transport = OpenAICompatTransport(base_url="https://api.example/v1", api_key="k")
    transport._client = OpenAI(
        base_url="https://api.example/v1", api_key="k", max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    return transport


REQ = ChatRequest(model="muse-spark-1.3", messages=MESSAGES, schema=THEME_SCHEMA, schema_name="theme_name",
                  max_tokens=50, reasoning_effort="low")


def test_transport_sends_strict_json_schema_and_reads_usage():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, json=_completion(json.dumps(GOOD)))

    result = _transport_with(handler).complete(REQ, 5)
    assert json.loads(result.content) == GOOD
    assert (result.input_tokens, result.output_tokens) == (120, 30)
    assert seen["response_format"]["type"] == "json_schema"
    assert seen["response_format"]["json_schema"]["strict"] is True
    assert seen["auth"] == "Bearer k"


def test_transport_drops_rejected_reasoning_effort_and_remembers():
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if "reasoning_effort" in body:
            return httpx.Response(400, json={"error": {"message": "Unknown parameter: reasoning_effort"}})
        return httpx.Response(200, json=_completion(json.dumps(GOOD)))

    transport = _transport_with(handler)
    transport.complete(REQ, 5)
    transport.complete(REQ, 5)
    assert "reasoning_effort" in transport.dropped_params
    assert len(bodies) == 3  # one rejected attempt, then two without the parameter


@pytest.mark.parametrize("status,error", [(429, TransientError), (503, TransientError), (401, PermanentError)])
def test_transport_maps_http_errors(status, error):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": "nope"}}, headers={"retry-after": "3"})

    with pytest.raises(error) as info:
        _transport_with(handler).complete(REQ, 5)
    if status == 429:
        assert info.value.retry_after == 3.0
