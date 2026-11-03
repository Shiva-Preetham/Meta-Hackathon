"""Webhook connector: one JSON payload from ``POST /api/feedback`` -> ``RawFeedback``."""

from __future__ import annotations

from datetime import datetime

from signalhq.connectors.base import parse_datetime
from signalhq.domain.schemas import FeedbackIn, RawFeedback


def from_webhook(payload: FeedbackIn, now: datetime) -> RawFeedback:
    """Items without a timestamp are stamped with the receive time."""
    return RawFeedback(
        source=payload.source,
        text=payload.text.strip(),
        created_at=parse_datetime(payload.created_at) or now,
        external_id=payload.external_id,
        account_ref=payload.account_ref,
    )
