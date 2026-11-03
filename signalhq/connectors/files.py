"""JSONL and CSV connectors for generic feedback exports."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from signalhq.connectors.base import parse_datetime
from signalhq.domain.schemas import RawFeedback

TEXT_KEYS = ("text", "body", "message", "content")
ID_KEYS = ("external_id", "id", "ticket_id")
ACCOUNT_KEYS = ("account_ref", "author_id", "account", "customer_id", "user_id")
TIME_KEYS = ("created_at", "timestamp", "date", "time")


def _first(record: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return value
    return None


def record_to_raw(record: dict[str, Any], default_source: str, line_no: int) -> RawFeedback:
    text = _first(record, TEXT_KEYS)
    if not text or not str(text).strip():
        raise ValueError(f"record {line_no}: no text field (expected one of {TEXT_KEYS})")
    created = parse_datetime(_first(record, TIME_KEYS))
    if created is None:
        raise ValueError(f"record {line_no}: no timestamp (expected one of {TIME_KEYS})")
    external_id = _first(record, ID_KEYS)
    account = _first(record, ACCOUNT_KEYS)
    meta = record.get("meta") if isinstance(record.get("meta"), dict) else {}
    return RawFeedback(
        source=str(record.get("source") or default_source),
        text=str(text).strip(),
        created_at=created,
        external_id=None if external_id is None else str(external_id),
        account_ref=None if account is None else str(account),
        meta=meta,
    )


def _open_text(source: Path | bytes) -> io.TextIOBase:
    if isinstance(source, bytes):
        return io.StringIO(source.decode("utf-8"))
    return open(source, encoding="utf-8", newline="")


class JsonlConnector:
    name = "jsonl"

    def __init__(self, default_source: str = "jsonl") -> None:
        self.default_source = default_source

    def read(self, source: Path | bytes) -> Iterator[RawFeedback]:
        with _open_text(source) as fh:
            for line_no, line in enumerate(fh, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"line {line_no}: invalid JSON: {exc.msg}") from exc
                yield record_to_raw(record, self.default_source, line_no)


class CsvConnector:
    name = "csv"

    def __init__(self, default_source: str = "csv") -> None:
        self.default_source = default_source

    def read(self, source: Path | bytes) -> Iterator[RawFeedback]:
        with _open_text(source) as fh:
            reader = csv.DictReader(fh)
            if not reader.fieldnames or not any(k in reader.fieldnames for k in TEXT_KEYS):
                raise ValueError(f"CSV header must include one of {TEXT_KEYS}; got {reader.fieldnames}")
            for line_no, record in enumerate(reader, start=2):
                yield record_to_raw(record, self.default_source, line_no)


def sort_by_time(raws: list[RawFeedback]) -> list[RawFeedback]:
    return sorted(raws, key=lambda r: (r.created_at or datetime.min, r.external_id or ""))
