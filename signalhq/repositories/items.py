from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import numpy as np
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from signalhq.domain.schemas import RawFeedback
from signalhq.domain.states import ItemState, transition
from signalhq.models import (
    Account,
    Embedding,
    Enrichment,
    FeedbackItem,
    Job,
    Theme,
    ThemeMember,
    ThemeScore,
)


def vector_to_blob(vector: np.ndarray) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


def blob_to_vector(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32).copy()


class ItemRepo:
    def __init__(self, session: Session) -> None:
        self.s = session

    # --- ingestion ---------------------------------------------------------------------
    def insert_if_new(
        self,
        raw: RawFeedback,
        content_hash: str,
        account_id: int | None,
        now: datetime,
    ) -> tuple[int, bool]:
        """Insert unless the content hash exists. Returns (item_id, created)."""
        result = self.s.execute(
            insert(FeedbackItem)
            .values(
                source=raw.source,
                external_id=raw.external_id,
                account_id=account_id,
                text=raw.text,
                created_at=raw.created_at,
                ingested_at=now,
                content_hash=content_hash,
                state=ItemState.RECEIVED.value,
                meta_json=json.dumps(raw.meta, sort_keys=True) if raw.meta else None,
            )
            .on_conflict_do_nothing(index_elements=["content_hash"])
        )
        item_id = self.s.execute(
            select(FeedbackItem.id).where(FeedbackItem.content_hash == content_hash)
        ).scalar_one()
        return item_id, result.rowcount == 1

    # --- reads ---------------------------------------------------------------------------
    def get(self, item_id: int) -> FeedbackItem | None:
        return self.s.get(FeedbackItem, item_id)

    def first(self, n: int) -> list[FeedbackItem]:
        return list(self.s.execute(select(FeedbackItem).order_by(FeedbackItem.id).limit(n)).scalars())

    def count(self) -> int:
        return self.s.execute(select(func.count(FeedbackItem.id))).scalar_one()

    def counts_by_state(self) -> dict[str, int]:
        rows = self.s.execute(
            select(FeedbackItem.state, func.count()).group_by(FeedbackItem.state)
        ).all()
        return {state: n for state, n in rows}

    def data_clock(self) -> datetime | None:
        """Latest feedback timestamp: "now" in data time."""
        return self.s.execute(select(func.max(FeedbackItem.created_at))).scalar_one()

    def sources(self) -> dict[str, int]:
        rows = self.s.execute(
            select(FeedbackItem.source, func.count()).group_by(FeedbackItem.source)
        ).all()
        return {source: n for source, n in rows}

    def synthetic_count(self) -> int:
        return self.s.execute(
            select(func.count()).where(FeedbackItem.meta_json.like('%"synthetic": true%'))
        ).scalar_one()

    # --- state ---------------------------------------------------------------------------
    def set_state(self, item_id: int, target: ItemState, error: str | None = None) -> None:
        item = self.s.get(FeedbackItem, item_id)
        if item is None:
            raise KeyError(f"item {item_id} not found")
        item.state = transition(item.state, target).value
        item.error = error

    # --- enrichment ----------------------------------------------------------------------
    def get_enrichment(self, item_id: int) -> Enrichment | None:
        return self.s.get(Enrichment, item_id)

    def save_enrichment(self, enrichment: Enrichment) -> None:
        self.s.merge(enrichment)

    # --- embeddings ----------------------------------------------------------------------
    def get_embedding(self, item_id: int) -> np.ndarray | None:
        row = self.s.get(Embedding, item_id)
        return None if row is None else blob_to_vector(row.vector)

    def save_embedding(self, item_id: int, model: str, vector: np.ndarray) -> None:
        self.s.merge(
            Embedding(item_id=item_id, model=model, dim=int(vector.shape[0]), vector=vector_to_blob(vector))
        )

    def _pool_query(self):
        member = select(ThemeMember.item_id).where(ThemeMember.item_id == FeedbackItem.id)
        return (
            select(FeedbackItem.id, Embedding.vector)
            .join(Embedding, Embedding.item_id == FeedbackItem.id)
            .where(FeedbackItem.state == ItemState.EMBEDDED.value)
            .where(~member.exists())
        )

    def pool(self, limit: int) -> tuple[list[int], np.ndarray | None]:
        """Embedded items not assigned to any theme (newest first, capped)."""
        rows = self.s.execute(
            self._pool_query().order_by(FeedbackItem.created_at.desc()).limit(limit)
        ).all()
        if not rows:
            return [], None
        ids = [r[0] for r in rows]
        return ids, np.vstack([blob_to_vector(r[1]) for r in rows])

    def pool_size(self) -> int:
        return self.s.execute(select(func.count()).select_from(self._pool_query().subquery())).scalar_one()

    # --- detail views -------------------------------------------------------------------
    def detail(self, item_id: int) -> dict[str, Any] | None:
        item = self.s.get(FeedbackItem, item_id)
        if item is None:
            return None
        account = self.s.get(Account, item.account_id) if item.account_id else None
        enrichment = self.s.get(Enrichment, item_id)
        member = self.s.execute(
            select(ThemeMember.theme_id, ThemeMember.similarity).where(ThemeMember.item_id == item_id)
        ).first()
        return {
            "id": item.id,
            "source": item.source,
            "external_id": item.external_id,
            "text": item.text,
            "created_at": item.created_at.isoformat(),
            "ingested_at": item.ingested_at.isoformat(),
            "state": item.state,
            "error": item.error,
            "meta": json.loads(item.meta_json) if item.meta_json else {},
            "account": None
            if account is None
            else {
                "external_ref": account.external_ref,
                "plan": account.plan,
                "mrr_dollars": account.mrr_cents / 100,
                "simulated": account.simulated,
            },
            "enrichment": None
            if enrichment is None
            else {
                "type": enrichment.type,
                "product_area": enrichment.product_area,
                "product_area_raw": enrichment.product_area_raw,
                "severity": enrichment.severity,
                "sentiment": enrichment.sentiment,
                "churn_signal": enrichment.churn_signal,
                "key_quote": enrichment.key_quote,
                "quote_verified": enrichment.quote_verified,
                "language": enrichment.language,
                "model": enrichment.model,
                "prompt_version": enrichment.prompt_version,
            },
            "theme": None if member is None else {"id": member[0], "similarity": round(member[1], 4)},
        }

    def enriched_rows(self) -> list[tuple[FeedbackItem, Enrichment]]:
        return list(
            self.s.execute(
                select(FeedbackItem, Enrichment).join(Enrichment, Enrichment.item_id == FeedbackItem.id)
            ).all()
        )

    # --- maintenance --------------------------------------------------------------------
    def reset_processed_data(self) -> None:
        """Delete items and everything derived from them. Keeps the LLM cache and ledger
        (so a replay re-run is free) and human eval labels (keyed by content hash)."""
        for model in (ThemeScore, ThemeMember, Theme, Embedding, Enrichment, Job, FeedbackItem, Account):
            self.s.execute(delete(model))
