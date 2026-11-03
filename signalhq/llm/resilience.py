"""Client-side rate limiting, backoff and circuit breaking. Thread-safe, clock-injectable."""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable

from signalhq.llm.errors import CircuitOpenError


class TokenBucket:
    """Classic token bucket: ``rate`` tokens per second, burst up to ``capacity``."""

    def __init__(
        self,
        rate_per_s: float,
        capacity: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.rate = rate_per_s
        self.capacity = capacity
        self._tokens = capacity
        self._clock = clock
        self._sleep = sleep
        self._last = clock()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = self._clock()
        self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
        self._last = now

    def acquire(self, amount: float = 1.0) -> float:
        """Block until ``amount`` tokens are available. Returns seconds waited."""
        amount = min(amount, self.capacity)
        waited = 0.0
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= amount:
                    self._tokens -= amount
                    return waited
                wait = (amount - self._tokens) / self.rate
            self._sleep(wait)
            waited += wait


def backoff_delay(
    attempt: int,
    base_s: float,
    max_s: float,
    retry_after: float | None = None,
    rng: random.Random | None = None,
) -> float:
    """Exponential backoff with full jitter; never shorter than the server's Retry-After."""
    rng = rng or random
    cap = min(max_s, base_s * (2**attempt))
    delay = rng.uniform(0, cap)
    if retry_after is not None:
        delay = max(delay, retry_after)
    return delay


class CircuitBreaker:
    """closed -> open after N consecutive failures -> half-open after a cool-down, where a
    single trial call decides between closed and open again."""

    CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"

    def __init__(
        self,
        failure_threshold: int,
        reset_timeout_s: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.reset_timeout_s = reset_timeout_s
        self._clock = clock
        self._state = self.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._trial_in_flight = False
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        with self._lock:
            if self._state == self.OPEN and self._clock() - self._opened_at >= self.reset_timeout_s:
                return self.HALF_OPEN
            return self._state

    def before_call(self) -> None:
        with self._lock:
            if self._state == self.OPEN:
                if self._clock() - self._opened_at < self.reset_timeout_s:
                    remaining = self.reset_timeout_s - (self._clock() - self._opened_at)
                    raise CircuitOpenError("circuit breaker is open", retry_after=remaining)
                self._state = self.HALF_OPEN
            if self._state == self.HALF_OPEN:
                if self._trial_in_flight:
                    raise CircuitOpenError("circuit breaker trial call in flight", retry_after=1.0)
                self._trial_in_flight = True

    def record_success(self) -> None:
        with self._lock:
            self._state = self.CLOSED
            self._failures = 0
            self._trial_in_flight = False

    def record_failure(self) -> None:
        with self._lock:
            self._trial_in_flight = False
            if self._state == self.HALF_OPEN:
                self._state = self.OPEN
                self._opened_at = self._clock()
                return
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._state = self.OPEN
                self._opened_at = self._clock()

    def record_neutral(self) -> None:
        """A call that failed for a reason unrelated to API health (e.g. a 400)."""
        with self._lock:
            self._trial_in_flight = False
            if self._state == self.HALF_OPEN:
                self._state = self.CLOSED
                self._failures = 0
