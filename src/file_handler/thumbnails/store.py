"""SQLite-backed bookmark thumbnail cache and durable work queue."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional
from urllib.parse import urlsplit

from .urls import canonicalize_url, provider_name_for_url


DEFAULT_DATA_DIR = os.path.abspath("data")
DEFAULT_CACHE_DIR = os.path.join(DEFAULT_DATA_DIR, "thumbnails")
DEFAULT_CACHE_DB = os.path.join(DEFAULT_DATA_DIR, "cache.db")
THUMBNAIL_ROUTE_PREFIX = "/api/bookmark-thumbnails"

PRIORITY_VISIBLE = 0
PRIORITY_BOOKMARK_CHANGE = 1
PRIORITY_MISSING = 2
PRIORITY_STALE = 3


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: Optional[datetime] = None) -> str:
    return (value or _utcnow()).replace(microsecond=0).isoformat()


def _parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def compute_media_id(source: str, identifier: str) -> str:
    base = f"{source}|{identifier}".encode("utf-8", "ignore")
    return hashlib.sha1(base).hexdigest()


@dataclass(frozen=True)
class ThumbnailLookup:
    media_id: str
    route: Optional[str]
    status: str
    provider: str
    sub_type: Optional[str]


class ThumbnailStore:
    """Own migrations, cache metadata, queue claims, and retry state."""

    def __init__(self, db_path: str = DEFAULT_CACHE_DB, cache_dir: str = DEFAULT_CACHE_DIR):
        self.db_path = os.path.abspath(db_path)
        self.cache_dir = os.path.abspath(cache_dir)
        self._setup_lock = threading.Lock()
        self._initialized = False
        self.ensure_setup()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @staticmethod
    def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}

    @classmethod
    def _add_column(cls, conn: sqlite3.Connection, table: str, definition: str) -> None:
        column = definition.split()[0]
        if column not in cls._columns(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")

    def ensure_setup(self) -> None:
        if self._initialized:
            return
        with self._setup_lock:
            if self._initialized:
                return
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            os.makedirs(self.cache_dir, exist_ok=True)
            conn = sqlite3.connect(self.db_path, timeout=30)
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS media_items (
                        id TEXT PRIMARY KEY,
                        source TEXT NOT NULL,
                        original_url TEXT,
                        title TEXT,
                        media_type TEXT,
                        sub_type TEXT,
                        metadata TEXT,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS thumbnails (
                        media_id TEXT PRIMARY KEY REFERENCES media_items(id) ON DELETE CASCADE,
                        local_path TEXT NOT NULL,
                        fetched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        source TEXT
                    )
                    """
                )
                self._add_column(conn, "media_items", "canonical_url TEXT")
                self._add_column(conn, "media_items", "thumbnail_status TEXT NOT NULL DEFAULT 'missing'")
                self._add_column(conn, "media_items", "last_error TEXT")
                self._add_column(conn, "thumbnails", "source_url TEXT")
                self._add_column(conn, "thumbnails", "width INTEGER")
                self._add_column(conn, "thumbnails", "height INTEGER")
                self._add_column(conn, "thumbnails", "content_type TEXT")
                self._add_column(conn, "thumbnails", "expires_at TEXT")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS thumbnail_jobs (
                        media_id TEXT PRIMARY KEY REFERENCES media_items(id) ON DELETE CASCADE,
                        url TEXT NOT NULL,
                        domain TEXT NOT NULL,
                        priority INTEGER NOT NULL DEFAULT 2,
                        status TEXT NOT NULL DEFAULT 'queued',
                        attempt_count INTEGER NOT NULL DEFAULT 0,
                        next_attempt_at TEXT NOT NULL,
                        lease_owner TEXT,
                        lease_expires_at TEXT,
                        last_error TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS runtime_leases (
                        name TEXT PRIMARY KEY,
                        owner TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS thumbnail_state (
                        key TEXT PRIMARY KEY,
                        value TEXT,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_media_items_source ON media_items(source)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_media_items_type ON media_items(media_type)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_media_items_url ON media_items(original_url)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_media_items_canonical_url ON media_items(canonical_url)")
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_thumbnail_jobs_ready "
                    "ON thumbnail_jobs(status, next_attempt_at, priority)"
                )
                conn.execute(
                    """
                    UPDATE media_items
                    SET thumbnail_status='ready'
                    WHERE id IN (SELECT media_id FROM thumbnails)
                      AND (thumbnail_status IS NULL OR thumbnail_status='missing')
                    """
                )
                conn.commit()
            finally:
                conn.close()
            self._initialized = True

    @staticmethod
    def thumbnail_route(media_id: str) -> str:
        return f"{THUMBNAIL_ROUTE_PREFIX}/{media_id}/image"

    def _select_media_id(self, conn: sqlite3.Connection, original_url: str, canonical_url: str) -> tuple[str, bool]:
        legacy_id = compute_media_id("bookmark", original_url)
        row = conn.execute("SELECT id FROM media_items WHERE id=?", (legacy_id,)).fetchone()
        if row:
            return str(row["id"]), False
        row = conn.execute(
            "SELECT id FROM media_items WHERE source='bookmark' AND canonical_url=? ORDER BY updated_at DESC LIMIT 1",
            (canonical_url,),
        ).fetchone()
        if row:
            return str(row["id"]), False
        return compute_media_id("bookmark", canonical_url), True

    def register_bookmark(
        self,
        url: str,
        title: str,
        metadata: Optional[dict] = None,
        *,
        priority: int = PRIORITY_VISIBLE,
        enqueue_missing: bool = True,
        only_if_new: bool = False,
    ) -> ThumbnailLookup:
        canonical_url = canonicalize_url(url)
        provider = provider_name_for_url(canonical_url)
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
        now = _timestamp()

        with self.connect() as conn:
            media_id, is_new = self._select_media_id(conn, url, canonical_url)
            existing = conn.execute(
                "SELECT thumbnail_status, sub_type FROM media_items WHERE id=?",
                (media_id,),
            ).fetchone()
            status = str(existing["thumbnail_status"] or "missing") if existing else "missing"
            conn.execute(
                """
                INSERT INTO media_items (
                    id, source, original_url, canonical_url, title, media_type, sub_type,
                    metadata, thumbnail_status, updated_at
                ) VALUES (?, 'bookmark', ?, ?, ?, 'bookmark', ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    original_url=excluded.original_url,
                    canonical_url=excluded.canonical_url,
                    title=excluded.title,
                    sub_type=excluded.sub_type,
                    metadata=excluded.metadata,
                    updated_at=excluded.updated_at
                """,
                (media_id, url, canonical_url, title, provider, metadata_json, status, now),
            )
            thumbnail = conn.execute(
                "SELECT local_path, source, expires_at FROM thumbnails WHERE media_id=?",
                (media_id,),
            ).fetchone()
            conn.commit()

        route = None
        thumbnail_provider = provider
        expired = False
        if thumbnail:
            local_path = str(thumbnail["local_path"] or "")
            if local_path and os.path.isfile(local_path):
                route = self.thumbnail_route(media_id)
                thumbnail_provider = str(thumbnail["source"] or provider)
                expiry = _parse_timestamp(thumbnail["expires_at"])
                expired = bool(expiry and expiry <= _utcnow())
                if status in {"missing", "queued", "fetching"}:
                    status = "ready"

        if route and expired:
            status = "stale"
            self.enqueue_job(media_id, priority=PRIORITY_STALE)
        elif not route and enqueue_missing and (is_new or not only_if_new):
            self.enqueue_job(media_id, priority=priority)
            status_row = self.get_status(media_id)
            status = str(status_row.get("status") or "queued")

        return ThumbnailLookup(media_id, route, status, thumbnail_provider, provider)

    def enqueue_job(self, media_id: str, *, priority: int = PRIORITY_MISSING, force: bool = False) -> bool:
        now = _timestamp()
        with self.connect() as conn:
            media = conn.execute(
                "SELECT canonical_url, original_url, thumbnail_status FROM media_items WHERE id=?",
                (media_id,),
            ).fetchone()
            if not media:
                return False
            url = str(media["canonical_url"] or media["original_url"] or "")
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                conn.execute(
                    "UPDATE media_items SET thumbnail_status='failed', last_error=? WHERE id=?",
                    ("Only public HTTP(S) bookmarks can have thumbnails", media_id),
                )
                conn.commit()
                return False

            existing = conn.execute("SELECT * FROM thumbnail_jobs WHERE media_id=?", (media_id,)).fetchone()
            if existing and not force:
                status = str(existing["status"])
                if status == "paused":
                    return False
                conn.execute(
                    "UPDATE thumbnail_jobs SET priority=MIN(priority, ?), updated_at=? WHERE media_id=?",
                    (priority, now, media_id),
                )
                if status in {"queued", "retry", "processing"}:
                    conn.commit()
                    return False

            domain = parsed.hostname.lower().rstrip(".")
            attempt_count = 0 if force or not existing else int(existing["attempt_count"] or 0)
            created_at = str(existing["created_at"]) if existing else now
            conn.execute(
                """
                INSERT INTO thumbnail_jobs (
                    media_id, url, domain, priority, status, attempt_count,
                    next_attempt_at, lease_owner, lease_expires_at, last_error,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'queued', ?, ?, NULL, NULL, NULL, ?, ?)
                ON CONFLICT(media_id) DO UPDATE SET
                    url=excluded.url,
                    domain=excluded.domain,
                    priority=excluded.priority,
                    status='queued',
                    attempt_count=excluded.attempt_count,
                    next_attempt_at=excluded.next_attempt_at,
                    lease_owner=NULL,
                    lease_expires_at=NULL,
                    last_error=NULL,
                    updated_at=excluded.updated_at
                """,
                (media_id, url, domain, priority, attempt_count, now, created_at, now),
            )
            conn.execute(
                "UPDATE media_items SET thumbnail_status='queued', last_error=NULL WHERE id=?",
                (media_id,),
            )
            conn.commit()
        return True

    def claim_jobs(
        self,
        owner: str,
        limit: int,
        excluded_domains: Optional[set[str]] = None,
        lease_seconds: int = 90,
        domain_filter: Optional[str] = None,
    ) -> list[dict]:
        if limit <= 0:
            return []
        excluded = excluded_domains or set()
        now = _utcnow()
        now_text = _timestamp(now)
        lease_until = _timestamp(now + timedelta(seconds=lease_seconds))
        normalized_filter = (domain_filter or "").lower().strip().lstrip(".")
        claimed: list[dict] = []

        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT * FROM thumbnail_jobs
                WHERE (
                    (status IN ('queued', 'retry', 'paused') AND next_attempt_at <= ?)
                    OR (status='processing' AND lease_expires_at <= ?)
                )
                ORDER BY priority ASC, next_attempt_at ASC, created_at ASC
                LIMIT 100
                """,
                (now_text, now_text),
            ).fetchall()
            chosen_domains = set(excluded)
            for row in rows:
                domain = str(row["domain"])
                if normalized_filter and domain != normalized_filter and not domain.endswith(f".{normalized_filter}"):
                    continue
                if domain in chosen_domains:
                    continue
                cursor = conn.execute(
                    """
                    UPDATE thumbnail_jobs
                    SET status='processing', lease_owner=?, lease_expires_at=?, updated_at=?
                    WHERE media_id=? AND (
                        (status IN ('queued', 'retry', 'paused') AND next_attempt_at <= ?)
                        OR (status='processing' AND lease_expires_at <= ?)
                    )
                    """,
                    (owner, lease_until, now_text, row["media_id"], now_text, now_text),
                )
                if cursor.rowcount:
                    conn.execute(
                        "UPDATE media_items SET thumbnail_status='fetching' WHERE id=?",
                        (row["media_id"],),
                    )
                    item = dict(row)
                    item["status"] = "processing"
                    claimed.append(item)
                    chosen_domains.add(domain)
                if len(claimed) >= limit:
                    break
            conn.commit()
        finally:
            conn.close()
        return claimed

    def record_thumbnail(
        self,
        media_id: str,
        local_path: str,
        *,
        provider: str,
        source_url: Optional[str],
        width: int,
        height: int,
        content_type: str = "image/webp",
        ttl_days: int = 90,
        status: str = "ready",
    ) -> None:
        now = _utcnow()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO thumbnails (
                    media_id, local_path, fetched_at, source, source_url,
                    width, height, content_type, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(media_id) DO UPDATE SET
                    local_path=excluded.local_path,
                    fetched_at=excluded.fetched_at,
                    source=excluded.source,
                    source_url=excluded.source_url,
                    width=excluded.width,
                    height=excluded.height,
                    content_type=excluded.content_type,
                    expires_at=excluded.expires_at
                """,
                (
                    media_id,
                    os.path.abspath(local_path),
                    _timestamp(now),
                    provider,
                    source_url,
                    width,
                    height,
                    content_type,
                    _timestamp(now + timedelta(days=ttl_days)),
                ),
            )
            conn.execute(
                "UPDATE media_items SET thumbnail_status=?, last_error=NULL, updated_at=? WHERE id=?",
                (status, _timestamp(now), media_id),
            )
            if status == "ready":
                conn.execute(
                    """
                    UPDATE thumbnail_jobs
                    SET status='complete', lease_owner=NULL, lease_expires_at=NULL,
                        last_error=NULL, updated_at=?
                    WHERE media_id=?
                    """,
                    (_timestamp(now), media_id),
                )
            conn.commit()

    def mark_job_failure(self, media_id: str, error: str) -> str:
        now = _utcnow()
        safe_error = (error or "Thumbnail provider returned no usable image")[:1000]
        with self.connect() as conn:
            job = conn.execute(
                "SELECT attempt_count FROM thumbnail_jobs WHERE media_id=?",
                (media_id,),
            ).fetchone()
            attempts = int(job["attempt_count"] or 0) + 1 if job else 1
            if attempts >= 4:
                job_status = "paused"
                media_status = "failed"
                delay = timedelta(days=7)
            else:
                job_status = "retry"
                media_status = "retry"
                delay = (timedelta(minutes=30), timedelta(hours=6), timedelta(hours=24))[attempts - 1]
            next_attempt = _timestamp(now + delay)
            conn.execute(
                """
                UPDATE thumbnail_jobs
                SET status=?, attempt_count=?, next_attempt_at=?, lease_owner=NULL,
                    lease_expires_at=NULL, last_error=?, updated_at=?
                WHERE media_id=?
                """,
                (job_status, attempts, next_attempt, safe_error, _timestamp(now), media_id),
            )
            conn.execute(
                "UPDATE media_items SET thumbnail_status=?, last_error=?, updated_at=? WHERE id=?",
                (media_status, safe_error, _timestamp(now), media_id),
            )
            conn.commit()
        return job_status

    def get_thumbnail_path(self, media_id: str) -> Optional[str]:
        with self.connect() as conn:
            row = conn.execute("SELECT local_path FROM thumbnails WHERE media_id=?", (media_id,)).fetchone()
        if not row:
            return None
        path = str(row["local_path"] or "")
        return path if path and os.path.isfile(path) else None

    def get_status(self, media_id: str) -> dict:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT m.id, m.thumbnail_status, m.sub_type, m.last_error,
                       t.local_path, t.source
                FROM media_items m
                LEFT JOIN thumbnails t ON t.media_id=m.id
                WHERE m.id=?
                """,
                (media_id,),
            ).fetchone()
        if not row:
            return {"id": media_id, "status": "unknown", "thumbnail_url": None}
        local_path = str(row["local_path"] or "")
        has_thumbnail = bool(local_path and os.path.isfile(local_path))
        return {
            "id": media_id,
            "status": str(row["thumbnail_status"] or "missing"),
            "provider": str(row["source"] or row["sub_type"] or "metadata"),
            "thumbnail_url": self.thumbnail_route(media_id) if has_thumbnail else None,
            "error": row["last_error"],
        }

    def get_statuses(self, media_ids: Iterable[str]) -> list[dict]:
        unique_ids = list(dict.fromkeys(str(value) for value in media_ids if value))[:200]
        return [self.get_status(media_id) for media_id in unique_ids]

    def sync_missing(self, *, force: bool = False, domain: Optional[str] = None, limit: Optional[int] = None) -> dict:
        normalized_domain = (domain or "").lower().strip().lstrip(".")
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT m.id, m.canonical_url, m.original_url, t.local_path
                FROM media_items m
                LEFT JOIN thumbnails t ON t.media_id=m.id
                WHERE m.source='bookmark'
                ORDER BY m.updated_at DESC
                """
            ).fetchall()
        queued = 0
        considered = 0
        for row in rows:
            url = str(row["canonical_url"] or row["original_url"] or "")
            host = (urlsplit(url).hostname or "").lower()
            if normalized_domain and host != normalized_domain and not host.endswith(f".{normalized_domain}"):
                continue
            local_path = str(row["local_path"] or "")
            if not force and local_path and os.path.isfile(local_path):
                continue
            considered += 1
            if self.enqueue_job(str(row["id"]), priority=PRIORITY_MISSING, force=force):
                queued += 1
            if limit is not None and considered >= limit:
                break
        return {"considered": considered, "queued": queued, "force": force, "domain": normalized_domain or None}

    def acquire_runtime_lease(self, name: str, owner: str, lease_seconds: int = 90) -> bool:
        now = _utcnow()
        now_text = _timestamp(now)
        expiry = _timestamp(now + timedelta(seconds=lease_seconds))
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute("SELECT owner, expires_at FROM runtime_leases WHERE name=?", (name,)).fetchone()
            if current and current["owner"] != owner:
                current_expiry = _parse_timestamp(current["expires_at"])
                current_pid = str(current["owner"]).split(":", 1)[0]
                process_alive = True
                try:
                    os.kill(int(current_pid), 0)
                except (OSError, ValueError):
                    process_alive = False
                if process_alive and current_expiry and current_expiry > now:
                    conn.rollback()
                    return False
            conn.execute(
                """
                INSERT INTO runtime_leases(name, owner, expires_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    owner=excluded.owner,
                    expires_at=excluded.expires_at,
                    updated_at=excluded.updated_at
                """,
                (name, owner, expiry, now_text),
            )
            conn.commit()
            return True
        finally:
            conn.close()

    def get_state(self, key: str) -> Optional[str]:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM thumbnail_state WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row and row["value"] is not None else None

    def set_state(self, key: str, value: str) -> None:
        now = _timestamp()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO thumbnail_state(key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (key, value, now),
            )
            conn.commit()


_DEFAULT_STORE: Optional[ThumbnailStore] = None
_DEFAULT_STORE_LOCK = threading.Lock()


def get_thumbnail_store() -> ThumbnailStore:
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        with _DEFAULT_STORE_LOCK:
            if _DEFAULT_STORE is None:
                _DEFAULT_STORE = ThumbnailStore()
    return _DEFAULT_STORE


__all__ = [
    "DEFAULT_CACHE_DB",
    "DEFAULT_CACHE_DIR",
    "PRIORITY_BOOKMARK_CHANGE",
    "PRIORITY_MISSING",
    "PRIORITY_STALE",
    "PRIORITY_VISIBLE",
    "ThumbnailLookup",
    "ThumbnailStore",
    "compute_media_id",
    "get_thumbnail_store",
]
