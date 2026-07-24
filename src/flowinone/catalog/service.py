"""Rebuildable Catalog projection, FTS search, facets, and local signals."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence
from urllib.parse import quote

from sqlalchemy import text

from src.file_handler.chrome_bookmarks import iter_chrome_bookmark_records
from src.file_handler.eagle_integration import (
    get_eagle_catalog_page,
    get_eagle_catalog_source,
)
from src.file_handler.item_db import fetch_items
from src.file_handler.media_cache import lookup_thumbnail_for_bookmark
from src.flowinone.resource_library.canonical import normalize_resource_url
from src.flowinone.resource_library.database import ResourceDatabase
from src.flowinone.resource_library.models import new_id, utc_now_text


LOGGER = logging.getLogger(__name__)

CATALOG_SOURCES = ("local", "eagle", "bookmarks", "resources")
CATALOG_LOCK_MAX_RETRIES = 3
CATALOG_LOCK_RETRY_BASE_DELAY = 0.1
CATALOG_LOCK_RETRY_MAX_DELAY = 1.0
EAGLE_SYNC_CURSOR_VERSION = 1
EAGLE_SYNC_FINGERPRINT_VERSION = 1
EAGLE_SYNC_PAGE_SIZE = 500
CATALOG_SORTS = (
    "newest",
    "recently_added",
    "relevance",
    "title",
    "random",
    "most_viewed",
    "recently_viewed",
    "favorites",
)


def _json(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _normalize_tag(value: str) -> str:
    return " ".join(str(value or "").strip().casefold().split())[:160]


def _fts_query(query: str) -> str:
    tokens = re.findall(r"[\w\u3400-\u9fff-]+", query, flags=re.UNICODE)
    return " AND ".join(f'"{token.replace(chr(34), chr(34) * 2)}"*' for token in tokens[:24])


@dataclass(frozen=True)
class CatalogQuery:
    q: str = ""
    scope: str = "all"
    sources: tuple[str, ...] = CATALOG_SOURCES
    item_type: str = ""
    tags: tuple[str, ...] = ()
    tag_mode: str = "any"
    favorite: bool = False
    unviewed: bool = False
    duration_min: float | None = None
    duration_max: float | None = None
    added_from: str = ""
    added_to: str = ""
    sort: str = "recently_added"
    seed: int = 0
    limit: int = 48
    cursor: str = ""

    @classmethod
    def create(cls, **values: Any) -> "CatalogQuery":
        raw_sources = values.get("sources") or CATALOG_SOURCES
        if isinstance(raw_sources, str):
            raw_sources = raw_sources.split(",")
        sources = tuple(
            dict.fromkeys(
                value.strip().lower()
                for raw in raw_sources
                for value in str(raw).split(",")
                if value.strip().lower() in CATALOG_SOURCES
            )
        ) or CATALOG_SOURCES
        raw_tags = values.get("tags") or ()
        if isinstance(raw_tags, str):
            raw_tags = raw_tags.split(",")
        tags = tuple(
            dict.fromkeys(
                normalized
                for raw_tag in raw_tags
                for tag in str(raw_tag).split(",")
                if (normalized := _normalize_tag(tag))
            )
        )
        sort = str(values.get("sort") or "recently_added").strip().lower()
        try:
            limit = max(1, min(int(values.get("limit") or 48), 100))
        except (TypeError, ValueError):
            limit = 48
        try:
            seed = max(0, int(values.get("seed") or 0))
        except (TypeError, ValueError):
            seed = 0

        def number(name: str) -> float | None:
            try:
                raw = values.get(name)
                return float(raw) if raw not in (None, "") else None
            except (TypeError, ValueError):
                return None

        scope = str(values.get("scope") or "all").strip().lower()
        return cls(
            q=str(values.get("q") or "").strip()[:300],
            scope=scope if scope in {"all", "gallery", "resources"} else "all",
            sources=sources,
            item_type=str(values.get("item_type") or values.get("type") or "").strip().lower()[:40],
            tags=tags,
            tag_mode="all" if str(values.get("tag_mode") or "any").lower() == "all" else "any",
            favorite=bool(values.get("favorite") in (True, 1, "1", "true", "yes")),
            unviewed=bool(values.get("unviewed") in (True, 1, "1", "true", "yes")),
            duration_min=number("duration_min"),
            duration_max=number("duration_max"),
            added_from=str(values.get("added_from") or "")[:40],
            added_to=str(values.get("added_to") or "")[:40],
            sort=sort if sort in CATALOG_SORTS else "recently_added",
            seed=seed,
            limit=limit,
            cursor=str(values.get("cursor") or "")[:1000],
        )

    def signature(self) -> str:
        payload = {**self.public_dict(), "cursor": None, "limit": None}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:20]

    def public_dict(self) -> dict[str, Any]:
        return {
            "q": self.q,
            "scope": self.scope,
            "sources": list(self.sources),
            "type": self.item_type or None,
            "tags": list(self.tags),
            "tag_mode": self.tag_mode,
            "favorite": self.favorite,
            "unviewed": self.unviewed,
            "duration_min": self.duration_min,
            "duration_max": self.duration_max,
            "added_from": self.added_from or None,
            "added_to": self.added_to or None,
            "sort": self.sort,
            "seed": self.seed or None,
            "limit": self.limit,
            "cursor": self.cursor or None,
        }


class CatalogSyncService:
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
            return self._sync_resources(conn, resource_id)

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
                conn, identity_key=identity, source_kind="bookmarks", source_key=url,
                item_type="bookmark", title=str(record.get("title") or canonical),
                description=folder, thumbnail_ref=str(thumbnail.route or ""), original_url=canonical, detail_uri=canonical,
                tags=tags, captured_at=str(record.get("date_added") or ""), metadata={"folder_path": folder, "thumbnail_ref": thumbnail.route or "", "thumbnail_sub_type": thumbnail.sub_type},
            )
            count += 1
        return count

    def _sync_local(self, conn) -> int:
        offset = count = 0
        while offset < 100_000:
            payload = fetch_items(limit=1000, offset=offset)
            rows = payload.get("items") or []
            if not rows:
                break
            for row in rows:
                if row.get("item_type") not in {"image", "video"}:
                    continue
                relative = str(row.get("relative_path") or "")
                detail = f"/{row['item_type']}/{quote(relative, safe='/')}?src=external"
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

    @staticmethod
    def _eagle_source_tokens(source: dict[str, Any]) -> tuple[str, str]:
        identity = str(source.get("identity") or "")
        if not identity:
            raise ValueError("Eagle catalog source identity is missing")
        signature = hashlib.sha256(identity.encode()).hexdigest()
        version = str(source.get("version") or "")
        snapshot = (
            hashlib.sha256(f"{identity}\0{version}".encode()).hexdigest()
            if version
            else ""
        )
        return signature, snapshot

    @staticmethod
    def _eagle_item_fingerprint(row: dict[str, Any]) -> str:
        payload = {
            "id": str(row.get("id") or ""),
            "name": str(row.get("name") or ""),
            "ext": str(row.get("ext") or "").lower(),
            "media_type": str(row.get("media_type") or ""),
            "original_url": str(row.get("original_url") or ""),
            "description": str(row.get("description") or ""),
            "captured_at": str(row.get("captured_at") or ""),
            "modified_at": str(row.get("modified_at") or ""),
            "tags": sorted(str(tag) for tag in row.get("tags") or []),
            "folders": sorted(str(folder) for folder in row.get("folders") or []),
        }
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @classmethod
    def _eagle_page_digest(cls, items: Sequence[dict[str, Any]]) -> str:
        payload = [
            (str(row.get("id") or ""), cls._eagle_item_fingerprint(row))
            for row in items
        ]
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()

    @staticmethod
    def _write_eagle_cursor(
        conn,
        *,
        cursor: dict[str, Any],
        source_signature: str,
        item_count: int,
        synced_at: str,
    ) -> None:
        conn.execute(
            text(
                """
                INSERT INTO catalog_sync_state(
                    source_kind,source_signature,status,item_count,error_message,
                    synced_at,cursor_version,cursor_json
                ) VALUES(
                    'eagle',:signature,'syncing',:count,NULL,:synced_at,
                    :cursor_version,:cursor_json
                )
                ON CONFLICT(source_kind) DO UPDATE SET
                    source_signature=excluded.source_signature,
                    status='syncing',error_message=NULL,synced_at=excluded.synced_at,
                    cursor_version=excluded.cursor_version,cursor_json=excluded.cursor_json
                """
            ),
            {
                "signature": source_signature,
                "count": item_count,
                "synced_at": synced_at,
                "cursor_version": EAGLE_SYNC_CURSOR_VERSION,
                "cursor_json": json.dumps(cursor, sort_keys=True, separators=(",", ":")),
            },
        )

    def _new_eagle_cursor(
        self,
        *,
        mode: str,
        source_snapshot: str,
    ) -> dict[str, Any]:
        return {
            "mode": mode,
            "scan_id": f"{utc_now_text()}:{new_id()}",
            "source_snapshot": source_snapshot,
            "next_offset": 0,
            "total": None,
            "changed": 0,
            "skipped": 0,
            "last_page_offset": None,
            "last_page_digest": None,
        }

    @staticmethod
    def _validated_eagle_cursor(candidate: Any) -> dict[str, Any]:
        if not isinstance(candidate, dict):
            raise ValueError("Eagle sync cursor must be an object")
        if candidate.get("mode") not in {"incremental", "full"}:
            raise ValueError("Eagle sync cursor mode is invalid")
        if not str(candidate.get("scan_id") or ""):
            raise ValueError("Eagle sync cursor scan id is missing")
        next_offset = int(candidate.get("next_offset") or 0)
        changed = int(candidate.get("changed") or 0)
        skipped = int(candidate.get("skipped") or 0)
        if min(next_offset, changed, skipped) < 0:
            raise ValueError("Eagle sync cursor contains a negative counter")
        total_value = candidate.get("total")
        total = None if total_value is None else int(total_value)
        if total is not None and (total < 0 or next_offset > total):
            raise ValueError("Eagle sync cursor total is invalid")
        if next_offset and (
            candidate.get("last_page_offset") is None
            or not str(candidate.get("last_page_digest") or "")
        ):
            raise ValueError("Eagle sync cursor anchor is missing")
        last_page_offset = candidate.get("last_page_offset")
        if last_page_offset is not None and not (
            0 <= int(last_page_offset) < max(1, next_offset)
        ):
            raise ValueError("Eagle sync cursor anchor offset is invalid")
        return {
            **candidate,
            "next_offset": next_offset,
            "total": total,
            "changed": changed,
            "skipped": skipped,
            "last_page_offset": (
                int(last_page_offset) if last_page_offset is not None else None
            ),
        }

    def _sync_eagle(
        self,
        *,
        full_rescan: bool = False,
        attempt: int = 1,
        max_attempts: int = 1,
    ) -> dict[str, Any]:
        """Checkpoint Eagle pages and only project records whose fingerprint changed."""
        source = get_eagle_catalog_source(force=True)
        source_signature, source_snapshot = self._eagle_source_tokens(source)
        with self.database.engine.connect() as conn:
            durable = conn.execute(
                text(
                    "SELECT source_signature,status,item_count,cursor_version,cursor_json "
                    "FROM catalog_sync_state WHERE source_kind='eagle'"
                )
            ).mappings().first()
        previous_count = int(durable["item_count"] or 0) if durable else 0
        cursor = None
        invalid_cursor = False
        if durable and durable["cursor_json"] and not full_rescan:
            try:
                candidate = self._validated_eagle_cursor(
                    json.loads(str(durable["cursor_json"]))
                )
                if (
                    int(durable["cursor_version"] or 0) != EAGLE_SYNC_CURSOR_VERSION
                    or durable["source_signature"] != source_signature
                ):
                    raise ValueError("incompatible Eagle sync cursor")
                cursor = candidate
            except (TypeError, ValueError, json.JSONDecodeError):
                invalid_cursor = True

        if cursor is None:
            can_increment = bool(
                durable
                and durable["status"] == "complete"
                and durable["source_signature"] == source_signature
                and not full_rescan
                and not invalid_cursor
            )
            cursor = self._new_eagle_cursor(
                mode="incremental" if can_increment else "full",
                source_snapshot=source_snapshot,
            )
            with self.database.write_transaction() as conn:
                self._write_eagle_cursor(
                    conn,
                    cursor=cursor,
                    source_signature=source_signature,
                    item_count=previous_count,
                    synced_at=utc_now_text(),
                )

        resumed = int(cursor.get("next_offset") or 0) > 0
        if resumed:
            anchor_offset = cursor.get("last_page_offset")
            anchor_digest = cursor.get("last_page_digest")
            snapshot_changed = bool(
                cursor.get("source_snapshot")
                and source_snapshot
                and cursor["source_snapshot"] != source_snapshot
            )
            anchor_valid = anchor_offset is not None and bool(anchor_digest)
            if anchor_valid and not snapshot_changed:
                anchor = get_eagle_catalog_page(
                    offset=int(anchor_offset), limit=EAGLE_SYNC_PAGE_SIZE
                )
                anchor_valid = (
                    int(anchor.total) == int(cursor.get("total") or 0)
                    and self._eagle_page_digest(anchor.items) == anchor_digest
                )
            if snapshot_changed or not anchor_valid:
                cursor = self._new_eagle_cursor(
                    mode=str(cursor.get("mode") or "full"),
                    source_snapshot=source_snapshot,
                )
                resumed = False
                with self.database.write_transaction() as conn:
                    self._write_eagle_cursor(
                        conn,
                        cursor=cursor,
                        source_signature=source_signature,
                        item_count=previous_count,
                        synced_at=utc_now_text(),
                    )

        self._publish_status(
            "eagle",
            "syncing",
            attempt=attempt,
            max_attempts=max_attempts,
            processed=int(cursor.get("next_offset") or 0),
            total=cursor.get("total"),
            changed=int(cursor.get("changed") or 0),
            skipped=int(cursor.get("skipped") or 0),
            resumed=resumed,
            full_rescan=cursor.get("mode") == "full",
            error=None,
        )

        while cursor.get("total") is None or int(cursor["next_offset"]) < int(cursor["total"]):
            offset = int(cursor.get("next_offset") or 0)
            page = get_eagle_catalog_page(offset=offset, limit=EAGLE_SYNC_PAGE_SIZE)
            if int(page.offset) != offset:
                raise RuntimeError(
                    f"Eagle returned offset {page.offset} while {offset} was requested"
                )
            if cursor.get("total") is None:
                cursor["total"] = int(page.total)
            elif int(page.total) != int(cursor["total"]):
                raise RuntimeError(
                    "Eagle library changed during catalog sync; the next run will restart safely"
                )
            if not page.items and offset < int(cursor["total"]):
                raise RuntimeError("Eagle returned an empty page before the catalog scan completed")

            item_ids = [str(row.get("id") or "") for row in page.items if row.get("id")]
            stored: dict[str, dict[str, Any]] = {}
            with self.database.write_transaction() as conn:
                if item_ids:
                    placeholders = ",".join(f":item_{index}" for index in range(len(item_ids)))
                    stored = {
                        str(row["source_key"]): dict(row)
                        for row in conn.execute(
                            text(
                                f"SELECT source_key,catalog_item_id,metadata_json FROM catalog_origins "
                                f"WHERE source_kind='eagle' AND source_key IN ({placeholders})"
                            ),
                            {f"item_{index}": item_id for index, item_id in enumerate(item_ids)},
                        ).mappings()
                    }

                unchanged = []
                for row in page.items:
                    item_id = str(row.get("id") or "")
                    item_type = str(row.get("media_type") or "")
                    if not item_id or item_type not in {"image", "video"}:
                        continue
                    fingerprint = self._eagle_item_fingerprint(row)
                    existing = stored.get(item_id)
                    existing_metadata = _json(existing["metadata_json"], {}) if existing else {}
                    is_unchanged = bool(
                        cursor.get("mode") != "full"
                        and existing
                        and existing_metadata.get("source_fingerprint") == fingerprint
                        and existing_metadata.get("fingerprint_version")
                        == EAGLE_SYNC_FINGERPRINT_VERSION
                    )
                    if is_unchanged:
                        unchanged.append(
                            {
                                "source_key": item_id,
                                "item_id": existing["catalog_item_id"],
                                "seen": cursor["scan_id"],
                            }
                        )
                        cursor["skipped"] = int(cursor.get("skipped") or 0) + 1
                        continue

                    if existing:
                        conn.execute(
                            text(
                                "DELETE FROM catalog_item_tags "
                                "WHERE catalog_item_id=:item AND source IN ('eagle','source')"
                            ),
                            {"item": existing["catalog_item_id"]},
                        )
                    detail = f"/EAGLE_{item_type}/{quote(item_id)}/"
                    metadata = {
                        "ext": row.get("ext"),
                        "folders": row.get("folders") or [],
                        "source_fingerprint": fingerprint,
                        "fingerprint_version": EAGLE_SYNC_FINGERPRINT_VERSION,
                        "modified_at": row.get("modified_at") or None,
                    }
                    self._upsert(
                        conn,
                        identity_key=f"eagle:{item_id}",
                        source_kind="eagle",
                        source_key=item_id,
                        item_type=item_type,
                        title=str(row.get("name") or "Untitled"),
                        description=str(row.get("description") or ""),
                        thumbnail_ref=str(row.get("thumbnail_route") or ""),
                        detail_uri=detail,
                        original_url=str(row.get("original_url") or ""),
                        tags=[(str(tag), "eagle") for tag in row.get("tags") or []],
                        captured_at=str(row.get("captured_at") or ""),
                        source_updated_at=str(row.get("modified_at") or ""),
                        metadata=metadata,
                        content_fingerprint=fingerprint,
                        prefer=True,
                        seen_at=str(cursor["scan_id"]),
                    )
                    cursor["changed"] = int(cursor.get("changed") or 0) + 1

                if unchanged:
                    conn.execute(
                        text(
                            "UPDATE catalog_origins SET last_seen_at=:seen,stale=0 "
                            "WHERE source_kind='eagle' AND source_key=:source_key"
                        ),
                        unchanged,
                    )
                    conn.execute(
                        text(
                            "UPDATE catalog_items SET is_deleted=0,availability='available' "
                            "WHERE id=:item_id"
                        ),
                        unchanged,
                    )

                next_offset = offset + len(page.items)
                if next_offset <= offset and next_offset < int(cursor["total"]):
                    raise RuntimeError("Eagle catalog pagination did not advance")
                cursor.update(
                    {
                        "next_offset": next_offset,
                        "last_page_offset": offset,
                        "last_page_digest": self._eagle_page_digest(page.items),
                    }
                )
                batch_time = utc_now_text()
                self._write_eagle_cursor(
                    conn,
                    cursor=cursor,
                    source_signature=source_signature,
                    item_count=previous_count,
                    synced_at=batch_time,
                )

            self._publish_status(
                "eagle",
                "syncing",
                attempt=attempt,
                max_attempts=max_attempts,
                processed=min(int(cursor["next_offset"]), int(cursor["total"])),
                total=int(cursor["total"]),
                changed=int(cursor["changed"]),
                skipped=int(cursor["skipped"]),
                resumed=resumed,
                full_rescan=cursor.get("mode") == "full",
                error=None,
                synced_at=batch_time,
            )

        final_source = get_eagle_catalog_source(force=True)
        final_signature, final_snapshot = self._eagle_source_tokens(final_source)
        if final_signature != source_signature or (
            source_snapshot and final_snapshot and final_snapshot != source_snapshot
        ):
            raise RuntimeError(
                "Eagle library changed during catalog sync; the next run will restart safely"
            )

        completed_at = utc_now_text()
        with self.database.write_transaction() as conn:
            seen_count = int(
                conn.execute(
                    text(
                        "SELECT COUNT(*) FROM catalog_origins "
                        "WHERE source_kind='eagle' AND last_seen_at=:scan_id"
                    ),
                    {"scan_id": cursor["scan_id"]},
                ).scalar()
                or 0
            )
            if seen_count != int(cursor.get("total") or 0):
                raise RuntimeError(
                    "Eagle returned an unstable item order; the next run will restart safely"
                )
            deleted = conn.execute(
                text(
                    "UPDATE catalog_origins SET stale=1 "
                    "WHERE source_kind='eagle' AND stale=0 AND last_seen_at<>:scan_id"
                ),
                {"scan_id": cursor["scan_id"]},
            ).rowcount
            conn.execute(
                text(
                    """
                    UPDATE catalog_items SET is_deleted=1,availability='missing'
                    WHERE id IN (
                        SELECT catalog_item_id FROM catalog_origins
                        WHERE source_kind='eagle' AND stale=1
                    )
                    AND NOT EXISTS (
                        SELECT 1 FROM catalog_origins live
                        WHERE live.catalog_item_id=catalog_items.id AND live.stale=0
                    )
                    """
                )
            )
            count = int(
                conn.execute(
                    text(
                        "SELECT COUNT(*) FROM catalog_origins "
                        "WHERE source_kind='eagle' AND stale=0"
                    )
                ).scalar()
                or 0
            )
            conn.execute(
                text(
                    """
                    INSERT INTO catalog_sync_state(
                        source_kind,source_signature,status,item_count,error_message,
                        synced_at,cursor_version,cursor_json
                    ) VALUES('eagle',:signature,'complete',:count,NULL,:synced_at,:version,NULL)
                    ON CONFLICT(source_kind) DO UPDATE SET
                        source_signature=excluded.source_signature,status='complete',
                        item_count=excluded.item_count,error_message=NULL,
                        synced_at=excluded.synced_at,cursor_version=excluded.cursor_version,
                        cursor_json=NULL
                    """
                ),
                {
                    "signature": source_signature,
                    "count": count,
                    "synced_at": completed_at,
                    "version": EAGLE_SYNC_CURSOR_VERSION,
                },
            )
        return {
            "count": count,
            "processed": int(cursor.get("total") or 0),
            "total": int(cursor.get("total") or 0),
            "changed": int(cursor.get("changed") or 0),
            "skipped": int(cursor.get("skipped") or 0),
            "deleted": max(0, int(deleted or 0)),
            "resumed": resumed,
            "full_rescan": cursor.get("mode") == "full",
            "synced_at": completed_at,
        }

    @staticmethod
    def _database_is_locked(exc: Exception) -> bool:
        """Return whether an exception chain represents SQLite BUSY/LOCKED."""
        pending: list[BaseException] = [exc]
        seen: set[int] = set()
        while pending:
            current = pending.pop()
            if id(current) in seen:
                continue
            seen.add(id(current))
            if isinstance(current, sqlite3.Error):
                code = getattr(current, "sqlite_errorcode", None)
                if isinstance(code, int) and (code & 0xFF) in {
                    sqlite3.SQLITE_BUSY,
                    sqlite3.SQLITE_LOCKED,
                }:
                    return True
                message = str(current).casefold()
                if (
                    "database is locked" in message
                    or "database table is locked" in message
                ):
                    return True
            for nested in (
                getattr(current, "orig", None),
                current.__cause__,
                current.__context__,
            ):
                if isinstance(nested, BaseException):
                    pending.append(nested)
        return False

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
            return self._sync_selected(selected, full_rescan=full_rescan)

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
            return {} if populated else self._sync_selected(selected)


class CatalogService:
    """Query the Catalog without exposing source-owned absolute paths."""

    def __init__(self, database: ResourceDatabase):
        self.database = database

    def count(self) -> int:
        with self.database.engine.connect() as conn:
            return int(conn.execute(text("SELECT COUNT(*) FROM catalog_items WHERE is_deleted=0")).scalar() or 0)

    @staticmethod
    def _decode_cursor(query: CatalogQuery) -> dict[str, Any] | None:
        if not query.cursor:
            return None
        try:
            raw = base64.urlsafe_b64decode(query.cursor + "=" * (-len(query.cursor) % 4))
            payload = json.loads(raw)
            if payload.get("signature") != query.signature():
                raise ValueError("cursor 與目前查詢不相符")
            return payload
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("無效的 Catalog cursor") from exc

    @staticmethod
    def _encode_cursor(query: CatalogQuery, sort_value: Any, item_id: str) -> str:
        raw = json.dumps({"signature": query.signature(), "sort": sort_value, "id": item_id}, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @staticmethod
    def _row(row: Any) -> dict[str, Any]:
        result = dict(row)
        result["tags"] = [tag for tag in str(result.pop("tag_names", "") or "").split("\x1f") if tag]
        result["sources"] = [source for source in str(result.pop("source_names", "") or "").split(",") if source]
        result["favorite"] = bool(result.get("favorite"))
        result["hidden"] = bool(result.get("hidden"))
        result["metadata"] = _json(result.pop("metadata_json", "{}"), {})
        return result

    def list(self, query: CatalogQuery) -> dict[str, Any]:
        cursor = self._decode_cursor(query)
        params: dict[str, Any] = {"limit": query.limit + 1, "seed": query.seed or 1}
        joins = ["LEFT JOIN item_user_state us ON us.catalog_item_id=i.id"]
        conditions = ["i.is_deleted=0", "COALESCE(us.hidden,0)=0"]
        if query.scope == "gallery":
            conditions.append("EXISTS (SELECT 1 FROM catalog_origins go WHERE go.catalog_item_id=i.id AND go.source_kind IN ('local','eagle','bookmarks') AND go.stale=0)")
        elif query.scope == "resources":
            conditions.append("EXISTS (SELECT 1 FROM catalog_origins ro WHERE ro.catalog_item_id=i.id AND ro.source_kind='resources' AND ro.stale=0)")
        if query.sources:
            placeholders = []
            for index, source in enumerate(query.sources):
                params[f"source_{index}"] = source
                placeholders.append(f":source_{index}")
            conditions.append(f"EXISTS (SELECT 1 FROM catalog_origins so WHERE so.catalog_item_id=i.id AND so.stale=0 AND so.source_kind IN ({','.join(placeholders)}))")
        if query.item_type:
            params["item_type"] = query.item_type
            if query.item_type == "bookmark":
                conditions.append("EXISTS (SELECT 1 FROM catalog_origins bo WHERE bo.catalog_item_id=i.id AND bo.source_kind='bookmarks' AND bo.stale=0)")
            else:
                conditions.append("i.item_type=:item_type")
        if query.favorite:
            conditions.append("COALESCE(us.favorite,0)=1")
        if query.unviewed:
            conditions.append("COALESCE(us.open_count,0)=0")
        if query.duration_min is not None:
            params["duration_min"] = query.duration_min
            conditions.append("i.duration_seconds>=:duration_min")
        if query.duration_max is not None:
            params["duration_max"] = query.duration_max
            conditions.append("i.duration_seconds<=:duration_max")
        if query.added_from:
            params["added_from"] = query.added_from
            conditions.append("i.captured_at>=:added_from")
        if query.added_to:
            params["added_to"] = query.added_to
            conditions.append("i.captured_at<=:added_to")
        if query.tags:
            tag_placeholders = []
            for index, tag in enumerate(query.tags):
                params[f"tag_{index}"] = tag
                tag_placeholders.append(f":tag_{index}")
            operator = ">= :tag_count" if query.tag_mode == "all" else ">= 1"
            params["tag_count"] = len(query.tags)
            conditions.append(
                f"(SELECT COUNT(DISTINCT ct.normalized_name) FROM catalog_item_tags cit JOIN catalog_tags ct ON ct.id=cit.tag_id WHERE cit.catalog_item_id=i.id AND ct.normalized_name IN ({','.join(tag_placeholders)})) {operator}"
            )
        rank_expression = "0.0"
        if query.q:
            fts = _fts_query(query.q)
            if not fts:
                return {"items": [], "next_cursor": None, "total_estimate": 0, "query": query.public_dict(), "facets": self.facets(query)}
            joins.append("JOIN catalog_fts f ON f.catalog_item_id=i.id")
            conditions.append("catalog_fts MATCH :fts")
            params["fts"] = fts
            rank_expression = "bm25(catalog_fts)"

        if query.sort == "title":
            sort_expression, direction = "lower(i.title)", "ASC"
        elif query.sort == "random":
            # Deterministic and seed-sensitive; changing the seed changes the
            # coefficients instead of adding the same constant to every row.
            sort_expression, direction = (
                "((unicode(substr(i.id,1,1))*(1103515245+(:seed%997)*97) + "
                "unicode(substr(i.id,5,1))*(12345+(:seed%991)*193) + "
                "unicode(substr(i.id,9,1))*2654435761 + "
                "unicode(substr(i.id,13,1))*(:seed+104729)) & 2147483647)",
                "ASC",
            )
        elif query.sort == "relevance" and query.q:
            sort_expression, direction = rank_expression, "ASC"
        elif query.sort == "most_viewed":
            sort_expression, direction = "COALESCE(us.open_count,0)", "DESC"
        elif query.sort == "recently_viewed":
            sort_expression, direction = "COALESCE(us.last_viewed_at,'')", "DESC"
        elif query.sort == "favorites":
            sort_expression, direction = "COALESCE(us.favorite,0)", "DESC"
        else:
            sort_expression, direction = "COALESCE(i.captured_at,i.indexed_at)", "DESC"
        if cursor:
            params["cursor_sort"] = cursor.get("sort")
            params["cursor_id"] = cursor.get("id")
            comparator = ">" if direction == "ASC" else "<"
            conditions.append(f"(({sort_expression}) {comparator} :cursor_sort OR (({sort_expression})=:cursor_sort AND i.id>:cursor_id))")

        sql = f"""
            SELECT i.*, COALESCE(us.favorite,0) AS favorite, COALESCE(us.hidden,0) AS hidden,
                   COALESCE(us.open_count,0) AS open_count, us.last_viewed_at,
                   ({sort_expression}) AS sort_value,
                   (SELECT GROUP_CONCAT(name, char(31)) FROM (SELECT DISTINCT t.name AS name FROM catalog_tags t JOIN catalog_item_tags it ON it.tag_id=t.id WHERE it.catalog_item_id=i.id ORDER BY t.name)) AS tag_names,
                   (SELECT GROUP_CONCAT(DISTINCT source_kind) FROM catalog_origins o WHERE o.catalog_item_id=i.id AND o.stale=0) AS source_names
            FROM catalog_items i {' '.join(joins)}
            WHERE {' AND '.join(conditions)}
            ORDER BY sort_value {direction}, i.id ASC LIMIT :limit
        """
        with self.database.engine.connect() as conn:
            rows = list(conn.execute(text(sql), params).mappings())
            count_sql = f"SELECT COUNT(DISTINCT i.id) FROM catalog_items i {' '.join(joins)} WHERE {' AND '.join(conditions[:-1] if cursor else conditions)}"
            count_params = {key: value for key, value in params.items() if key not in {"limit", "cursor_sort", "cursor_id"}}
            total = int(conn.execute(text(count_sql), count_params).scalar() or 0)
        has_more = len(rows) > query.limit
        rows = rows[: query.limit]
        next_cursor = self._encode_cursor(query, rows[-1]["sort_value"], rows[-1]["id"]) if has_more and rows else None
        return {"items": [self._row(row) for row in rows], "next_cursor": next_cursor, "total_estimate": total, "query": query.public_dict(), "facets": self.facets(query)}

    def facets(self, query: CatalogQuery) -> dict[str, Any]:
        with self.database.engine.connect() as conn:
            sources = dict(conn.execute(text("SELECT source_kind,COUNT(DISTINCT catalog_item_id) FROM catalog_origins WHERE stale=0 GROUP BY source_kind")).all())
            types = dict(conn.execute(text("SELECT item_type,COUNT(*) FROM catalog_items WHERE is_deleted=0 GROUP BY item_type")).all())
            tags = [dict(row) for row in conn.execute(text("SELECT t.name,COUNT(DISTINCT it.catalog_item_id) AS count FROM catalog_tags t JOIN catalog_item_tags it ON it.tag_id=t.id GROUP BY t.id ORDER BY count DESC,t.name LIMIT 100")).mappings()]
        return {
            "sources": sources,
            "types": types,
            "tags": tags,
            "sync": self.sync_status(),
        }

    def sync_status(self) -> dict[str, dict[str, Any]]:
        """Merge durable results with lock-safe, process-local live progress."""
        try:
            with self.database.engine.connect() as conn:
                sync = {}
                for row in conn.execute(
                    text(
                        "SELECT source_kind,status,item_count,error_message,synced_at,"
                        "cursor_version,cursor_json FROM catalog_sync_state"
                    )
                ).mappings():
                    state = {
                        "status": row["status"],
                        "item_count": row["item_count"],
                        "error": row["error_message"],
                        "synced_at": row["synced_at"],
                    }
                    if (
                        row["source_kind"] == "eagle"
                        and int(row["cursor_version"] or 0) == EAGLE_SYNC_CURSOR_VERSION
                        and row["cursor_json"]
                    ):
                        cursor = _json(row["cursor_json"], {})
                        if isinstance(cursor, dict):
                            state.update(
                                {
                                    "processed": int(cursor.get("next_offset") or 0),
                                    "total": cursor.get("total"),
                                    "changed": int(cursor.get("changed") or 0),
                                    "skipped": int(cursor.get("skipped") or 0),
                                    "resumed": int(cursor.get("next_offset") or 0) > 0,
                                    "full_rescan": cursor.get("mode") == "full",
                                }
                            )
                    sync[row["source_kind"]] = state
        except Exception as exc:
            if not CatalogSyncService._database_is_locked(exc):
                raise
            sync = {}
        for source, runtime_state in self.database.catalog_sync_status().items():
            durable_state = sync.get(source, {})
            runtime_is_active = runtime_state.get("status") in {"syncing", "retrying"}
            runtime_is_current = str(runtime_state.get("synced_at") or "") >= str(
                durable_state.get("synced_at") or ""
            )
            if runtime_is_active or runtime_is_current:
                sync[source] = {
                    **durable_state,
                    **runtime_state,
                }
        return sync

    def get(self, item_id: str) -> dict[str, Any]:
        with self.database.engine.connect() as conn:
            row = conn.execute(
                text("SELECT i.*,COALESCE(us.favorite,0) favorite,COALESCE(us.hidden,0) hidden,COALESCE(us.open_count,0) open_count,us.last_viewed_at,'' tag_names,'' source_names FROM catalog_items i LEFT JOIN item_user_state us ON us.catalog_item_id=i.id WHERE i.id=:id AND i.is_deleted=0"),
                {"id": item_id},
            ).mappings().first()
            if row is None:
                raise LookupError(item_id)
            result = self._row(row)
            result["origins"] = [dict(origin) for origin in conn.execute(text("SELECT source_kind,source_key,detail_uri,original_url,metadata_json,stale FROM catalog_origins WHERE catalog_item_id=:id"), {"id": item_id}).mappings()]
            result["tags"] = list(conn.execute(text("SELECT DISTINCT t.name FROM catalog_tags t JOIN catalog_item_tags it ON it.tag_id=t.id WHERE it.catalog_item_id=:id ORDER BY t.name"), {"id": item_id}).scalars())
            return result

    def get_origin(self, item_id: str, source_kind: str, *, include_stale: bool = False) -> dict[str, Any] | None:
        """Return the source-owned launch metadata for one canonical item.

        A canonical Catalog item can have several origins. Callers that render a
        source-specific surface must use this method instead of the item-level
        primary URI, otherwise a Bookmark can accidentally open its Resource
        detail page.
        """
        stale_clause = "" if include_stale else "AND stale=0"
        with self.database.engine.connect() as conn:
            row = conn.execute(
                text(f"SELECT * FROM catalog_origins WHERE catalog_item_id=:item AND source_kind=:source {stale_clause} ORDER BY last_seen_at DESC LIMIT 1"),
                {"item": item_id, "source": source_kind},
            ).mappings().first()
        if row is None:
            return None
        result = dict(row)
        result["metadata"] = _json(result.pop("metadata_json", "{}"), {})
        result["stale"] = bool(result.get("stale"))
        return result

    def record_event(self, item_id: str, event_type: str, *, value: float | None = None, session_id: str | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        if event_type not in {"open", "view", "favorite", "unfavorite", "hide", "unhide"}:
            raise ValueError("不支援的 Catalog event")
        now = utc_now_text()
        with self.database.write_transaction() as conn:
            if conn.execute(text("SELECT 1 FROM catalog_items WHERE id=:id"), {"id": item_id}).first() is None:
                raise LookupError(item_id)
            conn.execute(text("INSERT INTO catalog_events(id,catalog_item_id,event_type,event_value,session_id,metadata_json,created_at) VALUES(:id,:item,:type,:value,:session,:metadata,:created)"), {"id": new_id(), "item": item_id, "type": event_type, "value": value, "session": session_id, "metadata": json.dumps(metadata or {}, ensure_ascii=False), "created": now})
            conn.execute(text("INSERT OR IGNORE INTO item_user_state(catalog_item_id,updated_at) VALUES(:id,:now)"), {"id": item_id, "now": now})
            if event_type in {"open", "view"}:
                conn.execute(text("UPDATE item_user_state SET open_count=open_count+1,last_viewed_at=:now,updated_at=:now WHERE catalog_item_id=:id"), {"id": item_id, "now": now})
            elif event_type in {"favorite", "unfavorite"}:
                conn.execute(text("UPDATE item_user_state SET favorite=:value,updated_at=:now WHERE catalog_item_id=:id"), {"id": item_id, "value": 1 if event_type == "favorite" else 0, "now": now})
            elif event_type in {"hide", "unhide"}:
                conn.execute(text("UPDATE item_user_state SET hidden=:value,updated_at=:now WHERE catalog_item_id=:id"), {"id": item_id, "value": 1 if event_type == "hide" else 0, "now": now})
        return self.get(item_id)

    def save_session(self, payload: dict[str, Any], session_id: str | None = None) -> dict[str, Any]:
        query = CatalogQuery.create(**(payload.get("query") or {}))
        now = utc_now_text()
        session_id = session_id or new_id()
        focused = str(payload.get("focused_item_id") or "").strip() or None
        with self.database.write_transaction() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO browse_sessions(id,query_json,query_signature,focused_item_id,cursor,scroll_position,status,created_at,updated_at)
                    VALUES(:id,:query,:signature,:focused,:cursor,:scroll,:status,:now,:now)
                    ON CONFLICT(id) DO UPDATE SET query_json=:query,query_signature=:signature,
                        focused_item_id=:focused,cursor=:cursor,scroll_position=:scroll,status=:status,updated_at=:now
                    """
                ),
                {"id": session_id, "query": json.dumps(query.public_dict(), ensure_ascii=False), "signature": query.signature(), "focused": focused, "cursor": str(payload.get("cursor") or "") or None, "scroll": max(0, int(payload.get("scroll_position") or 0)), "status": str(payload.get("status") or "active"), "now": now},
            )
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> dict[str, Any]:
        with self.database.engine.connect() as conn:
            row = conn.execute(text("SELECT * FROM browse_sessions WHERE id=:id"), {"id": session_id}).mappings().first()
        if row is None:
            raise LookupError(session_id)
        result = dict(row)
        result["query"] = _json(result.pop("query_json"), {})
        return result

    def recent_sessions(self, limit: int = 3) -> list[dict[str, Any]]:
        with self.database.engine.connect() as conn:
            rows = list(conn.execute(text("SELECT * FROM browse_sessions WHERE status='active' ORDER BY updated_at DESC LIMIT :limit"), {"limit": max(1, min(limit, 20))}).mappings())
        output = []
        for row in rows:
            result = dict(row)
            result["query"] = _json(result.pop("query_json"), {})
            output.append(result)
        return output
