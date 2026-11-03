"""Call recordings -> one feedback item per speaker turn (``source=call``)."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

from signalhq.domain.schemas import RawFeedback
from signalhq.llm.voice import Transcript


def turns_to_feedback(
    transcript: Transcript,
    *,
    recording_id: str,
    recorded_at: datetime,
    account_ref: str | None = None,
    min_words: int = 4,
) -> Iterator[RawFeedback]:
    """Each speaker turn becomes an item. Very short turns ("yes", "okay") are skipped
    because they carry no feedback and would only add noise to clustering."""
    for turn in transcript.turns:
        if len(turn.transcript.split()) < min_words:
            continue
        yield RawFeedback(
            source="call",
            text=turn.transcript,
            created_at=recorded_at + timedelta(milliseconds=turn.start_ms),
            external_id=f"{recording_id}:{turn.turn_id}",
            account_ref=account_ref,
            meta={
                "recording": recording_id,
                "speaker": turn.speaker,
                "start_ms": turn.start_ms,
                "end_ms": turn.end_ms,
                "session_id": transcript.session_id,
            },
        )


def recording_id_for(path: Path, wav: bytes) -> str:
    return f"{path.stem}-{hashlib.sha256(wav).hexdigest()[:12]}"
