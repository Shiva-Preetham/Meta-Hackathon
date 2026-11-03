"""Error taxonomy shared by transports, the gateway and the worker.

The worker's behaviour depends only on the class: transient errors are retried with
backoff, permanent errors dead-letter the job immediately, and a budget stop pauses all
work so a human can decide.
"""

from __future__ import annotations


class LLMError(Exception):
    """Base class. Unknown subclasses are treated as retryable by the worker."""


class TransientError(LLMError):
    """429, 5xx, timeouts, dropped connections. Safe to retry."""

    def __init__(self, message: str, retry_after: float | None = None, status: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after
        self.status = status


class CircuitOpenError(TransientError):
    """The breaker is open: we are not calling the API for a while."""


class PermanentError(LLMError):
    """400/401/403/404 and similar. Retrying the same request will not help."""


class UnsupportedParameterError(PermanentError):
    """The API rejected an optional parameter (e.g. reasoning_effort)."""

    def __init__(self, param: str, message: str = "") -> None:
        super().__init__(message or f"parameter not supported: {param}")
        self.param = param


class SchemaValidationError(LLMError):
    """The model's output did not match the schema, even after one repair attempt."""


class BudgetExceededError(LLMError):
    """The configured spend cap would be exceeded. Work stops; a human decides."""
