"""End-to-end runs of the offline pipeline on the synthetic seed set."""

from __future__ import annotations

import json

import numpy as np
import pytest
from sklearn.metrics import adjusted_rand_score
from sqlalchemy import select

from signalhq.connectors import JsonlConnector
from signalhq.domain.clustering import agglomerate, merge_candidates, nearest, updated_mean
from signalhq.domain.scoring import rank_key
from signalhq.ingest import ingest
from signalhq.models import FeedbackItem
from signalhq.pipeline.cluster import needs_naming
from signalhq.queue.worker import drain
from signalhq.repositories import ItemRepo, JobRepo, LedgerRepo, ThemeRepo
from tests.conftest import SAMPLE


def test_nearest_and_incremental_mean():
    centroids = np.array([[1.0, 0.0], [0.0, 2.0]])
    assert nearest(np.array([0.1, 0.9]), centroids)[0] == 1
    np.testing.assert_allclose(updated_mean(np.array([1.0, 1.0]), 3, np.array([5.0, 1.0])), [2.0, 1.0])


def test_agglomerate_groups_similar_directions():
    vectors = np.array([[1, 0, 0], [0.9, 0.1, 0], [0, 1, 0], [0, 0.95, 0.05]], dtype=np.float32)
    labels = agglomerate(vectors, link_tau=0.8)
    assert labels[0] == labels[1] and labels[2] == labels[3] and labels[0] != labels[2]


def test_merge_candidates_merge_smaller_into_larger_once():
    c = np.array([[1, 0], [0.99, 0.01], [0.98, 0.02], [0, 1]], dtype=np.float32)
    merges = merge_candidates(c, ids=[10, 11, 12, 13], sizes=[5, 9, 1, 4], merge_tau=0.95)
    assert merges[0] == (10, 11)
    assert all(13 not in pair for pair in merges)
    assert len({x for pair in merges for x in pair}) == 2 * len(merges)


def test_renaming_threshold():
    assert needs_naming(4, None, 0.5)
    assert not needs_naming(14, 10, 0.5)
    assert needs_naming(15, 10, 0.5)


@pytest.fixture
def processed(ctx, settings):
    ingest(ctx.db, settings, JsonlConnector().read(SAMPLE))
    assert drain(ctx, concurrency=2, timeout_s=300)
    return ctx


def test_offline_pipeline_recovers_planted_themes(processed):
    db = processed.db
    with db.read() as s:
        assignments = ThemeRepo(s).assignments()
        rows = s.execute(select(FeedbackItem.id, FeedbackItem.meta_json)).all()
        states = ItemRepo(s).counts_by_state()
        jobs = JobRepo(s).counts_by_state()
    truth, pred = [], []
    for item_id, meta in rows:
        planted = json.loads(meta)["planted_theme"]
        truth.append(planted if planted != "noise" else f"noise-{item_id}")
        pred.append(assignments.get(item_id, -item_id))
    ari = adjusted_rand_score(truth, pred)
    assert ari >= 0.9, ari
    assert set(states) <= {"clustered", "embedded"}  # embedded = left in the pool as noise
    assert set(jobs) == {"done"}


def test_spiking_paid_theme_outranks_the_loud_free_theme(processed):
    with processed.db.read() as s:
        themes = ThemeRepo(s)
        scores = themes.latest_scores()
        ranked = sorted(scores.items(), key=lambda kv: rank_key(kv[0], kv[1]))
        top_id = ranked[0][0]
        member_ids = [i for i, t in themes.assignments().items() if t == top_id]
        planted = [
            json.loads(m)["planted_theme"]
            for m in s.execute(select(FeedbackItem.meta_json).where(FeedbackItem.id.in_(member_ids))).scalars()
        ]
        loudest = max(themes.list_active(), key=lambda t: t.size)
    assert planted.count("crash_on_launch") / len(planted) >= 0.9
    assert loudest.id != top_id  # the ads theme is louder but mostly free accounts
    assert scores[top_id].revenue_at_risk_cents > scores[loudest.id].revenue_at_risk_cents


def test_every_theme_is_named_and_quotes_are_verbatim(processed):
    with processed.db.read() as s:
        themes = ThemeRepo(s)
        for theme in themes.list_active():
            assert theme.name and theme.summary and theme.named_at_size
            for q in themes.quotes(theme.id):
                assert q["quote"] in q["text"]
        ledger = LedgerRepo(s).summary()
    assert ledger["models"] == ["offline-heuristic-v1"]
    assert ledger["cost_usd"] == 0.0


def test_reprocessing_is_a_noop(processed, settings):
    with processed.db.read() as s:
        before = ThemeRepo(s).assignments()
    result = ingest(processed.db, settings, JsonlConnector().read(SAMPLE))
    assert result.created == 0
    assert drain(processed, timeout_s=60)
    with processed.db.read() as s:
        assert ThemeRepo(s).assignments() == before
