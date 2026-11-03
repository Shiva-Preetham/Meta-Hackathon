"""Connectors (strategy pattern): each turns a source format into ``RawFeedback``."""

from __future__ import annotations

from signalhq.connectors.base import Connector, content_hash, normalize_text, parse_datetime
from signalhq.connectors.files import CsvConnector, JsonlConnector
from signalhq.connectors.twcs import TwcsConnector


def get_connector(name: str, **options) -> Connector:
    if name == "jsonl":
        return JsonlConnector()
    if name == "csv":
        return CsvConnector()
    if name == "twcs":
        return TwcsConnector(brand=options.get("brand") or "SpotifyCares", limit=options.get("limit"))
    raise ValueError(f"unknown connector {name!r}; choose jsonl, csv or twcs")


__all__ = [
    "Connector",
    "CsvConnector",
    "JsonlConnector",
    "TwcsConnector",
    "content_hash",
    "get_connector",
    "normalize_text",
    "parse_datetime",
]
