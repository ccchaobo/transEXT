"""Database engine, session factory and schema creation.

SQLite is the P0 store (spec 7.1).  The settings here matter for correctness
rather than speed:

* ``journal_mode=WAL`` lets the API read while the worker writes.
* ``busy_timeout`` makes concurrent writers wait instead of failing with
  "database is locked".
* ``foreign_keys=ON`` makes the cascades in :mod:`rr_storage.models` actually
  fire, which is what guarantees deleting a document removes its derived rows.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

__all__ = [
    "Database",
    "create_database",
    "SCHEMA_VERSION",
]

SCHEMA_VERSION = "1"


def _apply_sqlite_pragmas(dbapi_connection, _record) -> None:  # noqa: ANN001
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=15000")
    finally:
        cursor.close()


class Database:
    """Owns the engine and hands out sessions."""

    def __init__(self, url: str, *, echo: bool = False) -> None:
        self.url = url
        kwargs: dict = {"echo": echo, "future": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30.0}
        self.engine: Engine = create_engine(url, **kwargs)
        if url.startswith("sqlite"):
            event.listen(self.engine, "connect", _apply_sqlite_pragmas)
        self._session_factory = sessionmaker(
            bind=self.engine, expire_on_commit=False, autoflush=False, future=True
        )

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)

    def drop_all(self) -> None:
        Base.metadata.drop_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Transactional session: commits on success, rolls back on error."""
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def new_session(self) -> Session:
        """A session the caller must close; used for long-lived worker loops."""
        return self._session_factory()

    def dispose(self) -> None:
        """Close pooled connections.

        Required before deleting the database file on Windows, where an open
        handle makes the file undeletable, and also on clean shutdown.
        """
        self.engine.dispose()

    def health(self) -> str:
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return "ok"
        except Exception as exc:  # pragma: no cover - reported through /health
            return f"error: {type(exc).__name__}"


def create_database(data_dir: str | Path, *, echo: bool = False) -> Database:
    """Create (or open) the database inside ``data_dir``."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "reader.sqlite3"
    url = os.environ.get("RESEARCH_READER_DB_URL") or f"sqlite+pysqlite:///{db_path.as_posix()}"
    db = Database(url, echo=echo)
    db.create_all()
    return db
