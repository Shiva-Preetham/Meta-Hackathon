"""Online clustering (ADR-005).

* ``ClusterStage`` (per item): join the nearest active theme if cosine >= tau and update its
  centroid incrementally; otherwise leave the item in the unassigned pool.
* ``ReclusterStage`` (singleton job): agglomerate the pool into new themes, absorb pool
  items that now match a theme, and merge near-duplicate themes.

Both run inside a ``BEGIN IMMEDIATE`` transaction, so clustering is serialised: centroid
read-modify-write cycles never interleave, whatever the worker concurrency.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
from sqlalchemy.orm import Session

from signalhq.domain.clustering import agglomerate, merge_candidates, nearest, normalize, updated_mean
from signalhq.domain.states import ItemState
from signalhq.pipeline.stage import CLUSTER, NAME_THEME, RECLUSTER, SCORE, StageContext
from signalhq.repositories import ClaimedJob, ItemRepo, NewJob, ThemeRepo


def score_job(ctx: StageContext) -> NewJob:
    return NewJob(SCORE, {}, dedupe_key="score", delay_s=ctx.settings.score_delay_s)


def name_job(ctx: StageContext, theme_id: int, force: bool = False) -> NewJob:
    return NewJob(
        NAME_THEME,
        {"theme_id": theme_id, "force": force},
        dedupe_key=f"name:{theme_id}",
        delay_s=ctx.settings.name_delay_s,
    )


def recluster_job(ctx: StageContext) -> NewJob:
    return NewJob(RECLUSTER, {}, dedupe_key="recluster", delay_s=ctx.settings.recluster_delay_s)


def needs_naming(size: int, named_at_size: int | None, growth_ratio: float) -> bool:
    """Name new themes once, then rename only after meaningful growth (saves calls)."""
    if named_at_size is None:
        return True
    return size >= named_at_size * (1 + growth_ratio)


class ClusterStage:
    job_type = CLUSTER

    def run(self, job: ClaimedJob, ctx: StageContext) -> list[NewJob]:
        item_id = int(job.payload["item_id"])
        now = ctx.now()
        with ctx.db.write() as s:
            items, themes = ItemRepo(s), ThemeRepo(s)
            if items.get(item_id) is None:
                return []
            if themes.theme_of(item_id) is not None:
                return [score_job(ctx)]
            vector = items.get_embedding(item_id)
            if vector is None:
                raise RuntimeError(f"item {item_id} has no embedding; cannot cluster yet")

            active = themes.active()
            if active:
                idx, sim = nearest(vector, np.vstack([t.centroid for t in active]))
                if sim >= ctx.cluster.tau:
                    theme = active[idx]
                    themes.add_member(theme.id, item_id, sim, now)
                    new_size = theme.size + 1
                    themes.set_centroid(theme.id, updated_mean(theme.centroid, theme.size, vector), new_size, now)
                    items.set_state(item_id, ItemState.CLUSTERED)
                    follow = [score_job(ctx)]
                    if needs_naming(new_size, theme.named_at_size, ctx.settings.rename_growth_ratio):
                        follow.append(name_job(ctx, theme.id))
                    return follow

            if items.pool_size() >= ctx.settings.recluster_min_pool:
                return [recluster_job(ctx)]
            return []


class ReclusterStage:
    job_type = RECLUSTER

    def run(self, job: ClaimedJob, ctx: StageContext) -> list[NewJob]:
        now = ctx.now()
        with ctx.db.write() as s:
            created = self._pool_to_themes(s, ctx, now)
            absorbed = self._absorb_pool(s, ctx, now)
            survivors = self._merge_themes(s, ctx, now)
        # Merged themes changed membership wholesale, so they are renamed regardless of growth.
        follow: list[NewJob] = [name_job(ctx, tid) for tid in sorted(set(created) - set(survivors))]
        follow += [name_job(ctx, tid, force=True) for tid in sorted(set(survivors))]
        if created or absorbed or survivors:
            follow.append(score_job(ctx))
        return follow

    def _pool_to_themes(self, s: Session, ctx: StageContext, now: datetime) -> list[int]:
        items, themes = ItemRepo(s), ThemeRepo(s)
        ids, vectors = items.pool(ctx.settings.recluster_pool_cap)
        if vectors is None or len(ids) < ctx.settings.recluster_min_pool:
            return []
        labels = agglomerate(vectors, ctx.cluster.link_tau)
        created: list[int] = []
        for label in sorted(set(labels.tolist())):
            idx = np.flatnonzero(labels == label)
            if len(idx) < ctx.settings.min_theme_size:
                continue
            centroid = vectors[idx].mean(axis=0)
            sims = normalize(vectors[idx]) @ normalize(centroid)[0]
            theme_id = themes.create(centroid, now)
            for i, sim in zip(idx, sims, strict=True):
                themes.add_member(theme_id, ids[i], float(sim), now)
                items.set_state(ids[i], ItemState.CLUSTERED)
            themes.set_centroid(theme_id, centroid, len(idx), now)
            created.append(theme_id)
        s.flush()
        return created

    def _absorb_pool(self, s: Session, ctx: StageContext, now: datetime) -> int:
        """Pool items that now clear tau for some theme join it."""
        items, themes = ItemRepo(s), ThemeRepo(s)
        active = themes.active()
        ids, vectors = items.pool(ctx.settings.recluster_pool_cap)
        if not active or vectors is None:
            return 0
        centroids = {t.id: (t.centroid.copy(), t.size) for t in active}
        order = [t.id for t in active]
        absorbed = 0
        for item_id, vector in zip(ids, vectors, strict=True):
            idx, sim = nearest(vector, np.vstack([centroids[tid][0] for tid in order]))
            if sim < ctx.cluster.tau:
                continue
            tid = order[idx]
            mean, size = centroids[tid]
            themes.add_member(tid, item_id, sim, now)
            items.set_state(item_id, ItemState.CLUSTERED)
            centroids[tid] = (updated_mean(mean, size, vector), size + 1)
            absorbed += 1
        for tid, (mean, size) in centroids.items():
            themes.set_centroid(tid, mean, size, now)
        s.flush()
        return absorbed

    def _merge_themes(self, s: Session, ctx: StageContext, now: datetime) -> list[int]:
        themes = ThemeRepo(s)
        active = themes.active()
        merges = merge_candidates(
            np.vstack([t.centroid for t in active]) if active else np.zeros((0, 1)),
            [t.id for t in active],
            [t.size for t in active],
            ctx.cluster.merge_tau,
        )
        survivors: list[int] = []
        for src, dst in merges:
            themes.merge_into(src, dst, now)
            member_ids, vectors = themes.member_vectors(dst)
            if vectors is None:
                continue
            centroid = vectors.mean(axis=0)
            themes.set_centroid(dst, centroid, len(member_ids), now)
            themes.update_similarities(dst, member_ids, normalize(vectors) @ normalize(centroid)[0])
            survivors.append(dst)
        s.flush()
        return survivors
