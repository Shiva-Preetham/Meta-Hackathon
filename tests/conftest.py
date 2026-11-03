from __future__ import annotations

import json
import random
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from signalhq.config import Settings
from signalhq.db import Database
from signalhq.embeddings import HashingEmbedder
from signalhq.llm.gateway import LLMGateway
from signalhq.llm.offline import OfflineHeuristicTransport
from signalhq.llm.transport import ChatRequest, ChatResult
from signalhq.pipeline.stage import StageContext

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "data" / "sample" / "feedback_sample.jsonl"


def make_settings(tmp_path: Path, **overrides) -> Settings:
    base = dict(
        db_path=str(tmp_path / "test.db"),
        offline=True,
        embedder="hashing",
        recluster_delay_s=0,
        score_delay_s=0,
        name_delay_s=0,
        job_backoff_base_s=0.01,
        job_backoff_max_s=0.05,
        llm_backoff_base_s=0.0,
        llm_backoff_max_s=0.0,
        worker_poll_s=0.01,
    )
    base.update(overrides)
    return Settings(_env_file=None, **base)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def db(settings: Settings) -> Iterator[Database]:
    database = Database(settings.db_path)
    database.create_all()
    yield database
    database.dispose()


@pytest.fixture
def gateway(db: Database, settings: Settings) -> LLMGateway:
    return LLMGateway(db, settings, OfflineHeuristicTransport(), sleep=lambda _s: None)


@pytest.fixture
def ctx(db: Database, settings: Settings, gateway: LLMGateway) -> StageContext:
    return StageContext(db=db, settings=settings, gateway=gateway, embedder=HashingEmbedder())


class ScriptedTransport:
    """Fake billable transport: returns or raises the scripted outcomes in order."""

    name = "scripted"

    def __init__(self, outcomes: list, billable: bool = True) -> None:
        self.outcomes = list(outcomes)
        self.billable = billable
        self.requests: list[ChatRequest] = []

    def complete(self, request: ChatRequest, timeout_s: float) -> ChatResult:
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, ChatResult):
            return outcome
        content = outcome if isinstance(outcome, str) else json.dumps(outcome)
        return ChatResult(content=content, input_tokens=1000, output_tokens=100)

    def list_models(self) -> list[str]:
        return ["muse-spark-1.3"]


@pytest.fixture
def scripted_gateway(db: Database, tmp_path: Path) -> Callable[..., tuple[LLMGateway, ScriptedTransport, list[float]]]:
    def build(outcomes: list, **settings_overrides) -> tuple[LLMGateway, ScriptedTransport, list[float]]:
        st = make_settings(tmp_path, offline=False, model_api_key="test-key", **settings_overrides)
        transport = ScriptedTransport(outcomes)
        sleeps: list[float] = []
        gw = LLMGateway(db, st, transport, sleep=sleeps.append, rng=random.Random(0))
        return gw, transport, sleeps

    return build
