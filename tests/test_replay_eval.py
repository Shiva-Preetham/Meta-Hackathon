from datetime import datetime, timedelta

from signalhq.connectors import JsonlConnector
from signalhq.db import utcnow
from signalhq.domain.schemas import RawFeedback
from signalhq.eval.commands import report, sample
from signalhq.eval.labelling import read_sample
from signalhq.ingest import ingest
from signalhq.queue.worker import drain
from signalhq.replay import ReplayDriver, read_status, reset_processed_data, speed_for_duration
from signalhq.repositories import EvalRepo, ItemRepo, JobRepo
from tests.conftest import SAMPLE

T0 = datetime(2026, 10, 1)


class StepClock:
    """Each call advances one second of wall time."""

    def __init__(self):
        self.t = 0.0

    def __call__(self):
        self.t += 1.0
        return self.t


def raws(n=5, gap_h=1):
    return [RawFeedback(source="t", text=f"message {i} about playback", created_at=T0 + timedelta(hours=i * gap_h),
                        external_id=str(i)) for i in range(n)]


def test_replay_ingests_in_time_order_and_publishes_clock(db, settings):
    driver = ReplayDriver(db, settings, list(reversed(raws())), speed=3600, tick_s=0, clock=StepClock())
    status = driver.run()
    assert status.status == "done" and status.ingested == 5
    st = read_status(db)
    assert st.clock == (T0 + timedelta(hours=4)).isoformat() and (st.ingested, st.total) == (5, 5)
    with db.read() as s:
        items = sorted((ItemRepo(s).get(i) for i in range(1, 6)), key=lambda x: x.id)
        assert [x.external_id for x in items] == ["0", "1", "2", "3", "4"]  # arrival follows data time
        assert JobRepo(s).counts().get("score")  # scores follow the moving clock


def test_speed_for_duration():
    assert speed_for_duration(raws(5), 4) == 3600.0  # 4 data-hours in 4 seconds


def test_reset_keeps_labels_and_ledger(db, settings):
    (item_id,) = ingest(db, settings, raws(1)).item_ids
    with db.write() as s:
        item = ItemRepo(s).get(item_id)
        EvalRepo(s).upsert_label(item_id=item_id, content_hash=item.content_hash, type_="bug",
                                 product_area="playback", theme_label="x", labeller="me", now=utcnow())
    reset_processed_data(db)
    with db.read() as s:
        assert ItemRepo(s).count() == 0 and EvalRepo(s).count() == 1
    ingest(db, settings, raws(1))
    with db.read() as s:
        assert len(EvalRepo(s).labels_with_current_items()) == 1  # relinked by content hash


def test_eval_sample_and_report(ctx, settings, tmp_path):
    ingest(ctx.db, settings, JsonlConnector().read(SAMPLE))
    assert drain(ctx, concurrency=2, timeout_s=300)
    out = tmp_path / "to_label.csv"
    assert sample(settings, out, size=40, seed=3) == 0
    rows = read_sample(out)
    assert 30 <= len(rows) <= 40 and len({r.item_id for r in rows}) == len(rows)
    assert [r.item_id for r in read_sample(out)] == [r.item_id for r in rows]

    md = tmp_path / "report.md"
    report(settings, md)
    text = md.read_text()
    assert "No human labels yet: not yet measured" in text
    assert "Offline demo mode" in text
    assert "Adjusted Rand index vs planted themes" in text
    assert "| Cost per 1,000 items | not yet measured |" in text

    # A few labels (test fixtures, standing in for a human) produce accuracy rows.
    with ctx.db.write() as s:
        for r in rows[:10]:
            item = ItemRepo(s).get(r.item_id)
            EvalRepo(s).upsert_label(item_id=r.item_id, content_hash=item.content_hash, type_="bug",
                                     product_area="playback", theme_label="t", labeller="test", now=utcnow())
    report(settings, md)
    text = md.read_text()
    assert "| Type accuracy |" in text and "| 10 |" in text
    assert "Five worst errors" in text
