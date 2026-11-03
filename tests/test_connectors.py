import json
from datetime import datetime

import pytest
from sqlalchemy import func, select

from signalhq.connectors import CsvConnector, JsonlConnector, TwcsConnector, content_hash, parse_datetime
from signalhq.connectors.twcs import brand_volumes, clean_tweet, validate_columns
from signalhq.ingest import ingest
from signalhq.models import Account, FeedbackItem, Job
from tests.conftest import SAMPLE

TWCS = b"""tweet_id,author_id,inbound,created_at,text,response_tweet_id,in_response_to_tweet_id
1,115712,True,Tue Oct 31 22:10:47 +0000 2017,@SpotifyCares my downloads keep disappearing offline,2,
2,SpotifyCares,False,Tue Oct 31 22:11:45 +0000 2017,@115712 Sorry to hear that! Can you DM us?,,1
3,115713,True,Tue Oct 31 22:12:00 +0000 2017,the app crashes every time I open it https://t.co/x,4,
4,SpotifyCares,False,Tue Oct 31 22:13:00 +0000 2017,@115713 Let's fix this,,3
5,115714,True,Tue Oct 31 22:14:00 +0000 2017,@AppleSupport my phone is slow after the update,6,
6,AppleSupport,False,Tue Oct 31 22:15:00 +0000 2017,@115714 Happy to help,,5
7,115715,True,Wed Nov 01 08:00:00 +0000 2017,@SpotifyCares hi,,
"""


def test_content_hash_ignores_case_and_whitespace_but_not_source_or_id():
    a = content_hash("App  crashes\n on launch", "twitter", "1")
    assert a == content_hash("app crashes on launch", "twitter", "1")
    assert a != content_hash("app crashes on launch", "email", "1")
    assert a != content_hash("app crashes on launch", "twitter", "2")


def test_parse_datetime_formats():
    assert parse_datetime("Tue Oct 31 22:10:47 +0000 2017") == datetime(2017, 10, 31, 22, 10, 47)
    assert parse_datetime("2026-10-01T10:00:00Z") == datetime(2026, 10, 1, 10, 0)
    assert parse_datetime("2026-10-01T12:00:00+02:00") == datetime(2026, 10, 1, 10, 0)
    assert parse_datetime("") is None


def test_jsonl_connector_reads_sample():
    raws = list(JsonlConnector().read(SAMPLE))
    assert len(raws) == 300
    assert raws[0].meta["synthetic"] is True
    assert all(r.account_ref for r in raws)


def test_jsonl_connector_reports_bad_lines():
    with pytest.raises(ValueError, match="line 2"):
        list(JsonlConnector().read(b'{"text": "ok", "created_at": "2026-01-01"}\n{oops\n'))
    with pytest.raises(ValueError, match="no text"):
        list(JsonlConnector().read(b'{"created_at": "2026-01-01"}\n'))


def test_csv_connector_maps_common_columns():
    data = b"id,body,timestamp,customer_id\n10,Login is broken,2026-10-01T00:00:00Z,c1\n"
    (raw,) = list(CsvConnector().read(data))
    assert (raw.external_id, raw.text, raw.account_ref, raw.source) == ("10", "Login is broken", "c1", "csv")


def test_csv_connector_rejects_missing_text_column():
    with pytest.raises(ValueError, match="header"):
        list(CsvConnector().read(b"a,b\n1,2\n"))


def test_twcs_picks_brand_mentions_and_brand_replies_only():
    raws = list(TwcsConnector(brand="SpotifyCares").read(TWCS))
    ids = [r.external_id for r in raws]
    # 1 mentions the brand, 3 was replied to by the brand, 5 is another brand,
    # 7 is too short after cleaning, and brand tweets are never ingested.
    assert ids == ["1", "3"]
    assert raws[0].text == "my downloads keep disappearing offline"
    assert raws[1].text == "the app crashes every time I open it"
    assert raws[0].account_ref == "tw:115712"
    assert raws[0].created_at == datetime(2017, 10, 31, 22, 10, 47)


def test_twcs_limit_keeps_most_recent():
    raws = list(TwcsConnector(brand="SpotifyCares", limit=1).read(TWCS))
    assert [r.external_id for r in raws] == ["3"]


def test_twcs_validates_columns_with_clear_message():
    with pytest.raises(ValueError, match="Missing"):
        validate_columns(b"tweet_id,text\n1,hi\n")


def test_brand_volumes_counts_replies_per_brand():
    assert brand_volumes(TWCS) == {"SpotifyCares": 2, "AppleSupport": 1}


def test_clean_tweet():
    assert clean_tweet("@A @B  hello  there https://t.co/abc") == "hello there"


def test_ingesting_the_same_file_twice_creates_zero_new_rows(db, settings):
    first = ingest(db, settings, JsonlConnector().read(SAMPLE))
    with db.read() as s:
        counts = [s.execute(select(func.count()).select_from(m)).scalar_one() for m in (FeedbackItem, Job, Account)]
    second = ingest(db, settings, JsonlConnector().read(SAMPLE))
    with db.read() as s:
        again = [s.execute(select(func.count()).select_from(m)).scalar_one() for m in (FeedbackItem, Job, Account)]
    assert first.created == 300 and first.duplicates == 0
    assert second.created == 0 and second.duplicates == 300
    assert counts == again
    assert counts[1] == 300  # one enrich job per new item, enqueued with the insert
    assert second.item_ids == first.item_ids


def test_duplicate_inside_one_batch_is_detected(db, settings):
    line = json.dumps({"text": "Same", "created_at": "2026-10-01T00:00:00Z", "id": "1"}) + "\n"
    result = ingest(db, settings, JsonlConnector().read((line * 3).encode()))
    assert (result.created, result.duplicates) == (1, 2)
