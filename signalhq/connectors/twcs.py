"""Connector for Kaggle's "Customer Support on Twitter" dataset (``twcs.csv``).

A customer tweet is attributed to a brand when it mentions the brand's handle or when the
brand's support account replied to it. Brand replies are never ingested: only inbound
(customer) tweets are feedback. Raw tweets are never committed (``data/raw/`` is ignored).
"""

from __future__ import annotations

import io
import re
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

import pandas as pd

from signalhq.connectors.base import parse_datetime
from signalhq.domain.schemas import RawFeedback

EXPECTED_COLUMNS = (
    "tweet_id",
    "author_id",
    "inbound",
    "created_at",
    "text",
    "response_tweet_id",
    "in_response_to_tweet_id",
)
_URL = re.compile(r"https?://\S+")
_LEADING_HANDLES = re.compile(r"^(?:\s*@\w+)+\s*")
_WS = re.compile(r"\s+")
CHUNK = 250_000


def _as_buffer(source: Path | bytes) -> Path | io.BytesIO:
    return io.BytesIO(source) if isinstance(source, bytes) else source


def validate_columns(source: Path | bytes) -> None:
    columns = tuple(pd.read_csv(_as_buffer(source), nrows=0).columns)
    missing = [c for c in EXPECTED_COLUMNS if c not in columns]
    if missing:
        raise ValueError(
            f"twcs.csv columns differ from the expected schema. Missing: {missing}. "
            f"Found: {list(columns)}. Expected: {list(EXPECTED_COLUMNS)}."
        )


def _chunks(source: Path | bytes, usecols: list[str]) -> Iterator[pd.DataFrame]:
    yield from pd.read_csv(
        _as_buffer(source),
        usecols=usecols,
        dtype={"author_id": str, "text": str, "created_at": str, "tweet_id": "Int64"},
        chunksize=CHUNK,
    )


def _inbound(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().eq("true")


def clean_tweet(text: str) -> str:
    text = _URL.sub("", text)
    text = _LEADING_HANDLES.sub("", text)
    return _WS.sub(" ", text).strip()


def brand_volumes(source: Path | bytes) -> Counter[str]:
    """Customer tweets each brand account replied to: a proxy for inbound volume."""
    validate_columns(source)
    counts: Counter[str] = Counter()
    for chunk in _chunks(source, ["author_id", "inbound", "in_response_to_tweet_id"]):
        replies = chunk[~_inbound(chunk["inbound"]) & chunk["in_response_to_tweet_id"].notna()]
        counts.update(replies["author_id"].value_counts().to_dict())
    return counts


class TwcsConnector:
    name = "twcs"

    def __init__(self, brand: str = "SpotifyCares", limit: int | None = None, min_words: int = 3) -> None:
        self.brand = brand
        self.limit = limit
        self.min_words = min_words

    def read(self, source: Path | bytes) -> Iterator[RawFeedback]:
        validate_columns(source)
        brand_lower = self.brand.lower()
        mention = re.compile(rf"@{re.escape(self.brand)}\b", re.I)

        replied_to: set[int] = set()
        for chunk in _chunks(source, ["author_id", "in_response_to_tweet_id"]):
            rows = chunk[chunk["author_id"].str.lower() == brand_lower]
            replied_to.update(int(x) for x in rows["in_response_to_tweet_id"].dropna())

        matches: list[RawFeedback] = []
        cols = ["tweet_id", "author_id", "inbound", "created_at", "text"]
        for chunk in _chunks(source, cols):
            inbound = chunk[_inbound(chunk["inbound"])]
            if inbound.empty:
                continue
            tweet_ids = inbound["tweet_id"].astype("int64")
            keep = tweet_ids.isin(replied_to) | inbound["text"].fillna("").str.contains(mention)
            for row in inbound[keep].itertuples(index=False):
                text = clean_tweet(str(row.text or ""))
                if len(text.split()) < self.min_words:
                    continue
                created = parse_datetime(row.created_at)
                if created is None:
                    continue
                matches.append(
                    RawFeedback(
                        source="twitter",
                        text=text,
                        created_at=created,
                        external_id=str(row.tweet_id),
                        account_ref=f"tw:{row.author_id}",
                        meta={"brand": self.brand},
                    )
                )
        matches.sort(key=lambda r: (r.created_at, r.external_id or ""))
        if self.limit is not None:
            # The most recent N give a dense, contiguous window for scoring and replay.
            matches = matches[-self.limit:]
        yield from matches
