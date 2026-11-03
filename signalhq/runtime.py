"""Wiring: builds the database, gateway, embedder and stage context from settings."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from signalhq.config import Settings
from signalhq.db import Database
from signalhq.embeddings import build_embedder
from signalhq.llm import build_gateway
from signalhq.llm.gateway import LLMGateway
from signalhq.pipeline.stage import StageContext

log = logging.getLogger(__name__)


@dataclass
class Runtime:
    settings: Settings
    db: Database
    gateway: LLMGateway
    ctx: StageContext

    @property
    def mode(self) -> str:
        return "offline demo mode" if self.settings.is_offline else "online (Meta Model API)"


def build_runtime(settings: Settings) -> Runtime:
    db = Database(settings.db_path)
    db.create_all()
    gateway = build_gateway(db, settings)
    embedder = build_embedder(settings.embedder, settings.embed_model)
    ctx = StageContext(db=db, settings=settings, gateway=gateway, embedder=embedder)
    log.info(
        "Signal runtime: %s, model=%s, embedder=%s, db=%s, tau=%.2f",
        "offline demo mode" if settings.is_offline else "online",
        settings.effective_model,
        embedder.name,
        settings.db_path,
        ctx.cluster.tau,
    )
    return Runtime(settings=settings, db=db, gateway=gateway, ctx=ctx)
