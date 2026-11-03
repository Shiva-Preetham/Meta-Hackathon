"""Engine and session handling for SQLite in WAL mode (ADR-002).

Two kinds of transactions are offered:

* ``Database.read()`` starts a deferred transaction. In WAL mode readers never block
  writers, so the API and dashboard can read while workers write.
* ``Database.write()`` starts ``BEGIN IMMEDIATE``, taking SQLite's single write lock up
  front. A deferred transaction that reads and then writes can fail with SQLITE_BUSY when
  another writer committed in between; taking the lock first removes that class of error
  and makes read-modify-write sequences (job claims, centroid updates) serialisable.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from signalhq.models import Base


def utcnow() -> datetime:
    """Naive UTC timestamp. All times in the database are naive UTC."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def make_engine(db_path: str | Path) -> Engine:
    path = str(db_path)
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite+pysqlite:///{path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        pool_size=16,
        max_overflow=16,
    )

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _record) -> None:  # pragma: no cover - exercised implicitly
        # Let SQLAlchemy (not the sqlite3 module) decide when transactions begin, so we
        # can choose DEFERRED or IMMEDIATE per session.
        dbapi_conn.isolation_level = None
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()

    @event.listens_for(engine, "begin")
    def _on_begin(conn) -> None:  # pragma: no cover - exercised implicitly
        mode = conn.get_execution_options().get("sqlite_begin", "DEFERRED")
        conn.exec_driver_sql(f"BEGIN {mode}")

    return engine


class Database:
    """Owns the engine and hands out short-lived sessions."""

    def __init__(self, db_path: str | Path) -> None:
        self.path = str(db_path)
        self.engine = make_engine(db_path)
        self._read_factory = sessionmaker(self.engine, expire_on_commit=False)
        self._write_factory = sessionmaker(
            self.engine.execution_options(sqlite_begin="IMMEDIATE"), expire_on_commit=False
        )

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)

    def drop_all(self) -> None:
        Base.metadata.drop_all(self.engine)

    @contextmanager
    def read(self) -> Iterator[Session]:
        session = self._read_factory()
        try:
            yield session
        finally:
            # close() ends the read transaction and detaches objects without expiring
            # them, so loaded attributes stay readable after the block.
            session.close()

    @contextmanager
    def write(self) -> Iterator[Session]:
        session = self._write_factory()
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        self.engine.dispose()
