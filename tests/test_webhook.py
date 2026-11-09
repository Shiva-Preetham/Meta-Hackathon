"""Webhook items and the data-source note shown in the UI."""

from datetime import datetime, timedelta

from signalhq.api.views import data_note
from signalhq.config import SIMULATED_REVENUE_NOTE, SYNTHETIC_DATA_NOTE
from signalhq.connectors.webhook import from_webhook
from signalhq.domain.schemas import FeedbackIn, RawFeedback
from signalhq.ingest import ingest

NOW = datetime(2026, 11, 9, 12, 0, 0)


def test_webhook_without_timestamp_uses_receive_time():
    raw = from_webhook(FeedbackIn(text="  Login keeps failing  "), NOW)
    assert raw.created_at == NOW
    assert raw.text == "Login keeps failing"
    assert (raw.source, raw.external_id, raw.account_ref) == ("webhook", None, None)


def test_webhook_timestamp_is_normalised_to_naive_utc():
    from datetime import timezone

    sent = datetime(2026, 11, 9, 17, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    raw = from_webhook(FeedbackIn(text="x", created_at=sent), NOW)
    assert raw.created_at == datetime(2026, 11, 9, 12, 0, 0)


def _item(text: str, synthetic: bool) -> RawFeedback:
    return RawFeedback(source="t", text=text, created_at=NOW, meta={"synthetic": True} if synthetic else {})


def test_data_note_tracks_what_is_loaded(db, settings):
    with db.read() as s:
        assert data_note(s) == SIMULATED_REVENUE_NOTE  # empty database: the real-data wording
    ingest(db, settings, [_item("synthetic one", True)])
    with db.read() as s:
        assert data_note(s) == SYNTHETIC_DATA_NOTE
    ingest(db, settings, [_item("real one", False)])
    with db.read() as s:
        note = data_note(s)
    assert "mixes synthetic sample data and real data" in note and "simulated" in note
