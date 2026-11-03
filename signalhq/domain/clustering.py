"""Pure clustering helpers (ADR-005). No database access; unit-testable with arrays."""

from __future__ import annotations

import numpy as np


def normalize(matrix: np.ndarray) -> np.ndarray:
    """Row-wise L2 normalisation; zero rows stay zero."""
    m = np.atleast_2d(np.asarray(matrix, dtype=np.float32))
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return m / norms


def nearest(vector: np.ndarray, centroids: np.ndarray) -> tuple[int, float]:
    """Index and cosine similarity of the closest centroid."""
    sims = normalize(centroids) @ normalize(vector)[0]
    idx = int(np.argmax(sims))
    return idx, float(sims[idx])


def updated_mean(mean: np.ndarray, size: int, vector: np.ndarray) -> np.ndarray:
    """Incremental mean after adding one vector to a group of ``size``."""
    return (mean * size + vector) / (size + 1)


def agglomerate(vectors: np.ndarray, link_tau: float) -> np.ndarray:
    """Average-linkage agglomerative clustering on cosine distance. Groups merge while their
    average pairwise similarity stays at or above ``link_tau``. Returns a label per row."""
    n = vectors.shape[0]
    if n == 0:
        return np.array([], dtype=int)
    if n == 1:
        return np.zeros(1, dtype=int)
    from sklearn.cluster import AgglomerativeClustering

    model = AgglomerativeClustering(
        n_clusters=None,
        metric="cosine",
        linkage="average",
        distance_threshold=1.0 - link_tau,
    )
    # Zero vectors have no cosine; give them a tiny unique direction so they stay alone.
    safe = vectors.copy()
    zero = np.linalg.norm(safe, axis=1) == 0
    if zero.any():
        safe[zero, 0] = 1e-6
    return model.fit_predict(safe)


def merge_candidates(centroids: np.ndarray, ids: list[int], sizes: list[int], merge_tau: float) -> list[tuple[int, int]]:
    """Greedy (src, dst) merges for centroid pairs at or above ``merge_tau``, most similar
    first. The smaller theme merges into the larger; each theme takes part at most once."""
    if len(ids) < 2:
        return []
    sims = normalize(centroids) @ normalize(centroids).T
    pairs = [
        (float(sims[i, j]), i, j)
        for i in range(len(ids))
        for j in range(i + 1, len(ids))
        if sims[i, j] >= merge_tau
    ]
    pairs.sort(key=lambda p: (-p[0], ids[p[1]], ids[p[2]]))
    used: set[int] = set()
    merges: list[tuple[int, int]] = []
    for _, i, j in pairs:
        if i in used or j in used:
            continue
        # Keep the larger theme (older id on ties) so its name and history survive.
        dst, src = (i, j) if (sizes[i], -ids[i]) >= (sizes[j], -ids[j]) else (j, i)
        merges.append((ids[src], ids[dst]))
        used.update((i, j))
    return merges
