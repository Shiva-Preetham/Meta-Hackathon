"""Local embeddings (ADR-004). No network call, no per-item cost, deterministic.

* ``MiniLMEmbedder``: ``sentence-transformers/all-MiniLM-L6-v2``, the preferred model.
  Optional dependency (``requirements-embeddings.txt``) because it pulls in PyTorch, and the
  weights download from Hugging Face on first use.
* ``HashingEmbedder``: a deterministic bag-of-words embedding (light stemming, hashed
  unigrams and bigrams, sublinear term frequency, L2 norm). Needs only scikit-learn and no
  download, so the offline demo always works. Weaker on paraphrases than MiniLM.

``auto`` picks MiniLM when it can be loaded and falls back to hashing otherwise, and the
chosen model id is stored with every vector so mixed runs are detectable.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Protocol

import numpy as np

log = logging.getLogger(__name__)


class Embedder(Protocol):
    name: str
    kind: str  # "hashing" or "minilm"; selects clustering defaults

    def embed(self, texts: list[str]) -> np.ndarray: ...


_STOP = frozenset(
    """a an the and or but if so to of in on at for with from by is are was were be been being it its
    this that these those i me my we our you your they them their he she his her do does did have has had
    just im ive it's i'm i've am will would should could can there here than then too very really also
    hi hello hey please thanks thank pls plz guys team help any anyone""".split()
)
_TOKEN = re.compile(r"[a-z0-9']+")


def _stem(word: str) -> str:
    """Tiny suffix stripper: crash/crashes/crashing/crashed -> crash."""
    word = word.strip("'")
    for suffix in ("ingly", "ing", "edly", "ed", "ies", "es", "s", "ly"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            stem = word[: -len(suffix)]
            if suffix == "ies":
                stem += "y"
            return stem
    return word


def analyze(text: str) -> list[str]:
    tokens = [_stem(t) for t in _TOKEN.findall(text.lower())]
    tokens = [t for t in tokens if t and t not in _STOP and len(t) > 1]
    return tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:], strict=False)]


class HashingEmbedder:
    kind = "hashing"

    def __init__(self, n_features: int = 2048) -> None:
        from sklearn.feature_extraction.text import HashingVectorizer

        self.name = f"hashing-bow-{n_features}-v1"
        self._vectorizer = HashingVectorizer(
            n_features=n_features,
            analyzer=analyze,
            alternate_sign=False,
            norm=None,
        )

    def embed(self, texts: list[str]) -> np.ndarray:
        counts = self._vectorizer.transform(texts).toarray().astype(np.float32)
        counts = np.log1p(counts)
        norms = np.linalg.norm(counts, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return counts / norms


class MiniLMEmbedder:
    kind = "minilm"

    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2") -> None:
        from sentence_transformers import SentenceTransformer

        self.name = model_name
        self._model = SentenceTransformer(model_name, device="cpu")
        self._lock = threading.Lock()  # the model is not documented as thread-safe

    def embed(self, texts: list[str]) -> np.ndarray:
        with self._lock:
            vectors = self._model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
        return np.asarray(vectors, dtype=np.float32)


def build_embedder(choice: str = "auto", model_name: str = "sentence-transformers/all-MiniLM-L6-v2") -> Embedder:
    if choice == "hashing":
        return HashingEmbedder()
    if choice in ("auto", "minilm"):
        try:
            return MiniLMEmbedder(model_name)
        except Exception as exc:  # ImportError, or the weights could not be downloaded
            if choice == "minilm":
                raise
            log.warning("MiniLM unavailable (%s); using the hashing embedder", exc.__class__.__name__)
            return HashingEmbedder()
    raise ValueError(f"unknown embedder {choice!r}; choose auto, minilm or hashing")
