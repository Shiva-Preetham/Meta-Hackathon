from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from signalhq.models import KV


class KVRepo:
    def __init__(self, session: Session) -> None:
        self.s = session

    def get(self, key: str) -> str | None:
        return self.s.execute(select(KV.value).where(KV.key == key)).scalar_one_or_none()

    def set(self, key: str, value: str) -> None:
        stmt = insert(KV).values(key=key, value=value)
        self.s.execute(stmt.on_conflict_do_update(index_elements=["key"], set_={"value": value}))

    def delete(self, key: str) -> None:
        self.s.execute(delete(KV).where(KV.key == key))
