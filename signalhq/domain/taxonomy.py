"""Deterministic normalisation of model output: product areas and quotes."""

from __future__ import annotations

import difflib
import re

from signalhq.config import PRODUCT_AREA_SYNONYMS, PRODUCT_AREAS

_NON_WORD = re.compile(r"[^a-z0-9]+")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_WS = re.compile(r"\s+")


def _slug(value: str) -> str:
    return _NON_WORD.sub(" ", value.lower()).strip()


def normalize_product_area(
    raw: str,
    areas: tuple[str, ...] = PRODUCT_AREAS,
    synonyms: dict[str, str] = PRODUCT_AREA_SYNONYMS,
    cutoff: float = 0.75,
) -> str:
    """Map free text onto the taxonomy: exact, then synonym, then fuzzy, else "other"."""
    slug = _slug(raw)
    if not slug:
        return "other"
    by_slug = {_slug(a): a for a in areas}
    if slug in by_slug:
        return by_slug[slug]
    if slug in synonyms:
        return synonyms[slug]
    candidates = {**by_slug, **{_slug(k): v for k, v in synonyms.items()}}
    match = difflib.get_close_matches(slug, list(candidates), n=1, cutoff=cutoff)
    if match:
        return candidates[match[0]]
    # Multi-word answers such as "billing and payments": try each word on its own.
    for word in slug.split():
        if word in synonyms:
            return synonyms[word]
        if word in by_slug:
            return by_slug[word]
    return "other"


def first_sentence(text: str, max_chars: int = 240) -> str:
    stripped = text.strip()
    sentence = _SENTENCE_END.split(stripped, maxsplit=1)[0] if stripped else ""
    return sentence[:max_chars].strip()


def verify_quote(quote: str, text: str) -> tuple[str, bool]:
    """Return (quote, verified). The quote must appear verbatim in the text, ignoring case
    and runs of whitespace. When it does, the exact original span is returned. When it does
    not, the first sentence of the text is used, so the UI never shows a made-up quote."""
    needle = _WS.sub(" ", quote.strip()).strip(" \"'")
    if needle:
        # Build a regex that tolerates any whitespace between the quote's words.
        pattern = r"\s+".join(re.escape(part) for part in needle.split(" "))
        found = re.search(pattern, text, flags=re.IGNORECASE)
        if found:
            return found.group(0), True
    return first_sentence(text), False
