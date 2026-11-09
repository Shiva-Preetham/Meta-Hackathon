"""CLI entry points, run in-process against a temporary database (offline mode)."""

import json

from signalhq.cli import main
from tests.conftest import SAMPLE


def run(capsys, db, *args) -> tuple[int, str]:
    code = main(["--db", str(db), "--offline", *args])
    return code, capsys.readouterr().out


def test_ingest_limit_and_dedupe(tmp_path, capsys):
    db = tmp_path / "cli.db"
    code, out = run(capsys, db, "ingest", str(SAMPLE), "--limit", "5")
    assert code == 0 and "ingested 5 new items, skipped 0 duplicates" in out
    code, out = run(capsys, db, "ingest", str(SAMPLE), "--limit", "5")
    assert "ingested 0 new items, skipped 5 duplicates" in out


def test_stats_reports_queue_and_spend(tmp_path, capsys):
    db = tmp_path / "cli.db"
    run(capsys, db, "ingest", str(SAMPLE), "--limit", "3")
    code, out = run(capsys, db, "stats")
    stats = json.loads(out)
    assert code == 0
    assert stats["mode"] == "offline"
    assert stats["items_by_state"] == {"received": 3}
    assert stats["jobs_by_type"] == {"enrich": {"queued": 3}}
    assert stats["spend_usd"] == 0.0


def test_check_without_key_says_unverified(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    code, out = run(capsys, tmp_path / "cli.db", "check")
    assert code == 2
    assert "NOT VERIFIED" in out
