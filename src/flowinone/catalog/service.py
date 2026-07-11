"""Rebuildable Catalog projection, FTS search, facets, and local signals."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.parse import quote

from sqlalchemy import text

from src.file_handler.chrome_bookmarks import iter_chrome_bookmark_records
from src.file_handler.eagle_integration import get_eagle_stream_items
from src.file_handler.item_db import fetch_items
from src.flowinone.resource_library.canonical import normalize_resource_url
from src.flowinone.resource_library.database import ResourceDatabase
from src.flowinone.resource_library.models import new_id, utc_now_text


CATALOG_SOURCES = ("local", "eagle", "bookmarks", "resources")
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

    def __init__(self, database: ResourceDatabase):
        self.database = database

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
        duration_seconds: float | None = None,
        metadata: dict[str, Any] | None = None,
        extracted_text: str = "",
        content_fingerprint: str = "",
        prefer: bool = False,
    ) -> str:
        now = utc_now_text()
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
                    "updated_at": now,
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
                        description=COALESCE(NULLIF(:description,''), description),
                        thumbnail_ref=COALESCE(NULLIF(:thumbnail,''), thumbnail_ref),
                        primary_detail_uri=CASE WHEN :prefer=1 THEN COALESCE(NULLIF(:detail_uri,''),primary_detail_uri) ELSE COALESCE(primary_detail_uri,NULLIF(:detail_uri,'')) END,
                        original_url=COALESCE(original_url,NULLIF(:original_url,'')),
                        duration_seconds=COALESCE(:duration,duration_seconds),
                        captured_at=COALESCE(NULLIF(:captured_at,''),captured_at),
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
                "seen": now,
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
                metadata={"reading_state": row["reading_state"], "disposition": row["disposition"], "priority": row["priority"]},
                extracted_text="\n".join(filter(None, (row["summary_short"], row["why_this_matters"], row["user_note"], extracted))),
                content_fingerprint=row["content_hash"] or "", prefer=True,
            )
            count += 1
        return count

    def sync_resource(self, resource_id: str) -> int:
        with self.database.engine.begin() as conn:
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
            self._upsert(
                conn, identity_key=identity, source_kind="bookmarks", source_key=url,
                item_type="bookmark", title=str(record.get("title") or canonical),
                description=folder, original_url=canonical, detail_uri=canonical,
                tags=tags, captured_at=str(record.get("date_added") or ""), metadata={"folder_path": folder},
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

    def _sync_eagle(self, conn) -> int:
        count = 0
        for entry in get_eagle_stream_items(offset=0, limit=500):
            row = entry.to_dict() if hasattr(entry, "to_dict") else dict(entry)
            item_id = str(row.get("id") or "")
            item_type = str(row.get("media_type") or "")
            if not item_id or item_type not in {"image", "video"}:
                continue
            detail = f"/EAGLE_{item_type}/{quote(item_id)}/"
            self._upsert(
                conn, identity_key=f"eagle:{item_id}", source_kind="eagle", source_key=item_id,
                item_type=item_type, title=str(row.get("name") or "Untitled"),
                thumbnail_ref=str(row.get("thumbnail_route") or ""), detail_uri=detail,
                original_url=str(row.get("original_url") or row.get("url") or ""),
                tags=[(tag, "source") for tag in row.get("tags") or []],
                metadata={"ext": row.get("ext"), "folders": row.get("folders") or []},
            )
            count += 1
        return count

    def sync(self, sources: Iterable[str] = CATALOG_SOURCES) -> dict[str, dict[str, Any]]:
        selected = [source for source in sources if source in CATALOG_SOURCES]
        result: dict[str, dict[str, Any]] = {}
        loaders = {"resources": self._sync_resources, "bookmarks": self._sync_bookmarks, "local": self._sync_local, "eagle": self._sync_eagle}
        for source in selected:
            now = utc_now_text()
            try:
                with self.database.engine.begin() as conn:
                    conn.execute(text("UPDATE catalog_origins SET stale=1 WHERE source_kind=:source"), {"source": source})
                    count = loaders[source](conn)
                    conn.execute(
                        text("INSERT INTO catalog_sync_state(source_kind,status,item_count,synced_at) VALUES(:source,'complete',:count,:now) ON CONFLICT(source_kind) DO UPDATE SET status='complete',item_count=:count,error_message=NULL,synced_at=:now"),
                        {"source": source, "count": count, "now": now},
                    )
                result[source] = {"status": "complete", "count": count}
            except Exception as exc:
                with self.database.engine.begin() as conn:
                    conn.execute(
                        text("INSERT INTO catalog_sync_state(source_kind,status,item_count,error_message,synced_at) VALUES(:source,'failed',0,:error,:now) ON CONFLICT(source_kind) DO UPDATE SET status='failed',error_message=:error,synced_at=:now"),
                        {"source": source, "error": str(exc)[:2000], "now": now},
                    )
                result[source] = {"status": "failed", "error": str(exc)}
        return result


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
            sync = {
                row["source_kind"]: {
                    "status": row["status"], "item_count": row["item_count"],
                    "error": row["error_message"], "synced_at": row["synced_at"],
                }
                for row in conn.execute(
                    text("SELECT source_kind,status,item_count,error_message,synced_at FROM catalog_sync_state")
                ).mappings()
            }
        return {"sources": sources, "types": types, "tags": tags, "sync": sync}

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

    def record_event(self, item_id: str, event_type: str, *, value: float | None = None, session_id: str | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        if event_type not in {"open", "view", "favorite", "unfavorite", "hide", "unhide", "add_to_collection"}:
            raise ValueError("不支援的 Catalog event")
        now = utc_now_text()
        with self.database.engine.begin() as conn:
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
        with self.database.engine.begin() as conn:
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
