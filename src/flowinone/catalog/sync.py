"""Project source-owned records into the shared Catalog."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence
from urllib.parse import quote

from sqlalchemy import text

from src.flowinone import config
from src.file_handler.chrome_bookmarks import iter_chrome_bookmark_records
from src.file_handler.item_db import fetch_items
from src.file_handler.media_cache import lookup_thumbnail_for_bookmark
from src.flowinone.resource_library.canonical import normalize_resource_url
from src.flowinone.resource_library.database import ResourceDatabase
from src.flowinone.resource_library.models import new_id, utc_now_text

from .browse import CatalogService
from .eagle_sync import EagleSyncMixin
from .locking import database_is_locked
from .query import CATALOG_SOURCES, _normalize_tag

LOGGER = logging.getLogger(__name__)
CATALOG_LOCK_MAX_RETRIES = 3
CATALOG_LOCK_RETRY_BASE_DELAY = 0.1
CATALOG_LOCK_RETRY_MAX_DELAY = 1.0


class CatalogSyncService(EagleSyncMixin):
    """Incrementally project source-owned records into the shared Catalog."""

    def __init__(
        self,
        database: ResourceDatabase,
        *,
        lock_max_retries: int = CATALOG_LOCK_MAX_RETRIES,
        lock_retry_base_delay: float = CATALOG_LOCK_RETRY_BASE_DELAY,
        sleep: Callable[[float], None] | None = None,
    ):
        self.database = database
        self.lock_max_retries = max(0, int(lock_max_retries))
        self.lock_retry_base_delay = max(0.0, float(lock_retry_base_delay))
        self._sleep = sleep or time.sleep

    @staticmethod
    def _url_identity(url: str) -> tuple[str, str]:
        canonical = normalize_resource_url(url)
        return f"url:{hashlib.sha256(canonical.encode()).hexdigest()}", canonical

    def _upsert(
        self,
        conn,
        *,
        identity_key: str,
        source_kind: str,
        source_key: str,
        item_type: str,
        title: str,
        description: str = "",
        thumbnail_ref: str = "",
        detail_uri: str = "",
        original_url: str = "",
        source_path: str = "",
        tags: Sequence[tuple[str, str]] = (),
        captured_at: str = "",
        source_updated_at: str = "",
        duration_seconds: float | None = None,
        metadata: dict[str, Any] | None = None,
        extracted_text: str = "",
        content_fingerprint: str = "",
        prefer: bool = False,
        seen_at: str = "",
    ) -> str:
        now = utc_now_text()
        seen_at = seen_at or now
        item_id = conn.execute(
            text("SELECT id FROM catalog_items WHERE identity_key=:key"), {"key": identity_key}
        ).scalar_one_or_none()
        if item_id is None:
            item_id = new_id()
            conn.execute(
                text(
                    """
                    INSERT INTO catalog_items(
                        id, identity_key, item_type, title, description, thumbnail_ref,
                        primary_detail_uri, original_url, duration_seconds, captured_at,
                        source_updated_at, indexed_at, availability, metadata_json,
                        content_fingerprint, is_deleted
                    ) VALUES (
                        :id, :identity_key, :item_type, :title, :description, :thumbnail,
                        :detail_uri, :original_url, :duration, :captured_at,
                        :updated_at, :indexed_at, 'available', :metadata,
                        :fingerprint, 0
                    )
                    """
                ),
                {
                    "id": item_id,
                    "identity_key": identity_key,
                    "item_type": item_type or "unknown",
                    "title": title or "Untitled",
                    "description": description or None,
                    "thumbnail": thumbnail_ref or None,
                    "detail_uri": detail_uri or None,
                    "original_url": original_url or None,
                    "duration": duration_seconds,
                    "captured_at": captured_at or now,
                    "updated_at": source_updated_at or now,
                    "indexed_at": now,
                    "metadata": json.dumps(metadata or {}, ensure_ascii=False),
                    "fingerprint": content_fingerprint or None,
                },
            )
        else:
            conn.execute(
                text(
                    """
                    UPDATE catalog_items SET
                        title=CASE WHEN :prefer=1 OR title='' THEN :title ELSE title END,
                        item_type=CASE WHEN :prefer=1 THEN :item_type ELSE item_type END,
                        description=CASE WHEN :prefer=1 THEN NULLIF(:description,'') ELSE COALESCE(NULLIF(:description,''),description) END,
                        thumbnail_ref=COALESCE(NULLIF(:thumbnail,''), thumbnail_ref),
                        primary_detail_uri=CASE WHEN :prefer=1 THEN COALESCE(NULLIF(:detail_uri,''),primary_detail_uri) ELSE COALESCE(primary_detail_uri,NULLIF(:detail_uri,'')) END,
                        original_url=CASE WHEN :prefer=1 THEN COALESCE(NULLIF(:original_url,''),original_url) ELSE COALESCE(original_url,NULLIF(:original_url,'')) END,
                        duration_seconds=COALESCE(:duration,duration_seconds),
                        captured_at=COALESCE(NULLIF(:captured_at,''),captured_at),
                        source_updated_at=COALESCE(NULLIF(:source_updated_at,''),source_updated_at),
                        indexed_at=:indexed_at, availability='available', is_deleted=0,
                        metadata_json=CASE WHEN :prefer=1 THEN :metadata ELSE metadata_json END,
                        content_fingerprint=COALESCE(NULLIF(:fingerprint,''),content_fingerprint)
                    WHERE id=:id
                    """
                ),
                {
                    "id": item_id,
                    "prefer": 1 if prefer else 0,
                    "title": title or "Untitled",
                    "item_type": item_type or "unknown",
                    "description": description,
                    "thumbnail": thumbnail_ref,
                    "detail_uri": detail_uri,
                    "original_url": original_url,
                    "duration": duration_seconds,
                    "captured_at": captured_at,
                    "source_updated_at": source_updated_at,
                    "indexed_at": now,
                    "metadata": json.dumps(metadata or {}, ensure_ascii=False),
                    "fingerprint": content_fingerprint,
                },
            )
        conn.execute(
            text(
                """
                INSERT INTO catalog_origins(
                    id,catalog_item_id,source_kind,source_key,detail_uri,original_url,
                    source_path,metadata_json,last_seen_at,stale
                ) VALUES (:id,:item,:source,:key,:detail,:url,:path,:metadata,:seen,0)
                ON CONFLICT(source_kind,source_key) DO UPDATE SET
                    catalog_item_id=excluded.catalog_item_id, detail_uri=excluded.detail_uri,
                    original_url=excluded.original_url, source_path=excluded.source_path,
                    metadata_json=excluded.metadata_json,last_seen_at=excluded.last_seen_at,stale=0
                """
            ),
            {
                "id": new_id(), "item": item_id, "source": source_kind, "key": source_key,
                "detail": detail_uri or None, "url": original_url or None,
                "path": source_path or None, "metadata": json.dumps(metadata or {}, ensure_ascii=False),
                "seen": seen_at,
            },
        )
        for raw_tag, tag_source in tags:
            normalized = _normalize_tag(raw_tag)
            if not normalized:
                continue
            tag_id = conn.execute(
                text("SELECT id FROM catalog_tags WHERE normalized_name=:name"), {"name": normalized}
            ).scalar_one_or_none()
            if tag_id is None:
                tag_id = new_id()
                conn.execute(
                    text("INSERT INTO catalog_tags(id,name,normalized_name,created_at) VALUES(:id,:name,:normalized,:created)"),
                    {"id": tag_id, "name": str(raw_tag).strip()[:160], "normalized": normalized, "created": now},
                )
            conn.execute(
                text(
                    "INSERT OR IGNORE INTO catalog_item_tags(catalog_item_id,tag_id,source,created_at) VALUES(:item,:tag,:source,:created)"
                ),
                {"item": item_id, "tag": tag_id, "source": tag_source[:24], "created": now},
            )
        tag_text = " ".join(
            conn.execute(
                text("SELECT t.name FROM catalog_tags t JOIN catalog_item_tags it ON it.tag_id=t.id WHERE it.catalog_item_id=:id"),
                {"id": item_id},
            ).scalars()
        )
        existing_fts = conn.execute(text("SELECT extracted_text FROM catalog_fts WHERE catalog_item_id=:id"), {"id": item_id}).scalar_one_or_none() or ""
        stored = conn.execute(text("SELECT title,description FROM catalog_items WHERE id=:id"), {"id": item_id}).mappings().one()
        merged_text = extracted_text or existing_fts
        if extracted_text and existing_fts and extracted_text not in existing_fts:
            merged_text = f"{existing_fts}\n{extracted_text}"
        conn.execute(text("DELETE FROM catalog_fts WHERE catalog_item_id=:id"), {"id": item_id})
        conn.execute(
            text("INSERT INTO catalog_fts(catalog_item_id,title,description,tags,extracted_text) VALUES(:id,:title,:description,:tags,:text)"),
            {"id": item_id, "title": stored["title"] or "", "description": stored["description"] or "", "tags": tag_text, "text": merged_text[:500_000]},
        )
        return item_id

    def _sync_resources(self, conn, resource_id: str | None = None) -> int:
        where = "WHERE r.id=:resource_id" if resource_id else ""
        rows = conn.execute(
            text(
                f"""
                SELECT r.*, GROUP_CONCAT(DISTINCT t.name) AS tag_names,
                       (SELECT rc.content_path FROM resource_contents rc
                        WHERE rc.resource_id=r.id AND rc.content_type IN ('article_text','pdf_text','video_transcript','social_post_text')
                        ORDER BY rc.created_at DESC LIMIT 1) AS latest_content_path
                FROM resources r
                LEFT JOIN resource_tags rt ON rt.resource_id=r.id
                LEFT JOIN tags t ON t.id=rt.tag_id
                {where}
                GROUP BY r.id
                """
            ),
            {"resource_id": resource_id} if resource_id else {},
        ).mappings()
        count = 0
        for row in rows:
            identity, canonical = self._url_identity(row["canonical_url"])
            tags = [(tag, "resource") for tag in str(row["tag_names"] or "").split(",") if tag]
            extracted = ""
            if row["latest_content_path"]:
                try:
                    extracted = Path(str(row["latest_content_path"])).read_text(encoding="utf-8")[:500_000]
                except OSError:
                    extracted = ""
            self._upsert(
                conn, identity_key=identity, source_kind="resources", source_key=row["id"],
                item_type=row["source_type"], title=row["title"] or canonical,
                description=row["summary_one_line"] or row["description"] or "",
                detail_uri=f"/resources/{row['id']}/", original_url=canonical,
                tags=tags, captured_at=row["captured_at"],
                metadata={},
                extracted_text="\n".join(filter(None, (row["summary_short"], row["why_this_matters"], extracted))),
                content_fingerprint=row["content_hash"] or "", prefer=True,
            )
            count += 1
        return count

    def sync_resource(self, resource_id: str) -> int:
        with self.database.write_transaction() as conn:
            count = self._sync_resources(conn, resource_id)
        CatalogService.mark_browse_data_changed(self.database)
        return count

    def _sync_bookmarks(self, conn) -> int:
        count = 0
        for record in iter_chrome_bookmark_records():
            url = str(record.get("url") or "")
            if not url:
                continue
            try:
                identity, canonical = self._url_identity(url)
            except ValueError:
                # Chrome can contain chrome://, javascript:, extension, or local
                # entries. They remain in Chrome but are not safe Catalog links.
                continue
            folder = str(record.get("folder_path") or "")
            tags = [(part, "folder") for part in re.split(r"\s*/\s*|\s+\/\s+", folder) if part]
            thumbnail = lookup_thumbnail_for_bookmark(url, str(record.get("title") or canonical), {"folder_path": folder})
            self._upsert(
                conn, identity_key=identity, source_kind="bookmarks", source_key=f"{url}\n{folder}",
                item_type="bookmark", title=str(record.get("title") or canonical),
                description=folder, thumbnail_ref=str(thumbnail.route or ""), original_url=canonical, detail_uri=canonical,
                tags=tags, captured_at=str(record.get("date_added") or ""), metadata={"folder_path": folder, "thumbnail_ref": thumbnail.route or "", "thumbnail_sub_type": thumbnail.sub_type},
            )
            count += 1
        return count

    def _sync_local(self, conn) -> int:
        offset = count = 0
        internal_root = Path(config.DB_route_internal).expanduser().resolve() if config.DB_route_internal else None
        external_root = Path(config.DB_route_external).expanduser().resolve() if config.DB_route_external else None
        while offset < 100_000:
            payload = fetch_items(limit=1000, offset=offset)
            rows = payload.get("items") or []
            if not rows:
                break
            for row in rows:
                if row.get("item_type") not in {"image", "video"}:
                    continue
                relative = str(row.get("relative_path") or "")
                item_path = Path(str(row.get("absolute_path") or row.get("library_root") or "")).expanduser().resolve()
                source = "internal" if internal_root and internal_root != external_root and item_path.is_relative_to(internal_root) else "external"
                detail = f"/{row['item_type']}/{quote(relative, safe='/')}?src={source}"
                fingerprint = str(row.get("content_fingerprint") or "")
                portable_uid = str(row.get("portable_uid") or "")
                metadata_source = "sidecar" if row.get("sidecar_updated_at") else "folder"
                self._upsert(
                    conn, identity_key=f"local:{portable_uid or fingerprint or row['item_id']}", source_kind="local",
                    source_key=str(row["item_id"]), item_type=str(row["item_type"]),
                    title=str(row.get("name") or "Untitled"), thumbnail_ref=str(row.get("thumbnail_route") or ""),
                    detail_uri=detail, source_path=relative,
                    tags=[(tag, metadata_source) for tag in row.get("tags") or []], captured_at=str(row.get("updated_at") or ""),
                    metadata={"ext": row.get("ext"), "size_bytes": row.get("size_bytes"), "portable_uid": portable_uid or None, "metadata_provenance": metadata_source}, content_fingerprint=fingerprint,
                )
                count += 1
            offset += len(rows)
            if offset >= int(payload.get("total") or 0):
                break
        return count

    _database_is_locked = staticmethod(database_is_locked)

    def _retry_delay(self, retry_number: int) -> float:
        return min(
            self.lock_retry_base_delay * (2 ** max(0, retry_number - 1)),
            CATALOG_LOCK_RETRY_MAX_DELAY,
        )

    def _publish_status(self, source: str, status: str, **details: Any) -> None:
        self.database.set_catalog_sync_status(
            source,
            status=status,
            **details,
        )

    def _record_failure(
        self,
        source: str,
        previous_count: int,
        error: Exception,
        now: str,
    ) -> bool:
        """Persist a non-lock source error without ever replacing that error."""
        for attempt in range(1, self.lock_max_retries + 2):
            try:
                with self.database.write_transaction() as conn:
                    conn.execute(
                        text(
                            "INSERT INTO catalog_sync_state(source_kind,status,item_count,error_message,synced_at) "
                            "VALUES(:source,'failed',:count,:error,:now) "
                            "ON CONFLICT(source_kind) DO UPDATE SET status='failed',item_count=:count,error_message=:error,synced_at=:now"
                        ),
                        {
                            "source": source,
                            "count": previous_count,
                            "error": str(error)[:2000],
                            "now": now,
                        },
                    )
                return True
            except Exception as record_exc:
                can_retry = (
                    self._database_is_locked(record_exc)
                    and attempt <= self.lock_max_retries
                )
                if can_retry:
                    self._sleep(self._retry_delay(attempt))
                    continue
                LOGGER.warning(
                    "Unable to record failed Catalog sync for %s",
                    source,
                    exc_info=True,
                )
                return False
        return False

    def _sync_selected(
        self,
        selected: Sequence[str],
        *,
        full_rescan: bool = False,
    ) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        loaders = {
            "resources": self._sync_resources,
            "bookmarks": self._sync_bookmarks,
            "local": self._sync_local,
        }
        for source in selected:
            max_attempts = self.lock_max_retries + 1
            previous_count = 0
            force_eagle_full = full_rescan
            self._publish_status(
                source,
                "syncing",
                attempt=1,
                max_attempts=max_attempts,
                retry_count=0,
                error=None,
            )
            for attempt in range(1, max_attempts + 1):
                try:
                    with self.database.engine.connect() as conn:
                        previous_count = int(
                            conn.execute(
                                text(
                                    "SELECT item_count FROM catalog_sync_state "
                                    "WHERE source_kind=:source"
                                ),
                                {"source": source},
                            ).scalar()
                            or 0
                        )
                    eagle_details: dict[str, Any] = {}
                    if source == "eagle":
                        eagle_details = self._sync_eagle(
                            full_rescan=force_eagle_full,
                            attempt=attempt,
                            max_attempts=max_attempts,
                        )
                        count = int(eagle_details["count"])
                        now = str(eagle_details["synced_at"])
                    else:
                        now = utc_now_text()
                        with self.database.write_transaction() as conn:
                            conn.execute(text("UPDATE catalog_origins SET stale=1 WHERE source_kind=:source"), {"source": source})
                            count = loaders[source](conn)
                            conn.execute(
                                text("INSERT INTO catalog_sync_state(source_kind,status,item_count,synced_at) VALUES(:source,'complete',:count,:now) ON CONFLICT(source_kind) DO UPDATE SET status='complete',item_count=:count,error_message=NULL,synced_at=:now"),
                                {"source": source, "count": count, "now": now},
                            )
                    completed = {
                        "status": "complete",
                        "count": count,
                        "item_count": count,
                        "attempts": attempt,
                        "max_attempts": max_attempts,
                        "retry_count": attempt - 1,
                        "error": None,
                        "synced_at": now,
                        **{
                            key: value
                            for key, value in eagle_details.items()
                            if key not in {"count", "synced_at"}
                        },
                    }
                    self._publish_status(source, **completed)
                    result[source] = {
                        key: value
                        for key, value in completed.items()
                        if key != "item_count"
                    }
                    break
                except Exception as exc:
                    locked = self._database_is_locked(exc)
                    if locked and attempt < max_attempts:
                        retry_number = attempt
                        delay = self._retry_delay(retry_number)
                        progress = self.database.catalog_sync_status().get(source, {})
                        if source == "eagle" and force_eagle_full:
                            force_eagle_full = not bool(progress.get("full_rescan"))
                        self._publish_status(
                            source,
                            "retrying",
                            attempt=attempt,
                            next_attempt=attempt + 1,
                            max_attempts=max_attempts,
                            retry_count=retry_number,
                            retry_in_seconds=delay,
                            error=str(exc),
                            **{
                                key: progress[key]
                                for key in (
                                    "processed",
                                    "total",
                                    "changed",
                                    "skipped",
                                    "resumed",
                                    "full_rescan",
                                )
                                if key in progress
                            },
                        )
                        self._sleep(delay)
                        continue

                    now = utc_now_text()
                    recorded = False
                    if not locked:
                        recorded = self._record_failure(
                            source,
                            previous_count,
                            exc,
                            now,
                        )
                    progress = self.database.catalog_sync_status().get(source, {})
                    failed = {
                        "status": "failed",
                        "attempts": attempt,
                        "max_attempts": max_attempts,
                        "retry_count": attempt - 1,
                        "error": str(exc),
                        "locked": locked,
                        "recorded": recorded,
                        "synced_at": now,
                        **{
                            key: progress[key]
                            for key in (
                                "processed",
                                "total",
                                "changed",
                                "skipped",
                                "resumed",
                                "full_rescan",
                            )
                            if key in progress
                        },
                    }
                    self._publish_status(source, **failed)
                    result[source] = failed
                    break
        return result

    def sync(
        self,
        sources: Iterable[str] = CATALOG_SOURCES,
        *,
        full_rescan: bool = False,
    ) -> dict[str, dict[str, Any]]:
        """Synchronize sources without allowing overlapping full projections."""
        selected = tuple(source for source in sources if source in CATALOG_SOURCES)
        with self.database.catalog_sync():
            result = self._sync_selected(selected, full_rescan=full_rescan)
        if self._browse_data_may_have_changed(result):
            # A failed incremental source can already have committed a page, so
            # invalidate non-lock failures as well as fully successful results.
            # Do not attempt another write after a final SQLite lock failure.
            CatalogService.mark_browse_data_changed(self.database)
        return result

    def sync_if_empty(self, sources: Iterable[str] = CATALOG_SOURCES) -> dict[str, dict[str, Any]]:
        """Run the initial projection once when concurrent pages open together."""
        selected = tuple(source for source in sources if source in CATALOG_SOURCES)
        with self.database.catalog_sync():
            with self.database.engine.connect() as conn:
                populated = bool(
                    conn.execute(
                        text("SELECT 1 FROM catalog_items WHERE is_deleted=0 LIMIT 1")
                    ).first()
                )
            if populated:
                return {}
            result = self._sync_selected(selected)
        if self._browse_data_may_have_changed(result):
            CatalogService.mark_browse_data_changed(self.database)
        return result

    @staticmethod
    def _browse_data_may_have_changed(result: dict[str, dict[str, Any]]) -> bool:
        return any(
            state.get("status") == "complete" or not state.get("locked", False)
            for state in result.values()
        )
