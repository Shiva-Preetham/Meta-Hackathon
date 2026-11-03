import pytest
from fastapi.testclient import TestClient

from signalhq.api.app import create_app
from signalhq.connectors import JsonlConnector
from signalhq.ingest import ingest
from signalhq.queue.worker import drain
from signalhq.runtime import Runtime
from tests.conftest import SAMPLE


@pytest.fixture
def runtime(ctx, settings, db):
    return Runtime(settings=settings, db=db, gateway=ctx.gateway, ctx=ctx)


@pytest.fixture
def client(runtime):
    with TestClient(create_app(runtime)) as c:
        yield c


@pytest.fixture
def loaded(runtime, settings, client):
    ingest(runtime.db, settings, JsonlConnector().read(SAMPLE))
    assert drain(runtime.ctx, concurrency=2, timeout_s=300)
    return client


def test_webhook_is_idempotent(client):
    body = {"text": "The app crashes on launch", "source": "webhook", "external_id": "t-1",
            "account_ref": "acct-9", "created_at": "2026-10-20T10:00:00Z"}
    first = client.post("/api/feedback", json=body)
    second = client.post("/api/feedback", json=body)
    assert first.status_code == 201 and first.json()["created"] is True
    assert second.status_code == 200 and second.json() == {"id": first.json()["id"], "created": False}
    item = client.get(f"/api/items/{first.json()['id']}").json()
    assert item["state"] == "received" and item["account"]["simulated"] is True


def test_webhook_validates_payload(client):
    assert client.post("/api/feedback", json={"text": ""}).status_code == 422


def test_ranked_themes_and_detail(loaded):
    themes = loaded.get("/api/themes?sort=score").json()
    assert len(themes) >= 6
    assert [t["rank"] for t in themes] == list(range(1, len(themes) + 1))
    top = themes[0]
    assert top["score"] > 0 and top["is_new"] is True and len(top["sparkline"]) == 14
    detail = loaded.get(f"/api/themes/{top['id']}").json()
    assert "log1p" in detail["explanation"]["formula"]
    assert detail["explanation"]["filled"].endswith(f"{top['score']:.3f}")
    assert detail["quotes"] and all(q["quote"] in q["text"] for q in detail["quotes"])
    assert len(detail["trend"]["daily_counts"]) == 28
    by_size = loaded.get("/api/themes?sort=size").json()
    assert by_size[0]["size"] == max(t["size"] for t in themes)


def test_unknown_ids_return_404(client):
    assert client.get("/api/themes/999").status_code == 404
    assert client.get("/api/items/999").status_code == 404
    assert client.get("/api/themes?sort=bogus").status_code == 422


def test_summary_and_metrics(loaded):
    summary = loaded.get("/api/summary").json()
    assert summary["mode"] == "offline" and summary["model"] == "offline-heuristic-v1"
    assert summary["items_total"] == 300 and summary["revenue_simulated"] is True
    assert "synthetic" in summary["data_note"] and "simulated" in summary["data_note"]
    assert summary["revenue_at_risk_dollars"] > 0
    metrics = loaded.get("/api/metrics").json()
    assert metrics["queue_by_state"] == {"done": metrics["queue_by_state"]["done"]}
    assert set(metrics["stage_latency"]) >= {"enrich", "embed", "cluster"}
    assert metrics["llm"]["cost_usd"] == 0.0


def test_replay_rejects_paths_outside_data(client):
    r = client.post("/api/replay/start", json={"file": "../../etc/passwd"})
    assert r.status_code == 400
    r = client.post("/api/replay/start", json={"file": "signalhq/config.py"})
    assert r.status_code == 400


def test_labelling_flow(loaded, runtime, tmp_path, settings):
    from signalhq.eval.labelling import stratified_sample, write_sample

    with runtime.db.read() as s:
        rows, _ = stratified_sample(s, 5, seed=1)
    path = tmp_path / "to_label.csv"
    write_sample(rows, path)
    runtime.settings = settings.model_copy(update={"eval_sample_path": str(path)})
    with TestClient(create_app(runtime)) as c:
        nxt = c.get("/api/labels/next").json()
        assert nxt["done"] == 0 and nxt["total"] == 5
        assert "type" not in nxt["item"]  # no model prediction shown to the labeller
        ok = c.post("/api/labels", json={"item_id": nxt["item"]["item_id"], "type": "bug",
                                         "product_area": "playback", "theme_label": "stops on lock"})
        assert ok.status_code == 201
        assert c.get("/api/labels/next").json()["done"] == 1
        assert c.get("/api/labels/themes").json() == ["stops on lock"]
        bad = c.post("/api/labels", json={"item_id": 1, "type": "rant", "product_area": "playback", "theme_label": "x"})
        assert bad.status_code == 422


def test_pages_are_served(client):
    assert "Signal" in client.get("/").text
    assert "labelling" in client.get("/label").text.lower()
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/openapi.json").json()["info"]["title"] == "Signal API"
