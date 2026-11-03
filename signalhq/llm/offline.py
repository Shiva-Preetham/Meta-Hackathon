"""Offline demo mode: a deterministic keyword heuristic that stands in for Muse Spark.

It exists so the repo runs end to end with no API key (CLAUDE.md S1.6). It reads the same
prompts Spark would receive and returns schema-valid JSON, so the gateway, cache, ledger,
validation and every downstream stage run exactly as they would online. Its output is
recorded under the model id ``offline-heuristic-v1`` and costs nothing; it is never
reported as Spark output. Its accuracy is whatever the keyword rules achieve; it is not a
claim about any model.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

from signalhq.llm.transport import ChatRequest, ChatResult

TYPE_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("billing", re.compile(r"\b(charg(e|ed|es|ing)|refund\w*|invoice|bill(ed|ing)?|payment|paid|pricing|price|renew(ed|al)?|card)\b", re.I)),
    ("account_access", re.compile(r"\b(log ?in|logged out|log me in|sign ?in|password|locked out|reset (link|email)|2fa|verification code|access my account)\b", re.I)),
    ("performance", re.compile(r"\b(slow|lag(gy|s|ging)?|battery|drain(s|ing)?|buffer(s|ing)?|takes forever|loading|freez(e|es|ing)|frozen)\b", re.I)),
    ("bug", re.compile(r"\b(crash(es|ed|ing)?|bug(gy)?|broken|error|glitch\w*|stops?|stopped|not working|doesn'?t work|won'?t|disappear(ed|s|ing)?|vanish(ed|es|ing)?|fails?|failed|gone|keeps? (skipping|pausing|stopping|cutting))\b", re.I)),
    ("feature_request", re.compile(r"\b(please add|add an? (option|way|setting)|would love|i wish|wish (you|there|i)|feature request|could you add|bring back|let us|need an? (option|way)|it would be (great|nice)|any plans to)\b", re.I)),
    ("usability", re.compile(r"\b(confusing|hard to find|can'?t find|annoying|too many|so many|cluttered|out of order|wrong order|unclear|every (other|two|few) songs?|ridiculous)\b", re.I)),
    ("praise", re.compile(r"\b(love|great|awesome|amazing|thank(s| you)|best|fantastic|perfect|brilliant)\b", re.I)),
    ("question", re.compile(r"(\?\s*$|^\s*(how|what|can|is|does|why|where|when|do)\b)", re.I | re.M)),
]
_NEGATIVE_TYPES = ("billing", "account_access", "performance", "bug", "feature_request", "usability")

AREA_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("ads", re.compile(r"\b(ads?|advert\w*|commercials?)\b", re.I)),
    ("offline_downloads", re.compile(r"\b(download\w*|offline)\b", re.I)),
    ("billing_subscription", re.compile(r"\b(charg\w*|refund\w*|invoice|bill\w*|payment|subscription|renew\w*|premium|price|card)\b", re.I)),
    ("family_plan", re.compile(r"\bfamily\b", re.I)),
    ("account_login", re.compile(r"\b(log ?in|logged out|log me in|password|sign ?in|locked out|2fa|verification)\b", re.I)),
    ("podcasts", re.compile(r"\b(podcasts?|episodes?)\b", re.I)),
    ("playlists_library", re.compile(r"\b(playlists?|library|liked songs|collab\w*)\b", re.I)),
    ("devices_connect", re.compile(r"\b(bluetooth|car|speakers?|tv|smartwatch|watch|connect|chromecast|headphones?)\b", re.I)),
    ("search", re.compile(r"\bsearch\w*\b", re.I)),
    ("recommendations", re.compile(r"\b(recommend\w*|discover\w*|daily mix|algorithm)\b", re.I)),
    ("app_performance", re.compile(r"\b(crash\w*|launch\w*|freez\w*|slow|lag\w*|battery|startup|won'?t (open|load|start))\b", re.I)),
    ("playback", re.compile(r"\b(playback|playing|skips?|skipping|pauses?|pausing|songs?|tracks?|lock ?screen|shuffle|audio)\b", re.I)),
]

CRITICAL = re.compile(
    r"(can'?t (use|open)|cannot (use|open)|unusable|won'?t (open|launch|start|load)|crash(es|ing)? (on|at|right after) (launch|startup|open\w*)|lost (all|every)|every time i open)",
    re.I,
)
HIGH = re.compile(r"\b(crash\w*|charged twice|double charg\w*|charged again|lost|broken|locked out)\b", re.I)
CHURN = re.compile(
    r"\b(cancel(l?ing|led|lation)?|unsubscrib\w*|refund\w*|switch(ing)? to|moving to|leaving|downgrad\w*|done with|last month i pay|going back to)\b",
    re.I,
)
_SENTENCES = re.compile(r"[^.!?\n]+[.!?]?")
_FEEDBACK = re.compile(r"<feedback(?: id=\"(\d+)\"[^>]*)?>\n(.*?)\n</feedback>", re.S)

STOPWORDS = frozenset(
    """a an the and or but if so to of in on at for with from by is are was were be been being it its
    this that these those i me my we our you your they them their he she his her not no do does did
    have has had just still again now app after when why what how can cant can't dont don't im i'm
    ive i've it's its every all any some get got keep keeps please one two also very really there
    here than then too out up down about into over only more most even back way thing""".split()
)


def _best(rules: list[tuple[str, re.Pattern[str]]], text: str) -> tuple[str | None, dict[str, int]]:
    counts = {name: len(pattern.findall(text)) for name, pattern in rules}
    hits = {k: v for k, v in counts.items() if v}
    if not hits:
        return None, counts
    order = {name: i for i, (name, _) in enumerate(rules)}
    return max(hits, key=lambda k: (hits[k], -order[k])), counts


def heuristic_enrichment(text: str) -> dict[str, Any]:
    type_counts = {name: len(p.findall(text)) for name, p in TYPE_RULES}
    negative = {k: v for k, v in type_counts.items() if k in _NEGATIVE_TYPES and v}
    if negative:
        order = {name: i for i, name in enumerate(_NEGATIVE_TYPES)}
        ftype = max(negative, key=lambda k: (negative[k], -order[k]))
    elif type_counts["praise"]:
        ftype = "praise"
    elif type_counts["question"]:
        ftype = "question"
    else:
        ftype = "other"

    area, _ = _best(AREA_RULES, text)
    churn = bool(CHURN.search(text))
    if CRITICAL.search(text):
        severity = "critical"
    elif churn or ftype in ("billing", "account_access") or HIGH.search(text):
        severity = "high"
    elif ftype in ("bug", "performance", "usability"):
        severity = "medium"
    else:
        severity = "low"
    sentiment = "positive" if ftype == "praise" else ("neutral" if ftype in ("question", "other") else "negative")

    sentences = [s.strip() for s in _SENTENCES.findall(text) if s.strip()]

    def weight(sentence: str) -> int:
        return sum(len(p.findall(sentence)) for _, p in TYPE_RULES + AREA_RULES)

    key_quote = max(sentences, key=weight) if sentences else text.strip()
    ascii_ratio = sum(c.isascii() for c in text) / max(1, len(text))
    return {
        "type": ftype,
        "product_area": area or "other",
        "severity": severity,
        "sentiment": sentiment,
        "churn_signal": churn,
        "key_quote": key_quote,
        "language": "en" if ascii_ratio > 0.9 else "und",
    }


def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def heuristic_theme_name(quotes: list[str], areas: list[str]) -> dict[str, str]:
    doc_bigrams: Counter[tuple[str, str]] = Counter()
    doc_words: Counter[str] = Counter()
    surface: dict[str, Counter[str]] = {}
    for quote in quotes:
        words = [w for w in re.findall(r"[a-z']+", quote.lower()) if w not in STOPWORDS and len(w) > 2]
        stems = []
        for w in words:
            s = _stem(w)
            surface.setdefault(s, Counter())[w] += 1
            stems.append(s)
        doc_words.update(set(stems))
        doc_bigrams.update(set(zip(stems, stems[1:])))

    def show(stem: str) -> str:
        return surface[stem].most_common(1)[0][0]

    area = (areas[0] if areas else "other").replace("_", " ")
    bigram = next(((a, b) for (a, b), n in doc_bigrams.most_common() if n >= 2), None)
    if bigram:
        phrase = f"{show(bigram[0])} {show(bigram[1])}"
    else:
        top = [show(w) for w, _ in doc_words.most_common(2)]
        phrase = " and ".join(top) if top else "mixed feedback"
    name = f"{area.capitalize()}: {phrase}"
    top_words = ", ".join(show(w) for w, _ in doc_words.most_common(4))
    summary = (
        f"Customers mention {top_words or 'mixed issues'} ({len(quotes)} sampled quotes, mostly {area}). "
        "Offline heuristic summary."
    )
    return {"name": name, "summary": summary}


def schema_instance(schema: dict[str, Any]) -> Any:
    """Smallest value that satisfies a strict schema (used for the offline ``check``)."""
    kind = schema.get("type")
    if "enum" in schema:
        return schema["enum"][0]
    if kind == "object":
        return {k: schema_instance(v) for k, v in schema.get("properties", {}).items()}
    return {"array": [], "string": "ok", "boolean": True, "integer": 0, "number": 0}.get(kind)


class OfflineHeuristicTransport:
    name = "offline-heuristic"
    billable = False

    def complete(self, request: ChatRequest, timeout_s: float) -> ChatResult:
        user = next((m["content"] for m in reversed(request.messages) if m["role"] == "user"), "")
        if request.schema_name == "feedback_enrichment":
            match = _FEEDBACK.search(user)
            data: Any = heuristic_enrichment(match.group(2) if match else user)
        elif request.schema_name == "feedback_enrichment_batch":
            data = {
                "items": [
                    {"id": int(item_id), **heuristic_enrichment(text)}
                    for item_id, text in _FEEDBACK.findall(user)
                ]
            }
        elif request.schema_name == "theme_name":
            quotes = [line[2:] for line in user.splitlines() if line.startswith("- ")]
            area_line = next((ln for ln in user.splitlines() if ln.startswith("Product areas:")), "")
            areas = re.findall(r"([a-z_]+) \(\d+\)", area_line)
            data = heuristic_theme_name(quotes, areas)
        else:
            data = schema_instance(request.schema)
        return ChatResult(content=json.dumps(data))

    def list_models(self) -> list[str]:
        return ["offline-heuristic-v1"]
