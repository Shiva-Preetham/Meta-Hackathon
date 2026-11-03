"""Stage protocol and the context every stage receives."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from signalhq.config import CLUSTER_DEFAULTS, Settings
from signalhq.db import Database, utcnow
from signalhq.embeddings import Embedder
from signalhq.llm.gateway import LLMGateway
from signalhq.repositories import ClaimedJob, NewJob

# Job types, in pipeline order.
ENRICH, EMBED, CLUSTER, RECLUSTER, NAME_THEME, SCORE = (
    "enrich",
    "embed",
    "cluster",
    "recluster",
    "name_theme",
    "score",
)
JOB_TYPES = (ENRICH, EMBED, CLUSTER, RECLUSTER, NAME_THEME, SCORE)


@dataclass(frozen=True)
class ClusterParams:
    tau: float
    link_tau: float
    merge_tau: float

    @classmethod
    def from_settings(cls, settings: Settings, embedder_kind: str) -> ClusterParams:
        defaults = CLUSTER_DEFAULTS.get(embedder_kind, CLUSTER_DEFAULTS["hashing"])
        return cls(
            tau=settings.cluster_tau if settings.cluster_tau is not None else defaults["tau"],
            link_tau=settings.cluster_link_tau if settings.cluster_link_tau is not None else defaults["link_tau"],
            merge_tau=settings.cluster_merge_tau if settings.cluster_merge_tau is not None else defaults["merge_tau"],
        )


@dataclass
class StageContext:
    db: Database
    settings: Settings
    gateway: LLMGateway
    embedder: Embedder
    now: Callable[[], datetime] = utcnow
    cluster: ClusterParams = field(init=False)

    def __post_init__(self) -> None:
        self.cluster = ClusterParams.from_settings(self.settings, self.embedder.kind)


class Stage(Protocol):
    job_type: str

    def run(self, job: ClaimedJob, ctx: StageContext) -> list[NewJob]:
        """Do the work for one job and return follow-up jobs. Must be idempotent."""
        ...
