"""Opt-in, low-impact change detection for Catalog source projections.

The watcher runs only in the dedicated worker process.  It deliberately uses
small source fingerprints and the existing durable job queue instead of trying
to synchronize inside a web request or introducing a second queue.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from typing import Any

from sqlalchemy import text

from config import CHROME_BOOKMARK_PATH, DB_route_external
from src.file_handler.eagle_integration import get_eagle_catalog_source
from src.flowinone.paths import item_database_path
from src.flowinone.resource_library.canonical import hash_text
from src.flowinone.resource_library.database import ResourceDatabase
from src.flowinone.resource_library.jobs import JobQueue
from src.flowinone.resource_library.models import utc_now_text


CATALOG_SOURCE_WATCH_COMPONENT = "catalog_source_watcher"
CATALOG_SOURCE_WATCH_SOURCES = ("local", "eagle", "bookmarks", "resources")
DEFAULT_POLL_INTERVAL_SECONDS = 30
DEFAULT_DEBOUNCE_SECONDS = 8
DEFAULT_LOCAL_MAX_DIRECTORIES = 20_000


def _bounded_integer(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(int(value), maximum))
    except (TypeError, ValueError):
        return default


def _enabled_from_environment() -> bool:
    return os.environ.get("FLOWINONE_SOURCE_WATCHER", "0").lower() in {
        "1",
        "true",
        "yes",
    }


def _file_signature(path: Path) -> str | None:
    try:
        stat = path.expanduser().resolve().stat()
    except OSError:
        return None
    return f"{path.expanduser().resolve()}:{stat.st_mtime_ns}:{stat.st_size}"


def _local_root_signature(root: str | Path | None) -> str | None:
    """Fingerprint directory structure without reading media file contents.

    A direct file addition/removal updates its parent directory mtime, so
    tracking directory names and mtimes catches catalog-relevant local changes
    while avoiding a costly hash of every media file.  The cap keeps an
    accidentally broad root from monopolising the worker.
    """
    if not root:
        return None
    resolved = Path(root).expanduser().resolve()
    if not resolved.is_dir():
        return None
    limit = _bounded_integer(
        os.environ.get("FLOWINONE_WATCH_LOCAL_MAX_DIRECTORIES"),
        DEFAULT_LOCAL_MAX_DIRECTORIES,
        100,
        100_000,
    )
    digest = hashlib.sha256()
    seen = 0
    try:
        for current, directories, _files in os.walk(resolved, followlinks=False):
            directories[:] = sorted(
                entry for entry in directories if not entry.startswith(".")
            )
            current_path = Path(current)
            stat = current_path.stat()
            digest.update(
                f"{current_path.relative_to(resolved)}:{stat.st_mtime_ns}:".encode()
            )
            seen += 1
            if seen > limit:
                return None
    except OSError:
        return None
    return f"{resolved}:{seen}:{digest.hexdigest()}"


def _eagle_signature() -> str | None:
    try:
        source = get_eagle_catalog_source(force=True)
    except Exception:
        return None
    identity = str(source.get("identity") or "")
    version = str(source.get("version") or "")
    return f"{identity}:{version}" if identity and version else None


def _resource_signature(database: ResourceDatabase) -> str | None:
    with database.engine.connect() as conn:
        row = conn.execute(
            text("SELECT COUNT(*) AS count, MAX(updated_at) AS updated_at FROM resources")
        ).mappings().one()
    return f"{int(row['count'] or 0)}:{row['updated_at'] or ''}"


class CatalogSourceWatcher:
    """Persist source observations and queue debounced incremental syncs."""

    def __init__(
        self,
        database: ResourceDatabase,
        *,
        queue: JobQueue | None = None,
        signatures: Mapping[str, Callable[[], str | None]] | None = None,
        poll_interval_seconds: int | None = None,
        debounce_seconds: int | None = None,
        stop_event: Event | None = None,
    ) -> None:
        self.database = database
        self.queue = queue or JobQueue(database)
        self.poll_interval_seconds = _bounded_integer(
            poll_interval_seconds
            if poll_interval_seconds is not None
            else os.environ.get("FLOWINONE_SOURCE_WATCH_INTERVAL_SECONDS"),
            DEFAULT_POLL_INTERVAL_SECONDS,
            5,
            3600,
        )
        self.debounce_seconds = _bounded_integer(
            debounce_seconds
            if debounce_seconds is not None
            else os.environ.get("FLOWINONE_SOURCE_WATCH_DEBOUNCE_SECONDS"),
            DEFAULT_DEBOUNCE_SECONDS,
            0,
            300,
        )
        self.stop_event = stop_event or Event()
        self.signatures = dict(
            signatures
            or {
                "local": lambda: _local_root_signature(DB_route_external)
                or _file_signature(item_database_path()),
                "eagle": _eagle_signature,
                "bookmarks": lambda: _file_signature(Path(CHROME_BOOKMARK_PATH)),
                "resources": lambda: _resource_signature(self.database),
            }
        )

    @staticmethod
    def _parse_metadata(value: Any) -> dict[str, Any]:
        try:
            parsed = json.loads(str(value or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = {}
        return parsed if isinstance(parsed, dict) else {}

    def _state(self) -> dict[str, Any]:
        with self.database.engine.connect() as conn:
            raw = conn.execute(
                text(
                    "SELECT metadata_json FROM runtime_state "
                    "WHERE component=:component"
                ),
                {"component": CATALOG_SOURCE_WATCH_COMPONENT},
            ).scalar_one_or_none()
        state = self._parse_metadata(raw)
        state["enabled"] = bool(
            state.get("enabled", _enabled_from_environment())
        )
        state["sources"] = (
            state.get("sources") if isinstance(state.get("sources"), dict) else {}
        )
        return state

    def _store_state(self, state: dict[str, Any], *, now: str | None = None) -> None:
        timestamp = now or utc_now_text()
        with self.database.write_transaction() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO runtime_state(component,heartbeat_at,metadata_json)
                    VALUES(:component,:now,:metadata)
                    ON CONFLICT(component) DO UPDATE SET
                        heartbeat_at=excluded.heartbeat_at,
                        metadata_json=excluded.metadata_json
                    """
                ),
                {
                    "component": CATALOG_SOURCE_WATCH_COMPONENT,
                    "now": timestamp,
                    "metadata": json.dumps(state, ensure_ascii=False, sort_keys=True),
                },
            )

    @staticmethod
    def _seconds_since(timestamp: str | None, now: datetime) -> float:
        if not timestamp:
            return float("inf")
        try:
            recorded = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            return float("inf")
        if recorded.tzinfo is None:
            recorded = recorded.replace(tzinfo=timezone.utc)
        return max(0.0, (now - recorded).total_seconds())

    def set_enabled(self, enabled: bool) -> dict[str, Any]:
        state = self._state()
        state["enabled"] = bool(enabled)
        state["updated_at"] = utc_now_text()
        self._store_state(state, now=state["updated_at"])
        return self.status()

    def _queue_source(self, source: str, signature: str) -> dict[str, Any]:
        payload = {
            "sources": [source],
            "full_rescan": False,
            "trigger": "source_watcher",
            "source_signature": signature,
        }
        if source == "local":
            # Raw local media changes must be indexed before Catalog can see
            # them; the crawler remains in the worker process.
            payload["refresh_local_index"] = True
        return self.queue.queue(
            "catalog_sync",
            resource_id=None,
            payload=payload,
            input_hash=hash_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))[
                :16
            ],
            priority=15,
            force=False,
        )

    def process_once(self) -> dict[str, Any]:
        """Observe all sources once and queue only settled source changes."""
        state = self._state()
        if not state["enabled"]:
            return self.status(state)
        now = datetime.now(timezone.utc).replace(microsecond=0)
        now_text = now.isoformat()
        sources = state["sources"]
        for source, provider in self.signatures.items():
            previous = dict(sources.get(source) or {})
            try:
                signature = provider()
            except Exception as exc:  # pragma: no cover - provider boundary
                sources[source] = {
                    **previous,
                    "state": "error",
                    "error": str(exc)[:300],
                    "observed_at": now_text,
                }
                continue
            if signature is None:
                sources[source] = {
                    **previous,
                    "state": "unavailable",
                    "observed_at": now_text,
                }
                continue
            if not previous.get("signature"):
                sources[source] = {
                    "signature": signature,
                    "state": "watching",
                    "observed_at": now_text,
                }
                continue
            if previous.get("signature") != signature:
                previous = {
                    "signature": signature,
                    "state": "changed",
                    "changed_at": now_text,
                    "observed_at": now_text,
                }
            else:
                previous["observed_at"] = now_text
            if (
                previous.get("state") == "changed"
                and self._seconds_since(previous.get("changed_at"), now)
                >= self.debounce_seconds
            ):
                job = self._queue_source(source, signature)
                previous.update(
                    {
                        "state": "queued",
                        "job_id": job["id"],
                        "queued_at": now_text,
                        "job_status": job.get("status"),
                    }
                )
            sources[source] = previous
        state["last_checked_at"] = now_text
        state["sources"] = sources
        self._store_state(state, now=now_text)
        return self.status(state)

    def status(self, state: dict[str, Any] | None = None) -> dict[str, Any]:
        state = state or self._state()
        rendered_sources: dict[str, dict[str, Any]] = {}
        for source in CATALOG_SOURCE_WATCH_SOURCES:
            source_state = dict((state.get("sources") or {}).get(source) or {})
            job_id = source_state.get("job_id")
            if job_id:
                job = self.queue.get(str(job_id))
                if job:
                    source_state["job_status"] = job.get("status")
                    if job.get("status") == "complete":
                        source_state["state"] = "complete"
                    elif job.get("status") in {"failed", "retry", "running"}:
                        source_state["state"] = str(job.get("status"))
                        source_state["error"] = job.get("error_message")
            rendered_sources[source] = source_state
        return {
            "enabled": bool(state.get("enabled")),
            "poll_interval_seconds": self.poll_interval_seconds,
            "debounce_seconds": self.debounce_seconds,
            "last_checked_at": state.get("last_checked_at"),
            "sources": rendered_sources,
        }

    def run_forever(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.process_once()
            except Exception:  # pragma: no cover - runtime resilience
                # The next interval can recover a temporarily locked database or
                # an unavailable source without ending the worker runtime.
                pass
            self.stop_event.wait(self.poll_interval_seconds)

    def stop(self) -> None:
        self.stop_event.set()


__all__ = [
    "CATALOG_SOURCE_WATCH_COMPONENT",
    "CATALOG_SOURCE_WATCH_SOURCES",
    "CatalogSourceWatcher",
]
