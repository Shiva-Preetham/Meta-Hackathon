"""Transports: the only code that talks HTTP to a model. The gateway wraps them.

``OpenAICompatTransport`` targets the Meta Model API through the OpenAI Python SDK
(CLAUDE.md §3). It maps SDK exceptions onto our error taxonomy and quietly drops optional
parameters the API rejects (``reasoning_effort`` is unverified), remembering the decision
so later calls do not pay for the failed attempt again.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Protocol

from signalhq.llm.errors import PermanentError, TransientError, UnsupportedParameterError


@dataclass(frozen=True)
class ChatRequest:
    model: str
    messages: list[dict[str, Any]]
    schema: dict[str, Any]
    schema_name: str
    max_tokens: int
    reasoning_effort: str | None = None


@dataclass(frozen=True)
class ChatResult:
    content: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0


class ChatTransport(Protocol):
    name: str
    billable: bool

    def complete(self, request: ChatRequest, timeout_s: float) -> ChatResult: ...

    def list_models(self) -> list[str]: ...


# Optional request parameters that may be dropped if the API says it does not know them.
DROPPABLE_PARAMS = ("reasoning_effort", "max_completion_tokens")


@dataclass
class OpenAICompatTransport:
    base_url: str
    api_key: str
    name: str = "meta-model-api"
    billable: bool = True
    dropped_params: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        from openai import OpenAI

        # max_retries=0: retries belong to the gateway, which logs every attempt.
        self._client = OpenAI(base_url=self.base_url, api_key=self.api_key, max_retries=0)
        self._lock = threading.Lock()

    def _kwargs(self, request: ChatRequest, timeout_s: float) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": request.messages,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": request.schema_name, "strict": True, "schema": request.schema},
            },
            "timeout": timeout_s,
        }
        if "max_completion_tokens" not in self.dropped_params:
            kwargs["max_completion_tokens"] = request.max_tokens
        else:
            kwargs["max_tokens"] = request.max_tokens
        if request.reasoning_effort and "reasoning_effort" not in self.dropped_params:
            kwargs["reasoning_effort"] = request.reasoning_effort
        return kwargs

    def complete(self, request: ChatRequest, timeout_s: float) -> ChatResult:
        for _ in range(len(DROPPABLE_PARAMS) + 1):
            try:
                return self._complete_once(request, timeout_s)
            except UnsupportedParameterError as exc:
                with self._lock:
                    if exc.param in self.dropped_params:
                        raise
                    self.dropped_params.add(exc.param)
        raise PermanentError("request rejected after dropping optional parameters")

    def _complete_once(self, request: ChatRequest, timeout_s: float) -> ChatResult:
        import openai

        kwargs = self._kwargs(request, timeout_s)
        try:
            response = self._client.chat.completions.create(**kwargs)
        except openai.RateLimitError as exc:
            raise TransientError(str(exc), retry_after=_retry_after(exc), status=429) from exc
        except (openai.APITimeoutError, openai.APIConnectionError) as exc:
            raise TransientError(f"{type(exc).__name__}: {exc}") from exc
        except openai.BadRequestError as exc:
            message = str(exc).lower()
            for param in DROPPABLE_PARAMS:
                if param in message and param in kwargs:
                    raise UnsupportedParameterError(param, str(exc)) from exc
            raise PermanentError(str(exc)) from exc
        except openai.APIStatusError as exc:
            if exc.status_code >= 500 or exc.status_code in (408, 409, 425, 429):
                raise TransientError(str(exc), retry_after=_retry_after(exc), status=exc.status_code) from exc
            raise PermanentError(f"HTTP {exc.status_code}: {exc}") from exc

        choice = response.choices[0]
        content = choice.message.content or ""
        usage = response.usage
        cached = 0
        details = getattr(usage, "prompt_tokens_details", None) if usage else None
        if details is not None:
            cached = int(getattr(details, "cached_tokens", 0) or 0)
        return ChatResult(
            content=content,
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            cached_tokens=cached,
        )

    def list_models(self) -> list[str]:
        import openai

        try:
            return sorted(m.id for m in self._client.models.list())
        except openai.APIStatusError as exc:
            raise PermanentError(f"HTTP {exc.status_code}: {exc}") from exc
        except (openai.APITimeoutError, openai.APIConnectionError) as exc:
            raise TransientError(str(exc)) from exc


def _retry_after(exc: Any) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    value = headers.get("retry-after")
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None
