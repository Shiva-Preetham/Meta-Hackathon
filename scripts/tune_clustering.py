"""Sweep clustering thresholds on the synthetic set and report the adjusted Rand index.

SYNTHETIC DATA ONLY. This replays the online algorithm in memory (assign to the nearest
centroid if cosine >= tau, otherwise pool; agglomerate the pool with average linkage at
link_tau; merge themes above merge_tau) over the items in time order, using the same
embedder and embedding text as the pipeline. Ground truth is the planted theme; planted
noise and unassigned items count as singletons.

    python scripts/tune_clustering.py [--embedder hashing|minilm] [--top 15]
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sklearn.metrics import adjusted_rand_score  # noqa: E402

from signalhq.domain.clustering import agglomerate, merge_candidates, nearest, updated_mean  # noqa: E402
from signalhq.embeddings import build_embedder  # noqa: E402
from signalhq.llm.offline import heuristic_enrichment  # noqa: E402
from signalhq.pipeline.embed import embedding_text  # noqa: E402

SAMPLE = ROOT / "data" / "sample" / "feedback_sample.jsonl"


def simulate(vectors: np.ndarray, tau: float, link_tau: float, merge_tau: float,
             min_pool: int = 12, min_theme_size: int = 4, every: int = 5) -> list[int]:
    means: list[np.ndarray] = []
    sizes: list[int] = []
    members: list[list[int]] = []
    alive: list[bool] = []
    pool: list[int] = []
    since = 0

    def recluster() -> None:
        nonlocal pool
        if len(pool) < min_pool:
            return
        labels = agglomerate(vectors[pool], link_tau)
        keep = []
        for label in sorted(set(labels.tolist())):
            idx = [pool[i] for i in np.flatnonzero(labels == label)]
            if len(idx) < min_theme_size:
                keep.extend(idx)
                continue
            means.append(vectors[idx].mean(axis=0)); sizes.append(len(idx)); members.append(idx); alive.append(True)
        pool = keep
        live = [i for i, a in enumerate(alive) if a]
        if not live:
            return
        still = []
        for item in pool:
            j, sim = nearest(vectors[item], np.vstack([means[i] for i in live]))
            if sim >= tau:
                t = live[j]; means[t] = updated_mean(means[t], sizes[t], vectors[item]); sizes[t] += 1; members[t].append(item)
            else:
                still.append(item)
        pool = still
        live = [i for i, a in enumerate(alive) if a]
        for src, dst in merge_candidates(np.vstack([means[i] for i in live]), live, [sizes[i] for i in live], merge_tau):
            members[dst].extend(members[src]); members[src] = []; alive[src] = False
            means[dst] = vectors[members[dst]].mean(axis=0); sizes[dst] = len(members[dst])

    for item in range(len(vectors)):
        live = [i for i, a in enumerate(alive) if a]
        if live:
            j, sim = nearest(vectors[item], np.vstack([means[i] for i in live]))
            if sim >= tau:
                t = live[j]; means[t] = updated_mean(means[t], sizes[t], vectors[item]); sizes[t] += 1; members[t].append(item)
                continue
        pool.append(item); since += 1
        if len(pool) >= min_pool and since >= every:
            recluster(); since = 0
    recluster()
    labels = [-(i + 1) for i in range(len(vectors))]
    for t, items in enumerate(members):
        for item in items:
            labels[item] = t
    return labels


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--embedder", default="hashing")
    parser.add_argument("--top", type=int, default=15)
    parser.add_argument("--anchor", type=int, default=2, help="product-area anchor repetitions (0 = off)")
    parser.add_argument(
        "--every", type=int, nargs="+", default=[1, 5, 20, 10**9],
        help="re-cluster cadences (pooled items between re-clusters; 1e9 = one batch re-cluster). "
             "The sweep ranks settings by mean ARI across all cadences, because live timing varies.",
    )
    args = parser.parse_args()

    records = [json.loads(line) for line in SAMPLE.read_text().splitlines() if line.strip()]
    records.sort(key=lambda r: r["created_at"])
    enrichments = [heuristic_enrichment(r["text"]) for r in records]
    texts = [
        embedding_text(r["text"], e["key_quote"], e["product_area"], args.anchor)
        for r, e in zip(records, enrichments, strict=True)
    ]
    truth = [r["meta"]["planted_theme"] if r["meta"]["planted_theme"] != "noise" else f"noise-{i}"
             for i, r in enumerate(records)]
    embedder = build_embedder(args.embedder)
    vectors = embedder.embed(texts)
    print(f"embedder={embedder.name} anchor={args.anchor} cadences={args.every} items={len(records)}")

    results = []
    grid = itertools.product(
        [round(x, 2) for x in np.arange(0.15, 0.66, 0.05)],
        [round(x, 2) for x in np.arange(0.05, 0.41, 0.05)],
        [0.5, 0.6, 0.7, 0.8],
    )
    for tau, link_tau, merge_tau in grid:
        aris, themes, assigned = [], [], []
        for every in args.every:
            labels = simulate(vectors, tau, link_tau, merge_tau, every=every)
            aris.append(adjusted_rand_score(truth, labels))
            themes.append(len({label for label in labels if label >= 0}))
            assigned.append(sum(label >= 0 for label in labels) / len(labels))
        results.append((float(np.mean(aris)), min(aris), tau, link_tau, merge_tau, themes, float(np.mean(assigned))))
    results.sort(key=lambda r: (-r[0], -r[1]))
    print("meanARI minARI tau   link  merge themes_per_cadence assigned")
    for mean_ari, min_ari, tau, link_tau, merge_tau, themes, assigned in results[: args.top]:
        print(f"{mean_ari:.3f}   {min_ari:.3f}  {tau:.2f}  {link_tau:.2f}  {merge_tau:.2f}  {themes}  {assigned:.2f}")


if __name__ == "__main__":
    main()
