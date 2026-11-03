"""FastAPI app: read API, idempotent webhook ingest, metrics, replay controls, labelling.

OpenAPI docs are served at ``/docs``. The dashboard and labelling page are static files
under ``signalhq/web/static`` (no build step).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from fastapi import FastAPI, HTTPException, Query, Response, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from signalhq import __version__
from signalhq.api import views
from signalhq.config import PRODUCT_AREAS
from signalhq.connectors.webhook import from_webhook
from signalhq.db import utcnow
from signalhq.domain.schemas import FEEDBACK_TYPES, FeedbackIn
from signalhq.eval.labelling import next_to_label
from signalhq.ingest import ingest
from signalhq.replay import ReplayController, read_status
from signalhq.repositories import EvalRepo, ItemRepo

if TYPE_CHECKING:
    from signalhq.queue.worker import WorkerPool
    from signalhq.runtime import Runtime

ROOT = Path(__file__).resolve().parents[2]
STATIC = Path(__file__).resolve().parents[1] / "web" / "static"
DATA_DIR = ROOT / "data"
DEFAULT_REPLAY_FILE = DATA_DIR / "sample" / "feedback_sample.jsonl"


class IngestResponse(BaseModel):
    id: int
    created: bool


class ReplayRequest(BaseModel):
    file: str | None = Field(default=None, description="Path under data/ (default: the synthetic sample)")
    connector: Literal["jsonl", "csv"] = "jsonl"
    speed: float | None = Field(default=None, gt=0, description="Data-seconds per real second")
    seconds: float = Field(default=90, gt=0, description="Target replay length when speed is not given")
    reset: bool = Field(default=True, description="Delete processed data first (keeps cache, ledger, labels)")


class LabelIn(BaseModel):
    item_id: int
    type: str
    product_area: str
    theme_label: str = Field(min_length=1, max_length=200)
    labeller: str = Field(default="human", max_length=64)


def _safe_data_path(raw: str | None) -> Path:
    """Replay files must live under data/: the API never reads arbitrary paths."""
    path = DEFAULT_REPLAY_FILE if raw is None else (ROOT / raw).resolve()
    if not path.is_relative_to(DATA_DIR.resolve()) or not path.is_file():
        raise HTTPException(status_code=400, detail="file must be an existing file under data/")
    return path


def create_app(runtime: Runtime, pool: WorkerPool | None = None) -> FastAPI:
    db, settings = runtime.db, runtime.settings
    replay = ReplayController(db, settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        replay.stop()

    app = FastAPI(
        title="Signal API",
        version=__version__,
        description="Customer feedback ranked by simulated revenue at risk. Revenue figures are simulated.",
        lifespan=lifespan,
    )
    app.state.runtime = runtime
    app.state.replay = replay

    # --- read API -----------------------------------------------------------------------
    @app.get("/api/summary", tags=["read"])
    def summary() -> dict:
        with db.read() as s:
            out = views.summary_view(s, settings, runtime.ctx.embedder.name)
        out["replay"] = read_status(db).__dict__
        out["workers_running"] = bool(pool and pool.running)
        return out

    @app.get("/api/themes", tags=["read"])
    def themes(
        sort: Literal["score", "revenue", "growth", "mentions", "size"] = "score",
        limit: int = Query(default=50, ge=1, le=500),
    ) -> list[dict]:
        with db.read() as s:
            return views.themes_view(s, sort, limit)

    @app.get("/api/themes/{theme_id}", tags=["read"])
    def theme(theme_id: int) -> dict:
        with db.read() as s:
            detail = views.theme_detail(s, theme_id, settings)
        if detail is None:
            raise HTTPException(status_code=404, detail="theme not found")
        return detail

    @app.get("/api/items/{item_id}", tags=["read"])
    def item(item_id: int) -> dict:
        with db.read() as s:
            detail = ItemRepo(s).detail(item_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="item not found")
        return detail

    @app.get("/api/metrics", tags=["read"])
    def metrics() -> dict:
        with db.read() as s:
            return views.metrics_view(s, settings, bool(pool and pool.running))

    # --- webhook ------------------------------------------------------------------------
    @app.post("/api/feedback", tags=["ingest"], response_model=IngestResponse, status_code=201)
    def post_feedback(payload: FeedbackIn, response: Response) -> IngestResponse:
        """Idempotent: re-posting the same text/source/external_id returns the existing id
        with ``created: false`` and status 200."""
        result = ingest(db, settings, [from_webhook(payload, utcnow())])
        created = result.created == 1
        if not created:
            response.status_code = status.HTTP_200_OK
        return IngestResponse(id=result.item_ids[0], created=created)

    # --- replay -------------------------------------------------------------------------
    @app.post("/api/replay/start", tags=["replay"])
    def replay_start(req: ReplayRequest | None = None) -> dict:
        req = req or ReplayRequest()
        try:
            st = replay.start(_safe_data_path(req.file), speed=req.speed, seconds=req.seconds,
                              reset=req.reset, connector=req.connector)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return st.__dict__

    @app.post("/api/replay/stop", tags=["replay"])
    def replay_stop() -> dict:
        return replay.stop().__dict__

    @app.get("/api/replay", tags=["replay"])
    def replay_status() -> dict:
        return {**read_status(db).__dict__, "running": replay.running}

    # --- labelling ----------------------------------------------------------------------
    sample_path = ROOT / settings.eval_sample_path

    @app.get("/api/labels/next", tags=["eval"])
    def label_next() -> dict:
        with db.read() as s:
            row, done, total = next_to_label(s, sample_path)
        return {
            "item": None if row is None else {"item_id": row.item_id, "text": row.text, "source": row.source},
            "done": done,
            "total": total,
            "types": list(FEEDBACK_TYPES),
            "areas": list(PRODUCT_AREAS),
        }

    @app.get("/api/labels/themes", tags=["eval"])
    def label_theme_names() -> list[str]:
        """Theme labels used so far, for autocomplete on the labelling page."""
        with db.read() as s:
            return EvalRepo(s).theme_labels()

    @app.post("/api/labels", tags=["eval"], status_code=201)
    def label_post(label: LabelIn) -> dict:
        if label.type not in FEEDBACK_TYPES or label.product_area not in PRODUCT_AREAS:
            raise HTTPException(status_code=422, detail="unknown type or product area")
        with db.write() as s:
            item = ItemRepo(s).get(label.item_id)
            if item is None:
                raise HTTPException(status_code=404, detail="item not found")
            EvalRepo(s).upsert_label(
                item_id=item.id, content_hash=item.content_hash, type_=label.type,
                product_area=label.product_area, theme_label=label.theme_label.strip(),
                labeller=label.labeller, now=utcnow(),
            )
        return {"ok": True}

    # --- pages --------------------------------------------------------------------------
    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/label", include_in_schema=False)
    def label_page() -> FileResponse:
        return FileResponse(STATIC / "label.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    return app
