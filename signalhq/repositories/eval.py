from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from signalhq.models import EvalLabel, FeedbackItem


class EvalRepo:
    def __init__(self, session: Session) -> None:
        self.s = session

    def upsert_label(
        self,
        *,
        item_id: int,
        content_hash: str,
        type_: str,
        product_area: str,
        theme_label: str,
        labeller: str,
        now: datetime,
    ) -> None:
        values = dict(
            item_id=item_id,
            content_hash=content_hash,
            type=type_,
            product_area=product_area,
            theme_label=theme_label,
            labeller=labeller,
            created_at=now,
        )
        stmt = insert(EvalLabel).values(**values)
        self.s.execute(
            stmt.on_conflict_do_update(
                index_elements=["item_id"],
                set_={k: v for k, v in values.items() if k != "item_id"},
            )
        )

    def labelled_hashes(self) -> set[str]:
        return set(self.s.execute(select(EvalLabel.content_hash)).scalars())

    def labels_with_current_items(self) -> list[tuple[EvalLabel, int]]:
        """Labels joined to the current item with the same content hash."""
        rows = self.s.execute(
            select(EvalLabel, FeedbackItem.id).join(
                FeedbackItem, FeedbackItem.content_hash == EvalLabel.content_hash
            )
        ).all()
        return [(label, item_id) for label, item_id in rows]

    def count(self) -> int:
        return len(self.labelled_hashes())
