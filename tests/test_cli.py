"""CLI entry points, run in-process against a temporary database (offline mode)."""

import json

import pytest

from signalhq.cli import main
from signalhq.config import get_settings
from tests.conftest import SAMPLE


@pytest.fixture(autouse=True)
def fresh_settings():
    """Settings are cached per process; each test reads the environment it sets up."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


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


def test_check_failure_prints_hints(tmp_path, capsys, monkeypatch):
    """A failing model listing (e.g. no payment method on the account) explains what to fix."""
    from signalhq.llm.errors import PermanentError
    from signalhq.llm.transport import OpenAICompatTransport

    def refuse(self):
        raise PermanentError("HTTP 403: payment required")

    monkeypatch.setenv("MODEL_API_KEY", "test-key")
    monkeypatch.setattr(OpenAICompatTransport, "list_models", refuse)
    code = main(["--db", str(tmp_path / "cli.db"), "check"])
    out = capsys.readouterr().out
    assert code == 1
    assert "No payment method on file" in out and "API keys" in out
