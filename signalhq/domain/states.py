"""Item and job state machines.

Items move forward one stage at a time::

    received -> enriched -> embedded -> clustered

``failed`` is reachable from any non-terminal state and is retryable: the retried stage
moves the item forward again. ``dead`` is reached when a job exhausts its attempts; an
operator can requeue a dead item back to ``received``. Re-asserting the current state is
an idempotent no-op, which is what makes re-running a stage safe.
"""

from __future__ import annotations

from enum import Enum


class ItemState(str, Enum):
    RECEIVED = "received"
    ENRICHED = "enriched"
    EMBEDDED = "embedded"
    CLUSTERED = "clustered"
    FAILED = "failed"
    DEAD = "dead"


class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"  # failed at least once, waiting for its backoff to elapse
    DEAD = "dead"  # dead-lettered after max attempts or a permanent error


class InvalidTransition(ValueError):
    """Raised when code tries to move an item along an edge the state machine forbids."""


_FORWARD = (ItemState.ENRICHED, ItemState.EMBEDDED, ItemState.CLUSTERED)

ALLOWED_TRANSITIONS: dict[ItemState, frozenset[ItemState]] = {
    ItemState.RECEIVED: frozenset({ItemState.ENRICHED, ItemState.FAILED, ItemState.DEAD}),
    ItemState.ENRICHED: frozenset({ItemState.EMBEDDED, ItemState.FAILED, ItemState.DEAD}),
    ItemState.EMBEDDED: frozenset({ItemState.CLUSTERED, ItemState.FAILED, ItemState.DEAD}),
    # A clustered item can only be dead-lettered by an operator; re-clustering moves it
    # between themes without changing its state.
    ItemState.CLUSTERED: frozenset({ItemState.DEAD}),
    # A retry that succeeds moves the item to the state of the stage that succeeded.
    ItemState.FAILED: frozenset({*_FORWARD, ItemState.DEAD}),
    ItemState.DEAD: frozenset({ItemState.RECEIVED}),
}


def can_transition(src: ItemState | str, dst: ItemState | str) -> bool:
    src, dst = ItemState(src), ItemState(dst)
    return src == dst or dst in ALLOWED_TRANSITIONS[src]


def transition(src: ItemState | str, dst: ItemState | str) -> ItemState:
    """Return the new state or raise ``InvalidTransition``."""
    src, dst = ItemState(src), ItemState(dst)
    if not can_transition(src, dst):
        raise InvalidTransition(f"item cannot move from {src.value} to {dst.value}")
    return dst
