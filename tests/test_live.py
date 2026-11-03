"""Real-API tests. Skipped by default (pytest addopts deselect the "live" marker).

    MODEL_API_KEY=... python -m pytest -m live
"""

import os

import pytest

from signalhq.domain.schemas import ENRICH_SCHEMA, ENRICH_SCHEMA_VERSION, EnrichmentOut
from signalhq.llm import build_gateway
from signalhq.llm.prompts import enrich_messages
from tests.conftest import make_settings

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get("MODEL_API_KEY"), reason="needs MODEL_API_KEY"),
]


def test_spark_enrichment_round_trip(db, tmp_path):
    settings = make_settings(tmp_path, offline=False, model_api_key=os.environ["MODEL_API_KEY"], budget_usd=0.05)
    gateway = build_gateway(db, settings)
    assert settings.spark_model in gateway.transport.list_models()
    text = "The app crashes every time I open it since the update. I'm cancelling if this isn't fixed."
    out = gateway.structured(purpose="live-test", messages=enrich_messages(text, "test"), schema=ENRICH_SCHEMA,
                             schema_name="feedback_enrichment", schema_version=ENRICH_SCHEMA_VERSION)
    parsed = EnrichmentOut.model_validate(out)
    assert parsed.churn_signal is True
    assert parsed.key_quote.lower() in text.lower()
