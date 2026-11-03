"""Connector strategy interface and the shared normalisation used for deduplication."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterator
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Protocol

from signalhq.domain.schemas import RawFeedback

_WS = re.compile(r"\s+")


class Connector(Protocol):
    name: str

    def read(self, source: Path | bytes) -> Iterator[RawFeedback]: ...


def normalize_text(text: str) -> str:
    """Canonical form for hashing: NFKC, lower case, collapsed whitespace."""
    return _WS.sub(" ", unicodedata.normalize("NFKC", text)).strip().lower()


def content_hash(text: str, source: str, external_id: str | None) -> str:
    """SHA-256 of normalised text + source + external id (CLAUDE.md §4)."""
    key = "\x1f".join((normalize_text(text), source, external_id or ""))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def parse_datetime(value: str | datetime | None) -> datetime | None:
    """Parse ISO 8601 or RFC 2822-style timestamps into naive UTC."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value).strip()
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            try:
                # Twitter format: "Tue Oct 31 22:10:47 +0000 2017"
                dt = datetime.strptime(raw, "%a %b %d %H:%M:%S %z %Y")
            except ValueError:
                dt = parsedate_to_datetime(raw)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt
