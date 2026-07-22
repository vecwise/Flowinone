"""Database lifecycle and transaction helpers for the Resource Library."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from .settings import PROJECT_ROOT, get_resource_settings


def sqlite_url(path: Path) -> str:
    """Build a SQLAlchemy SQLite URL for an absolute filesystem path."""
    return f"sqlite+pysqlite:///{path.expanduser().resolve()}"


def upgrade_database(path: Path) -> None:
    """Upgrade one Resource Library database to the latest Alembic revision."""
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", sqlite_url(resolved))
    if resolved.exists():
        # Repair databases produced by the earliest development migration, where
        # SQLite kept non-transactional DDL but rolled back Alembic's version row.
        with sqlite3.connect(resolved) as connection:
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if {"alembic_version", "resources", "resource_origins", "processing_jobs"} <= tables:
                version = connection.execute(
                    "SELECT version_num FROM alembic_version LIMIT 1"
                ).fetchone()
                if version is None:
                    connection.execute(
                        "INSERT INTO alembic_version(version_num) VALUES (?)",
                        ("0001_resource_library",),
                    )
                    connection.commit()
    command.upgrade(config, "head")


class ResourceDatabase:
    """Own the SQLAlchemy engine, migrations, and scoped transactions."""

    def __init__(self, path: Path, *, migrate: bool = True):
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # SQLite permits concurrent readers but only one writer.  Flowinone has
        # Flask request threads plus background resource workers, so serialize
        # their writes in-process instead of making them race until SQLite's
        # busy timeout expires (which is especially easy to hit on Windows).
        self._write_lock = threading.RLock()
        self._catalog_sync_lock = threading.RLock()
        self._catalog_sync_status_lock = threading.Lock()
        self._catalog_sync_status: dict[str, dict[str, Any]] = {}
        if migrate:
            upgrade_database(self.path)

        self.engine = create_engine(
            sqlite_url(self.path),
            future=True,
            connect_args={"timeout": 30, "check_same_thread": False},
        )
        event.listen(self.engine, "connect", self._configure_connection)
        self._session_factory = sessionmaker(
            bind=self.engine,
            class_=Session,
            expire_on_commit=False,
            autoflush=False,
        )

    @staticmethod
    def _configure_connection(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA journal_mode=WAL")
        finally:
            cursor.close()

    @contextmanager
    def session(self, *, write: bool = True) -> Iterator[Session]:
        """Commit a unit of work, serializing it when it can write.

        Read-only callers should pass ``write=False`` so WAL-mode reads can
        continue while a long Catalog projection is being rebuilt.
        """
        lock = self._write_lock if write else _NOOP_LOCK
        with lock:
            session = self._session_factory()
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    @contextmanager
    def write_transaction(self) -> Iterator[Connection]:
        """Open one SQLAlchemy write transaction without competing threads."""
        with self._write_lock:
            with self.engine.begin() as connection:
                yield connection

    @contextmanager
    def catalog_sync(self) -> Iterator[None]:
        """Allow only one full Catalog synchronization at a time."""
        with self._catalog_sync_lock:
            yield

    def set_catalog_sync_status(self, source: str, **state: Any) -> None:
        """Publish transient per-source progress without requiring a DB write.

        SQLite cannot persist a ``retrying`` row while another process owns the
        writer lock.  Keeping this small process-local snapshot lets Navigator
        report the retry immediately and also preserves a final lock failure for
        the lifetime of the app process.
        """
        with self._catalog_sync_status_lock:
            self._catalog_sync_status[source] = dict(state)

    def catalog_sync_status(self) -> dict[str, dict[str, Any]]:
        """Return an isolated snapshot of transient Catalog sync progress."""
        with self._catalog_sync_status_lock:
            return {
                source: dict(state)
                for source, state in self._catalog_sync_status.items()
            }

    @contextmanager
    def raw_write_connection(self):
        """Return a pooled DB-API connection guarded for a manual transaction."""
        with self._write_lock:
            connection = self.engine.raw_connection()
            try:
                yield connection
            finally:
                connection.close()

    def dispose(self) -> None:
        """Release pooled SQLite connections."""
        self.engine.dispose()
        with self._catalog_sync_status_lock:
            self._catalog_sync_status.clear()

    def sync_fts(self, resource_id: str, *, extracted_text: str = "") -> None:
        """Replace one resource's denormalized FTS row."""
        with self.write_transaction() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT title, summary_one_line, summary_short,
                           why_this_matters
                    FROM resources WHERE id=:resource_id
                    """
                ),
                {"resource_id": resource_id},
            ).mappings().first()
            conn.execute(
                text("DELETE FROM resource_fts WHERE resource_id=:resource_id"),
                {"resource_id": resource_id},
            )
            if row:
                conn.execute(
                    text(
                        """
                        INSERT INTO resource_fts (
                            resource_id, title, summary_one_line, summary_short,
                            why_this_matters, extracted_text
                        ) VALUES (
                            :resource_id, :title, :summary_one_line, :summary_short,
                            :why_this_matters, :extracted_text
                        )
                        """
                    ),
                    {
                        "resource_id": resource_id,
                        "title": row["title"] or "",
                        "summary_one_line": row["summary_one_line"] or "",
                        "summary_short": row["summary_short"] or "",
                        "why_this_matters": row["why_this_matters"] or "",
                        "extracted_text": extracted_text or "",
                    },
                )


_DATABASES: dict[Path, ResourceDatabase] = {}
_DATABASES_LOCK = threading.Lock()


class _NoopLock:
    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        return None


_NOOP_LOCK = _NoopLock()


def get_resource_database(path: Optional[Path] = None) -> ResourceDatabase:
    """Return one migrated database instance per absolute path."""
    settings = get_resource_settings()
    settings.ensure_directories()
    resolved = (path or settings.database_path).expanduser().resolve()
    with _DATABASES_LOCK:
        database = _DATABASES.get(resolved)
        if database is None:
            database = ResourceDatabase(resolved)
            _DATABASES[resolved] = database
        return database


def clear_database_cache() -> None:
    """Dispose cached engines; primarily useful for isolated tests."""
    with _DATABASES_LOCK:
        for database in _DATABASES.values():
            database.dispose()
        _DATABASES.clear()


__all__ = [
    "ResourceDatabase",
    "clear_database_cache",
    "get_resource_database",
    "sqlite_url",
    "upgrade_database",
]
