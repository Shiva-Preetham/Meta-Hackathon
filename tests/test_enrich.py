from datetime import datetime

import pytest

from signalhq.domain.schemas import RawFeedback
from signalhq.domain.states import ItemState
from signalhq.domain.taxonomy import first_sentence, normalize_product_area, verify_quote
from signalhq.ingest import ingest
from signalhq.llm.gateway import LLMGateway
from signalhq.pipeline.enrich import EnrichStage
from signalhq.repositories import ClaimedJob, ItemRepo
from tests.conftest import ScriptedTransport

TEXT = "Hi team. My downloads keep disappearing when I go offline!  Please fix."


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("offline_downloads", "offline_downloads"),
        ("Offline Downloads", "offline_downloads"),
        ("login", "account_login"),
        ("Billing", "billing_subscription"),
        ("billing and payments", "billing_subscription"),
        ("playlist", "playlists_library"),
        ("podcats", "podcasts"),  # typo, fuzzy match
        ("quantum flux", "other"),
        ("", "other"),
    ],
)
def test_product_area_normalisation(raw, expected):
    assert normalize_product_area(raw) == expected


def test_verbatim_quote_is_kept_with_original_casing_and_spacing():
    quote, ok = verify_quote("my downloads keep disappearing when i go offline", TEXT)
    assert ok and quote == "My downloads keep disappearing when I go offline"


def test_quote_spanning_double_space_is_found():
    quote, ok = verify_quote("offline! Please fix", TEXT)
    assert ok and quote == "offline!  Please fix"


def test_made_up_quote_falls_back_to_first_sentence():
    quote, ok = verify_quote("The app deleted all my music", TEXT)
    assert not ok and quote == "Hi team."


def test_first_sentence_handles_no_punctuation():
    assert first_sentence("no punctuation here") == "no punctuation here"


def _job(item_id: int) -> ClaimedJob:
    return ClaimedJob(id=1, type="enrich", payload={"item_id": item_id}, attempts=1, max_attempts=3, locked_by="t")


def _model_answer(**overrides):
    answer = {
        "type": "bug",
        "product_area": "Offline mode",
        "severity": "high",
        "sentiment": "negative",
        "churn_signal": False,
        "key_quote": "The app ate my homework",  # not in the text: must be replaced
        "language": "en",
    }
    answer.update(overrides)
    return answer


def test_enrich_stage_normalises_verifies_and_is_idempotent(ctx, settings, db):
    transport = ScriptedTransport([_model_answer()])
    ctx.gateway = LLMGateway(db, ctx.settings.model_copy(update={"offline": False, "model_api_key": "k"}), transport)
    (item_id,) = ingest(db, settings, [RawFeedback(source="t", text=TEXT, created_at=datetime(2026, 10, 1))]).item_ids

    follow = EnrichStage().run(_job(item_id), ctx)
    assert [j.type for j in follow] == ["embed"]
    with db.read() as s:
        items = ItemRepo(s)
        enrichment = items.get_enrichment(item_id)
        assert items.get(item_id).state == ItemState.ENRICHED.value
    assert enrichment.product_area == "offline_downloads"
    assert enrichment.product_area_raw == "Offline mode"
    assert enrichment.key_quote == "Hi team." and enrichment.quote_verified is False
    assert enrichment.model == "muse-spark-1.3"

    # Second run: no model call (the scripted transport has no more answers), same follow-up.
    assert [j.type for j in EnrichStage().run(_job(item_id), ctx)] == ["embed"]
    assert len(transport.requests) == 1


def test_enrich_stage_skips_deleted_items(ctx):
    assert EnrichStage().run(_job(999), ctx) == []
