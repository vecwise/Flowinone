"""Catalog search, facets, paging, state, and browse metadata cache."""

from __future__ import annotations

import base64
import json
import re
from collections import OrderedDict
from dataclasses import dataclass
from threading import RLock
from typing import Any
from weakref import WeakKeyDictionary

from sqlalchemy import text

from src.flowinone.resource_library.database import ResourceDatabase
from src.flowinone.resource_library.models import new_id, utc_now_text

from .eagle_sync import EAGLE_SYNC_CURSOR_VERSION
from .eagle_library import EagleLibraryService
from .locking import database_is_locked
from .media import media_category, media_category_sql
from .query import CatalogQuery, _fts_query, _json
from .targets import BrowseTargetService

CATALOG_BROWSE_CACHE_MAX_ENTRIES = 128
CATALOG_BROWSE_CACHE_COMPONENT = "catalog_browse_cache"
_MATCH_MARKER_START = "\x01"
_MATCH_MARKER_END = "\x02"
_MATCH_REASON_FIELDS = (
    ("title", "標題", "fts_matched_title"),
    ("tags", "標籤", "fts_matched_tags"),
    ("description", "描述", "fts_matched_description"),
    ("content", "擷取內容", "fts_matched_content"),
)


@dataclass(frozen=True)
class _CachedFacets:
    sources: tuple[tuple[str, int], ...]
    types: tuple[tuple[str, int], ...]
    tags: tuple[tuple[str, int], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "sources": dict(self.sources),
            "types": dict(self.types),
            "tags": [
                {"name": name, "count": count}
                for name, count in self.tags
            ],
        }


@dataclass
class _BrowseCacheEntry:
    total: int | None = None
    facets: _CachedFacets | None = None


class _CatalogBrowseCache:
    """Small LRU cache for expensive exact Navigator metadata."""

    def __init__(self, *, max_entries: int = CATALOG_BROWSE_CACHE_MAX_ENTRIES):
        self.max_entries = max(1, max_entries)
        self._entries: OrderedDict[str, _BrowseCacheEntry] = OrderedDict()
        self._revision = ""
        self._lock = RLock()

    def _prepare(self, revision: str) -> None:
        if revision != self._revision:
            self._entries.clear()
            self._revision = revision

    def _entry(self, key: str) -> _BrowseCacheEntry:
        entry = self._entries.pop(key, None)
        if entry is None:
            entry = _BrowseCacheEntry()
        self._entries[key] = entry
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)
        return entry

    def get_total(self, key: str, revision: str) -> int | None:
        with self._lock:
            self._prepare(revision)
            entry = self._entries.get(key)
            if entry is None:
                return None
            self._entries.move_to_end(key)
            return entry.total

    def store_total(self, key: str, revision: str, total: int) -> None:
        with self._lock:
            self._prepare(revision)
            self._entry(key).total = total

    def get_facets(self, key: str, revision: str) -> _CachedFacets | None:
        with self._lock:
            self._prepare(revision)
            entry = self._entries.get(key)
            if entry is None:
                return None
            self._entries.move_to_end(key)
            return entry.facets

    def store_facets(self, key: str, revision: str, facets: _CachedFacets) -> None:
        with self._lock:
            self._prepare(revision)
            self._entry(key).facets = facets

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


_BROWSE_CACHES: WeakKeyDictionary[ResourceDatabase, _CatalogBrowseCache] = (
    WeakKeyDictionary()
)
_BROWSE_CACHES_LOCK = RLock()


def _browse_cache_for(database: ResourceDatabase) -> _CatalogBrowseCache:
    with _BROWSE_CACHES_LOCK:
        cache = _BROWSE_CACHES.get(database)
        if cache is None:
            cache = _CatalogBrowseCache()
            _BROWSE_CACHES[database] = cache
        return cache


class CatalogService:
    """Query the Catalog without exposing source-owned absolute paths."""

    def __init__(self, database: ResourceDatabase):
        self.database = database
        self._browse_cache = _browse_cache_for(database)

    def _eagle_allowed(self) -> bool:
        return EagleLibraryService(self.database).visibility()["allowed"]

    def _visibility_sql(self, alias="i") -> str:
        if self._eagle_allowed():
            return "1=1"
        return (f"(NOT EXISTS (SELECT 1 FROM catalog_origins eg WHERE eg.catalog_item_id={alias}.id "
                "AND eg.source_kind='eagle') OR EXISTS (SELECT 1 FROM catalog_origins other "
                f"WHERE other.catalog_item_id={alias}.id AND other.source_kind!='eagle' AND other.stale=0))")

    @staticmethod
    def mark_browse_data_changed(database: ResourceDatabase) -> None:
        """Invalidate Catalog metadata in this and other application processes.

        Navigator requests may be served by a web process while synchronization
        happens in a worker process.  The persisted revision makes the next
        read in either process discard its local LRU entries; clearing here
        avoids even that one-request delay in the process that performed the
        mutation.
        """
        revision = json.dumps({"revision": new_id()}, separators=(",", ":"))
        with database.write_transaction() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO runtime_state(component,heartbeat_at,metadata_json)
                    VALUES(:component,:now,:revision)
                    ON CONFLICT(component) DO UPDATE SET
                        heartbeat_at=excluded.heartbeat_at,
                        metadata_json=excluded.metadata_json
                    """
                ),
                {
                    "component": CATALOG_BROWSE_CACHE_COMPONENT,
                    "now": utc_now_text(),
                    "revision": revision,
                },
            )
        _browse_cache_for(database).clear()

    def _browse_cache_context(self, query: CatalogQuery) -> tuple[str, str]:
        """Return a filter key and the durable Catalog mutation revision."""
        with self.database.engine.connect() as conn:
            revision = str(
                conn.execute(
                    text(
                        "SELECT metadata_json FROM runtime_state "
                        "WHERE component=:component"
                    ),
                    {"component": CATALOG_BROWSE_CACHE_COMPONENT},
                ).scalar()
                or ""
            )
        return query.filter_signature() + f":eagle={self._eagle_allowed()}", revision

    def count(self) -> int:
        visibility = self._visibility_sql()
        with self.database.engine.connect() as conn:
            return int(conn.execute(text(f"SELECT COUNT(*) FROM catalog_items i WHERE is_deleted=0 AND {visibility}")).scalar() or 0)

    def folder_children(self, parent: str = "", *, limit: int = 24) -> list[dict[str, Any]]:
        """Show direct Chrome folder children while counting bookmarked items below them."""
        parent = parent.strip()[:500]
        with self.database.engine.connect() as conn:
            rows = conn.execute(text("""
                WITH folders AS (
                    SELECT o.catalog_item_id AS item_id,
                           CASE WHEN json_valid(o.metadata_json)
                                THEN COALESCE(json_extract(o.metadata_json, '$.folder_path'), '')
                                ELSE '' END AS path
                    FROM catalog_origins o
                    JOIN catalog_items i ON i.id=o.catalog_item_id AND i.is_deleted=0
                    LEFT JOIN item_user_state state ON state.catalog_item_id=i.id
                    WHERE o.source_kind='bookmarks' AND o.stale=0 AND COALESCE(state.hidden,0)=0
                ), descendants AS (
                    SELECT item_id,
                           CASE WHEN :parent='' THEN path
                                ELSE substr(path, length(:parent)+4) END AS remainder
                    FROM folders
                    WHERE :parent='' OR substr(path,1,length(:parent)+3)=:parent||' / '
                ), children AS (
                    SELECT item_id,
                           CASE WHEN instr(remainder,' / ')>0
                                THEN substr(remainder,1,instr(remainder,' / ')-1)
                                ELSE remainder END AS name
                    FROM descendants WHERE remainder!=''
                )
                SELECT name,COUNT(DISTINCT item_id) AS item_count
                FROM children WHERE name!=''
                GROUP BY name ORDER BY item_count DESC,name COLLATE NOCASE LIMIT :limit
            """), {"parent": parent, "limit": max(1, min(limit, 100))}).mappings()
            return [
                {"name": row["name"], "path": f"{parent} / {row['name']}" if parent else row["name"], "count": row["item_count"]}
                for row in rows
            ]

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
        result["media_category"] = media_category(result.get("item_type"), result.get("original_url"))
        match_reason = None
        for field, label, key in _MATCH_REASON_FIELDS:
            value = str(result.pop(key, "") or "")
            if _MATCH_MARKER_START not in value:
                continue
            excerpt = " ".join(
                value.replace(_MATCH_MARKER_START, "")
                .replace(_MATCH_MARKER_END, "")
                .split()
            )
            match_reason = {
                "field": field,
                "label": label,
                "excerpt": excerpt[:360],
            }
            break
        result["tags"] = [tag for tag in str(result.pop("tag_names", "") or "").split("\x1f") if tag]
        result["sources"] = [source for source in str(result.pop("source_names", "") or "").split(",") if source]
        result["favorite"] = bool(result.get("favorite"))
        result["hidden"] = bool(result.get("hidden"))
        result["metadata"] = _json(result.pop("metadata_json", "{}"), {})
        if match_reason:
            result["match_reason"] = match_reason
        return result

    def list(
        self,
        query: CatalogQuery,
        *,
        candidate_ids: tuple[str, ...] | None = None,
        include_facets: bool = True,
        include_items: bool = True,
    ) -> dict[str, Any]:
        """Read a page, or only its cached exact total for topic chips."""
        if candidate_ids is not None and not candidate_ids:
            return {"items": [], "next_cursor": None, "total_estimate": 0, "query": query.public_dict(), "facets": self.facets(query) if include_facets else {}}
        cursor = self._decode_cursor(query)
        params: dict[str, Any] = {"limit": query.limit + 1, "seed": query.seed or 1}
        joins = ["LEFT JOIN item_user_state us ON us.catalog_item_id=i.id"]
        conditions = ["i.is_deleted=0", "COALESCE(us.hidden,0)=0"]
        eagle_allowed = self._eagle_allowed()
        conditions.append(self._visibility_sql())
        if query.target:
            condition, target_params = BrowseTargetService(self.database).predicate(
                query.target, sources=query.sources, eagle_allowed=eagle_allowed,
            )
            conditions.append(condition)
            params.update(target_params)
        if candidate_ids is not None:
            placeholders = []
            for index, item_id in enumerate(candidate_ids):
                params[f"candidate_{index}"] = item_id
                placeholders.append(f":candidate_{index}")
            conditions.append(f"i.id IN ({','.join(placeholders)})")
        if query.scope == "gallery":
            conditions.append("EXISTS (SELECT 1 FROM catalog_origins go WHERE go.catalog_item_id=i.id AND go.source_kind IN ('local','eagle','bookmarks') AND go.stale=0)")
        elif query.scope == "resources":
            conditions.append("EXISTS (SELECT 1 FROM catalog_origins ro WHERE ro.catalog_item_id=i.id AND ro.source_kind='resources' AND ro.stale=0)")
        if query.sources:
            placeholders = []
            for index, source in enumerate(query.sources):
                if source == "eagle" and not eagle_allowed:
                    continue
                params[f"source_{index}"] = source
                placeholders.append(f":source_{index}")
            conditions.append(f"EXISTS (SELECT 1 FROM catalog_origins so WHERE so.catalog_item_id=i.id AND so.stale=0 AND so.source_kind IN ({','.join(placeholders)}))" if placeholders else "0=1")
        if query.media:
            params["media"] = query.media
            conditions.append(f"({media_category_sql()})=:media")
        if query.item_type:
            params["item_type"] = query.item_type
            if query.item_type == "bookmark":
                conditions.append("EXISTS (SELECT 1 FROM catalog_origins bo WHERE bo.catalog_item_id=i.id AND bo.source_kind='bookmarks' AND bo.stale=0)")
            else:
                conditions.append("i.item_type=:item_type")
        if query.folder:
            if "bookmarks" not in query.sources:
                conditions.append("0=1")
            params["folder"] = query.folder
            conditions.append("""
                EXISTS (
                    SELECT 1 FROM catalog_origins folder_origin
                    WHERE folder_origin.catalog_item_id=i.id
                      AND folder_origin.source_kind='bookmarks' AND folder_origin.stale=0
                      AND json_valid(folder_origin.metadata_json)
                      AND (json_extract(folder_origin.metadata_json,'$.folder_path')=:folder
                           OR substr(json_extract(folder_origin.metadata_json,'$.folder_path'),1,length(:folder)+3)=:folder||' / ')
                )
            """)
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
            if query.tag_mode == "all" and len(query.tags) > 1:
                params["tag_count"] = len(query.tags)
                conditions.append(
                    f"(SELECT COUNT(DISTINCT ct.normalized_name) FROM catalog_item_tags cit JOIN catalog_tags ct ON ct.id=cit.tag_id WHERE cit.catalog_item_id=i.id AND ct.normalized_name IN ({','.join(tag_placeholders)})) >= :tag_count"
                )
            else:
                # Start from the existing normalized-tag and tag/item indexes,
                # rather than counting matching tags once for every item.
                conditions.append(
                    f"i.id IN (SELECT cit.catalog_item_id FROM catalog_tags ct "
                    f"JOIN catalog_item_tags cit ON cit.tag_id=ct.id "
                    f"WHERE ct.normalized_name IN ({','.join(tag_placeholders)}))"
                )
        rank_expression = "0.0"
        match_select = """
            NULL AS fts_matched_title,
            NULL AS fts_matched_description,
            NULL AS fts_matched_tags,
            NULL AS fts_matched_content,
        """
        search_prefix = ""
        if query.q:
            fts = _fts_query(query.q)
            if not fts:
                return {"items": [], "next_cursor": None, "total_estimate": 0, "query": query.public_dict(), "facets": self.facets(query) if include_facets else {}}
            params["fts"] = fts
            if re.search(r"[\u3400-\u9fff]", query.q):
                # unicode61 treats uninterrupted Chinese as one token. Add
                # substring candidates only from album titles, not image text.
                words = re.findall(r"[\w\u3400-\u9fff-]+", query.q.lower())[:24]
                title_conditions = []
                for number, word in enumerate(words):
                    params[f"album_word_{number}"] = word
                    title_conditions.append(f"instr(lower(a.title),:album_word_{number})>0")
                search_prefix = """
                    WITH search_matches AS MATERIALIZED (
                        SELECT catalog_item_id,bm25(catalog_fts) AS rank,
                               highlight(catalog_fts,1,char(1),char(2)) AS title_match,
                               highlight(catalog_fts,2,char(1),char(2)) AS description_match,
                               highlight(catalog_fts,3,char(1),char(2)) AS tags_match,
                               snippet(catalog_fts,4,char(1),char(2),'…',20) AS content_match
                        FROM catalog_fts WHERE catalog_fts MATCH :fts
                    ), search_ids AS (
                        SELECT catalog_item_id FROM search_matches
                        UNION SELECT a.id FROM catalog_items a
                        WHERE a.item_type='album' AND """ + " AND ".join(title_conditions) + ") "
                joins.extend([
                    "JOIN search_ids si ON si.catalog_item_id=i.id",
                    "LEFT JOIN search_matches sm ON sm.catalog_item_id=i.id",
                ])
                params["album_exact"] = query.q.lower()
                rank_expression = "CASE WHEN i.item_type='album' AND lower(i.title)=:album_exact THEN -1000000 ELSE COALESCE(sm.rank,0) END"
                match_select = """
                    COALESCE(sm.title_match,char(1)||i.title||char(2)) AS fts_matched_title,
                    sm.description_match AS fts_matched_description,
                    sm.tags_match AS fts_matched_tags,sm.content_match AS fts_matched_content,
                """
            else:
                joins.append("JOIN catalog_fts f ON f.catalog_item_id=i.id")
                conditions.append("catalog_fts MATCH :fts")
                rank_expression = "bm25(catalog_fts)"
                match_select = """
                    highlight(catalog_fts, 1, char(1), char(2)) AS fts_matched_title,
                    highlight(catalog_fts, 2, char(1), char(2)) AS fts_matched_description,
                    highlight(catalog_fts, 3, char(1), char(2)) AS fts_matched_tags,
                    snippet(catalog_fts, 4, char(1), char(2), '…', 20) AS fts_matched_content,
                """

        if query.sort == "page_order":
            sort_expression, direction = "flowinone_natural_key(i.title)", "ASC"
        elif query.sort == "title":
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
        filter_conditions = conditions
        page_conditions = conditions.copy()
        if cursor:
            params["cursor_sort"] = cursor.get("sort")
            params["cursor_id"] = cursor.get("id")
            comparator = ">" if direction == "ASC" else "<"
            page_conditions.append(f"(({sort_expression}) {comparator} :cursor_sort OR (({sort_expression})=:cursor_sort AND i.id>:cursor_id))")

        eagle_clause = "" if eagle_allowed else "AND o.source_kind!='eagle'"
        sql = f"""{search_prefix}
            SELECT i.*, COALESCE(us.favorite,0) AS favorite, COALESCE(us.hidden,0) AS hidden,
                   COALESCE(us.open_count,0) AS open_count, us.last_viewed_at,
                   ({sort_expression}) AS sort_value,
                   {match_select}
                   (SELECT GROUP_CONCAT(name, char(31)) FROM (SELECT DISTINCT t.name AS name FROM catalog_tags t JOIN catalog_item_tags it ON it.tag_id=t.id WHERE it.catalog_item_id=i.id ORDER BY t.name)) AS tag_names,
                   (SELECT GROUP_CONCAT(DISTINCT source_kind) FROM catalog_origins o WHERE o.catalog_item_id=i.id AND o.stale=0 {eagle_clause}) AS source_names
            FROM catalog_items i {' '.join(joins)}
            WHERE {' AND '.join(page_conditions)}
            ORDER BY sort_value {direction}, i.id ASC LIMIT :limit
        """
        cache_context = self._browse_cache_context(query) if candidate_ids is None else None
        with self.database.engine.connect() as conn:
            rows = list(conn.execute(text(sql), params).mappings()) if include_items else []
            total = self._browse_cache.get_total(*cache_context) if cache_context else None
            if total is None:
                count_sql = f"{search_prefix} SELECT COUNT(DISTINCT i.id) FROM catalog_items i {' '.join(joins)} WHERE {' AND '.join(filter_conditions)}"
                count_params = {key: value for key, value in params.items() if key not in {"limit", "cursor_sort", "cursor_id"}}
                total = int(conn.execute(text(count_sql), count_params).scalar() or 0)
                if cache_context:
                    self._browse_cache.store_total(*cache_context, total)
        has_more = len(rows) > query.limit
        rows = rows[: query.limit]
        next_cursor = self._encode_cursor(query, rows[-1]["sort_value"], rows[-1]["id"]) if has_more and rows else None
        return {"items": [self._row(row) for row in rows], "next_cursor": next_cursor, "total_estimate": total, "query": query.public_dict(), "facets": self.facets(query, cache_context=cache_context) if include_facets else {}}

    def matching_ids(self, query: CatalogQuery, candidate_ids: list[str]) -> set[str]:
        """Apply the same Navigator filters to a bounded related-item pool."""
        matches: set[str] = set()
        unique_ids = list(dict.fromkeys(candidate_ids))
        for start in range(0, len(unique_ids), 100):
            chunk = tuple(unique_ids[start:start + 100])
            values = query.public_dict()
            values.update({"limit": len(chunk), "cursor": None})
            page = self.list(CatalogQuery.create(**values), candidate_ids=chunk, include_facets=False)
            matches.update(item["id"] for item in page["items"])
        return matches

    def facets(
        self,
        query: CatalogQuery,
        *,
        cache_context: tuple[str, str] | None = None,
    ) -> dict[str, Any]:
        """Return catalog facets with live sync state kept outside the cache."""
        cache_key, cache_revision = cache_context or self._browse_cache_context(query)
        visibility = self._visibility_sql()
        origin_visibility = "1=1" if self._eagle_allowed() else "source_kind!='eagle'"
        cached = self._browse_cache.get_facets(cache_key, cache_revision)
        if cached is None:
            with self.database.engine.connect() as conn:
                sources = tuple(
                    (str(source), int(count))
                    for source, count in conn.execute(
                        text(
                            "SELECT source_kind,COUNT(DISTINCT catalog_item_id) "
                            f"FROM catalog_origins WHERE stale=0 AND {origin_visibility} GROUP BY source_kind"
                        )
                    ).all()
                )
                types = tuple(
                    (str(item_type), int(count))
                    for item_type, count in conn.execute(
                        text(
                            "SELECT item_type,COUNT(*) FROM catalog_items i "
                            f"WHERE is_deleted=0 AND {visibility} GROUP BY item_type"
                        )
                    ).all()
                )
                tags = tuple(
                    (str(row["name"]), int(row["count"]))
                    for row in conn.execute(
                        text(
                            "SELECT t.name,COUNT(DISTINCT it.catalog_item_id) AS count "
                            "FROM catalog_tags t JOIN catalog_item_tags it ON it.tag_id=t.id "
                            f"JOIN catalog_items i ON i.id=it.catalog_item_id WHERE {visibility} "
                            "GROUP BY t.id ORDER BY count DESC,t.name LIMIT 100"
                        )
                    ).mappings()
                )
            cached = _CachedFacets(sources=sources, types=types, tags=tags)
            self._browse_cache.store_facets(cache_key, cache_revision, cached)
        return {**cached.as_dict(), "sync": self.sync_status()}

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
            if not database_is_locked(exc):
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
        visibility = self._visibility_sql()
        eagle_clause = "" if self._eagle_allowed() else "AND source_kind!='eagle'"
        with self.database.engine.connect() as conn:
            row = conn.execute(
                text(f"SELECT i.*,COALESCE(us.favorite,0) favorite,COALESCE(us.hidden,0) hidden,COALESCE(us.open_count,0) open_count,us.last_viewed_at,'' tag_names,'' source_names FROM catalog_items i LEFT JOIN item_user_state us ON us.catalog_item_id=i.id WHERE i.id=:id AND i.is_deleted=0 AND {visibility}"),
                {"id": item_id},
            ).mappings().first()
            if row is None:
                raise LookupError(item_id)
            result = self._row(row)
            result["origins"] = [dict(origin) for origin in conn.execute(text(f"SELECT source_kind,source_key,detail_uri,original_url,metadata_json,stale FROM catalog_origins WHERE catalog_item_id=:id {eagle_clause}"), {"id": item_id}).mappings()]
            result["tags"] = list(conn.execute(text("SELECT DISTINCT t.name FROM catalog_tags t JOIN catalog_item_tags it ON it.tag_id=t.id WHERE it.catalog_item_id=:id ORDER BY t.name"), {"id": item_id}).scalars())
            return result

    def text_excerpts_for_items(self, item_ids: list[str], *, length: int = 440) -> dict[str, str]:
        """Read short extracted-text previews for the current Catalog page."""
        if not item_ids:
            return {}
        params = {f"item_{index}": item_id for index, item_id in enumerate(item_ids)}
        placeholders = ",".join(f":item_{index}" for index in range(len(item_ids)))
        params["length"] = length
        with self.database.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT catalog_item_id, substr(extracted_text, 1, :length) excerpt "
                    f"FROM catalog_fts WHERE catalog_item_id IN ({placeholders})"
                ),
                params,
            ).mappings()
            return {
                row["catalog_item_id"]: " ".join((row["excerpt"] or "").split())
                for row in rows
            }

    def get_origin(self, item_id: str, source_kind: str, *, include_stale: bool = False) -> dict[str, Any] | None:
        """Return the source-owned launch metadata for one canonical item.

        A canonical Catalog item can have several origins. Callers that render a
        source-specific surface must use this method instead of the item-level
        primary URI, otherwise a Bookmark can accidentally open its Resource
        detail page.
        """
        if source_kind == "eagle" and not self._eagle_allowed():
            return None
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

    def get_origins_for_items(
        self, item_ids: Iterable[str], *, include_stale: bool = False, folder: str = ""
    ) -> dict[str, dict[str, dict[str, Any]]]:
        """Load the newest origin for every item/source pair in one query."""
        selected = tuple(dict.fromkeys(str(item_id) for item_id in item_ids if item_id))
        if not selected:
            return {}
        placeholders = ",".join(f":item_{index}" for index in range(len(selected)))
        stale_clause = "" if include_stale else "AND stale=0"
        folder_order = ""
        params = {f"item_{index}": item_id for index, item_id in enumerate(selected)}
        if folder:
            folder_order = (
                "CASE WHEN source_kind='bookmarks' AND json_valid(metadata_json) AND "
                "(json_extract(metadata_json,'$.folder_path')=:folder OR "
                "substr(json_extract(metadata_json,'$.folder_path'),1,length(:folder)+3)=:folder||' / ') "
                "THEN 0 ELSE 1 END, "
            )
            params["folder"] = folder
        eagle_clause = "AND source_kind!='eagle'" if not self._eagle_allowed() else ""
        statement = text(
            f"SELECT * FROM catalog_origins WHERE catalog_item_id IN ({placeholders}) "
            f"{stale_clause} {eagle_clause} "
            f"ORDER BY catalog_item_id, source_kind, {folder_order}last_seen_at DESC"
        )
        with self.database.engine.connect() as conn:
            rows = conn.execute(statement, params).mappings()
            grouped: dict[str, dict[str, dict[str, Any]]] = {}
            for row in rows:
                item_id = str(row["catalog_item_id"])
                source = str(row["source_kind"])
                item_origins = grouped.setdefault(item_id, {})
                if source in item_origins:
                    continue
                result = dict(row)
                result["metadata"] = _json(result.pop("metadata_json", "{}"), {})
                result["stale"] = bool(result.get("stale"))
                item_origins[source] = result
        return grouped

    def record_event(self, item_id: str, event_type: str, *, value: float | None = None, session_id: str | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        if event_type not in {"open", "view", "favorite", "unfavorite", "hide", "unhide"}:
            raise ValueError("不支援的 Catalog event")
        if not self._eagle_allowed():
            self.get(item_id)
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
        # Favorites, hidden state, and view state can all affect exact totals
        # for active Navigator filters, so invalidate the filter-keyed cache.
        self.mark_browse_data_changed(self.database)
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

    @staticmethod
    def _saved_search_result(row: dict[str, Any]) -> dict[str, Any]:
        """Normalize a persisted saved search before exposing it to callers."""
        result = dict(row)
        query = CatalogQuery.create(**_json(result.pop("query_json"), {}))
        result["query"] = query.public_dict()
        result["pinned"] = bool(result.pop("is_pinned"))
        return result

    def save_search(
        self,
        label: str,
        query: dict[str, Any],
        *,
        saved_search_id: str | None = None,
        pinned: bool = True,
    ) -> dict[str, Any]:
        """Create or update a named, reusable Navigator query.

        Browse sessions intentionally remain ephemeral and are updated while a
        user scrolls.  A saved search has its own record so a later session
        update can never overwrite the name or pinned state a user chose.
        """
        normalized_label = " ".join(str(label or "").split())[:80]
        if not normalized_label:
            raise ValueError("儲存搜尋需要名稱")
        normalized_query = CatalogQuery.create(**(query or {})).public_dict()
        normalized_query["cursor"] = None
        now = utc_now_text()
        search_id = saved_search_id or new_id()
        stored_query = json.dumps(normalized_query, ensure_ascii=False)
        signature = CatalogQuery.create(**normalized_query).signature()
        with self.database.write_transaction() as conn:
            existing = conn.execute(
                text("SELECT 1 FROM saved_catalog_searches WHERE id=:id"),
                {"id": search_id},
            ).first()
            if existing:
                conn.execute(
                    text(
                        """
                        UPDATE saved_catalog_searches
                        SET label=:label, query_json=:query, query_signature=:signature,
                            is_pinned=:pinned, updated_at=:now
                        WHERE id=:id
                        """
                    ),
                    {
                        "id": search_id,
                        "label": normalized_label,
                        "query": stored_query,
                        "signature": signature,
                        "pinned": int(bool(pinned)),
                        "now": now,
                    },
                )
            else:
                conn.execute(
                    text(
                        """
                        INSERT INTO saved_catalog_searches(
                            id,label,query_json,query_signature,is_pinned,
                            created_at,updated_at,last_used_at
                        ) VALUES(
                            :id,:label,:query,:signature,:pinned,:now,:now,NULL
                        )
                        """
                    ),
                    {
                        "id": search_id,
                        "label": normalized_label,
                        "query": stored_query,
                        "signature": signature,
                        "pinned": int(bool(pinned)),
                        "now": now,
                    },
                )
        return self.get_saved_search(search_id)

    def get_saved_search(self, saved_search_id: str) -> dict[str, Any]:
        with self.database.engine.connect() as conn:
            row = conn.execute(
                text("SELECT * FROM saved_catalog_searches WHERE id=:id"),
                {"id": saved_search_id},
            ).mappings().first()
        if row is None:
            raise LookupError(saved_search_id)
        return self._saved_search_result(dict(row))

    def list_saved_searches(self, limit: int = 12) -> list[dict[str, Any]]:
        with self.database.engine.connect() as conn:
            rows = list(
                conn.execute(
                    text(
                        """
                        SELECT * FROM saved_catalog_searches
                        ORDER BY is_pinned DESC, updated_at DESC
                        LIMIT :limit
                        """
                    ),
                    {"limit": max(1, min(int(limit), 50))},
                ).mappings()
            )
        return [self._saved_search_result(dict(row)) for row in rows]

    def delete_saved_search(self, saved_search_id: str) -> None:
        with self.database.write_transaction() as conn:
            result = conn.execute(
                text("DELETE FROM saved_catalog_searches WHERE id=:id"),
                {"id": saved_search_id},
            )
        if not result.rowcount:
            raise LookupError(saved_search_id)
