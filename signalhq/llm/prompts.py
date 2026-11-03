"""Versioned prompts. The version is stored with every enrichment and is part of the cache
key (through the message text), so changing a prompt never silently reuses old answers."""

from __future__ import annotations

from typing import Any

from signalhq.config import PRODUCT_AREAS

ENRICH_PROMPT_VERSION = "enrich-v1"
THEME_PROMPT_VERSION = "theme-v1"

ENRICH_SYSTEM = f"""You label customer feedback for a subscription software product (a music and podcast streaming app).
Return only JSON that matches the schema. Field rules:
- type: bug (something is broken), feature_request (asks for something new), usability (works but is confusing or annoying), billing (charges, refunds, plans), performance (slow, laggy, battery, buffering), account_access (login, password, locked out), praise, question, other.
- product_area: the part of the product this is about. Prefer one of: {", ".join(PRODUCT_AREAS)}. Use "other" if none fits.
- severity: low (cosmetic or minor), medium (degraded, a workaround exists), high (a core feature is broken or money is affected), critical (cannot use the product at all, or data loss).
- sentiment: negative, neutral or positive.
- churn_signal: true only if the customer says or clearly implies they will cancel, switch to a competitor, downgrade or ask for a refund because of this.
- key_quote: copy the single most informative sentence or phrase from the feedback exactly, character for character. Do not paraphrase, translate or fix typos.
- language: the ISO 639-1 code of the feedback text, for example "en"."""

ENRICH_BATCH_SYSTEM = (
    ENRICH_SYSTEM
    + "\nYou will receive several feedback items, each with an integer id. Return one result per item in "
    "\"items\", with the same id."
)

THEME_SYSTEM = """You name groups of customer feedback for a product team.
Return only JSON that matches the schema.
- name: 2 to 6 words, sentence case, describing the problem or request (for example "Downloads vanish after update"). No emoji, no hype.
- summary: one or two plain sentences on what customers report. Use only facts present in the quotes; do not invent numbers, versions or causes."""

REPAIR_PROMPT = (
    "Your previous reply did not match the required JSON schema. Problems: {errors}. "
    "Reply again with only the corrected JSON object."
)


def enrich_messages(text: str, source: str) -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": ENRICH_SYSTEM},
        {"role": "user", "content": f"Source: {source}\nFeedback:\n<feedback>\n{text}\n</feedback>"},
    ]


def enrich_batch_messages(items: list[tuple[int, str, str]]) -> list[dict[str, Any]]:
    body = "\n".join(
        f'<feedback id="{item_id}" source="{source}">\n{text}\n</feedback>' for item_id, text, source in items
    )
    return [
        {"role": "system", "content": ENRICH_BATCH_SYSTEM},
        {"role": "user", "content": body},
    ]


def theme_messages(quotes: list[str], types: list[tuple[str, int]], areas: list[tuple[str, int]]) -> list[dict[str, Any]]:
    type_line = ", ".join(f"{t} ({n})" for t, n in types) or "none"
    area_line = ", ".join(f"{a} ({n})" for a, n in areas) or "none"
    quote_lines = "\n".join(f"- {q}" for q in quotes)
    return [
        {"role": "system", "content": THEME_SYSTEM},
        {
            "role": "user",
            "content": f"Feedback types: {type_line}\nProduct areas: {area_line}\nQuotes:\n{quote_lines}",
        },
    ]
