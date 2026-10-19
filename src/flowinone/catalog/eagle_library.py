"""Pinned Eagle library and explicit, previewed Catalog cleanup.

Only reads Eagle. Cleanup owns Catalog records, never Eagle assets or Resources.
"""

from __future__ import annotations

import hashlib
import json

from flask import g, has_request_context
from sqlalchemy import text

from src.file_handler.eagle_integration import get_eagle_catalog_page, get_eagle_catalog_source
from src.flowinone.resource_library.database import backup_database
from src.flowinone.resource_library.models import utc_now_text

COMPONENT = "eagle_primary_library"


class EagleLibraryError(ValueError):
    """An operation cannot safely act on the pinned library."""


def source_signature(source):
    identity = str(source.get("identity") or "")
    if not identity:
        raise EagleLibraryError("無法確認 Eagle 資源庫。")
    return hashlib.sha256(identity.encode()).hexdigest()


class EagleLibraryService:
    def __init__(self, database):
        self.database = database

    def primary(self, conn=None):
        if conn is None:
            with self.database.engine.connect() as connection:
                return self.primary(connection)
        raw = conn.execute(text(
            "SELECT metadata_json FROM runtime_state WHERE component=:component"
        ), {"component": COMPONENT}).scalar()
        return json.loads(raw) if raw else None

    def pin(self, expected_identity):
        source = get_eagle_catalog_source(force=True)
        if not expected_identity or source["identity"] != expected_identity:
            raise EagleLibraryError("目前資源庫已變更，請重新確認。")
        primary = {key: source.get(key, "") for key in ("identity", "name", "path")}
        primary["signature"] = source_signature(source)
        with self.database.write_transaction() as conn:
            previous = self.primary(conn)
            if previous and previous["signature"] != primary["signature"]:
                raise EagleLibraryError("已固定主要庫；請切回主要庫，不可直接以其他庫取代。")
            conn.execute(text(
                "INSERT INTO runtime_state(component,heartbeat_at,metadata_json) "
                "VALUES(:component,:now,:metadata) ON CONFLICT(component) DO NOTHING"
            ), {"component": COMPONENT, "now": utc_now_text(),
                "metadata": json.dumps(primary, ensure_ascii=False)})
            if self.primary(conn)["signature"] != primary["signature"]:
                raise EagleLibraryError("主要庫設定已變更，請重新確認。")
        self._invalidate()
        return primary

    def require_source(self, source, *, require_pin=False, conn=None):
        primary = self.primary(conn)
        signature = source_signature(source)
        if primary:
            if primary["signature"] != signature:
                raise EagleLibraryError("目前 Eagle 不是主要庫；已暫停 Eagle 同步與清理，請切回主要庫。")
        elif require_pin:
            raise EagleLibraryError("請先將目前 Eagle 資源庫設為主要庫。")
        else:
            # Existing installations must not replace a known library before pinning.
            if conn is None:
                with self.database.engine.connect() as connection:
                    return self.require_source(source, conn=connection)
            previous = conn.execute(text(
                "SELECT source_signature FROM catalog_sync_state WHERE source_kind='eagle'"
            )).scalar()
            if previous and previous != signature:
                raise EagleLibraryError("Eagle 資源庫與上次同步不同；請先在管理收藏確認主要庫。")
        return signature

    def status(self):
        primary = self.primary()
        try:
            current = get_eagle_catalog_source(force=True)
            state = "unconfigured" if not primary else (
                "active" if primary["signature"] == source_signature(current) else "different"
            )
        except Exception:
            current, state = None, "unavailable"
        return {"primary": primary, "current": current, "state": state,
                "allowed": bool(primary and state == "active")}

    def visibility(self):
        """One fresh library observation per HTTP request, no cross-request TTL."""
        key = str(self.database.path)
        if has_request_context():
            cache = g.setdefault("eagle_library_visibility", {})
            if key in cache:
                return cache[key]
        primary = self.primary()
        # Unconfigured legacy databases retain their existing read behavior.
        result = self.status() if primary else {"primary": None, "state": "unconfigured", "allowed": True}
        if has_request_context():
            cache[key] = result
        return result

    def _invalidate(self):
        from .browse import CatalogService
        if has_request_context():
            g.pop("eagle_library_visibility", None)
        CatalogService.mark_browse_data_changed(self.database)

    def _snapshot(self):
        source = get_eagle_catalog_source(force=True)
        self.require_source(source, require_pin=True)
        ids, offset, total = set(), 0, None
        for _ in range(10000):
            # Check before every page so switching libraries never mixes snapshots.
            observed = get_eagle_catalog_source(force=True)
            if (source_signature(observed), observed.get("version")) != (
                source_signature(source), source.get("version")
            ):
                raise EagleLibraryError("比對期間資源庫已變更，請重新預覽。")
            page = get_eagle_catalog_page(offset=offset, limit=1000)
            if total is None:
                total = page.total
            if page.total != total or page.offset != offset or (not page.items and offset < total):
                raise EagleLibraryError("Eagle 分頁不完整，未進行清理。")
            for row in page.items:
                item_id = str(row.get("id") or "")
                if not item_id or item_id in ids:
                    raise EagleLibraryError("Eagle 項目順序不穩定，未進行清理。")
                ids.add(item_id)
            offset += len(page.items)
            if offset >= total:
                break
        else:
            raise EagleLibraryError("Eagle 分頁超出上限，未進行清理。")
        final = get_eagle_catalog_source(force=True)
        if final != source or len(ids) != total:
            raise EagleLibraryError("比對期間資源庫已變更，請重新預覽。")
        return source, ids

    @staticmethod
    def _targets(conn, live_ids):
        return [dict(row) for row in conn.execute(text(
            "SELECT o.id,o.catalog_item_id,o.source_key,i.title,o.stale "
            "FROM catalog_origins o JOIN catalog_items i ON i.id=o.catalog_item_id "
            "WHERE o.source_kind='eagle' ORDER BY o.id"
        )).mappings() if row["source_key"] not in live_ids]

    @staticmethod
    def _token(source, live_ids, targets):
        return hashlib.sha256(json.dumps(
            [source, sorted(live_ids), targets], ensure_ascii=False, sort_keys=True
        ).encode()).hexdigest()

    def preview_cleanup(self):
        source, live_ids = self._snapshot()
        with self.database.engine.connect() as conn:
            targets = self._targets(conn, live_ids)
        return {"library": source, "live_count": len(live_ids), "count": len(targets),
                "sample": [{"id": row["source_key"], "title": row["title"]} for row in targets[:10]],
                "token": self._token(source, live_ids, targets)}

    def cleanup(self, token):
        if not isinstance(token, str) or not token:
            raise EagleLibraryError("請先預覽待清理資料。")
        with self.database.catalog_sync():
            source, live_ids = self._snapshot()
            with self.database.engine.connect() as conn:
                targets = self._targets(conn, live_ids)
            if self._token(source, live_ids, targets) != token:
                raise EagleLibraryError("資料已變更，請重新預覽後再清理。")
            backup = backup_database(self.database.path) if targets else None
            with self.database.write_transaction() as conn:
                # Reserve the SQLite writer before rechecking the approved set.
                conn.execute(text("UPDATE runtime_state SET heartbeat_at=heartbeat_at WHERE component=:component"), {"component": COMPONENT})
                final = get_eagle_catalog_source(force=True)
                self.require_source(final, require_pin=True, conn=conn)
                if final != source:
                    raise EagleLibraryError("備份期間資源庫已變更，請重新預覽。")
                targets = self._targets(conn, live_ids)
                if self._token(source, live_ids, targets) != token:
                    raise EagleLibraryError("資料已變更，請重新預覽後再清理。")
                deleted_items = 0
                for row in targets:
                    item = row["catalog_item_id"]
                    tag_ids = list(conn.execute(text(
                        "SELECT tag_id FROM catalog_item_tags WHERE catalog_item_id=:id"
                    ), {"id": item}).scalars())
                    conn.execute(text("DELETE FROM catalog_origins WHERE id=:id"), {"id": row["id"]})
                    conn.execute(text("DELETE FROM catalog_item_tags WHERE catalog_item_id=:id AND source='eagle'"), {"id": item})
                    if not conn.execute(text("SELECT 1 FROM catalog_origins WHERE catalog_item_id=:id LIMIT 1"), {"id": item}).scalar():
                        conn.execute(text("DELETE FROM catalog_fts WHERE catalog_item_id=:id"), {"id": item})
                        conn.execute(text("DELETE FROM catalog_items WHERE id=:id"), {"id": item})
                        deleted_items += 1
                    else:
                        # Shared records keep their user state and independent tags.
                        from .fts import CatalogSearchIndex
                        index = CatalogSearchIndex(conn, {item})
                        previous = index.get(item)
                        if previous:
                            tags = " ".join(conn.execute(text(
                                "SELECT t.name FROM catalog_tags t JOIN catalog_item_tags it "
                                "ON it.tag_id=t.id WHERE it.catalog_item_id=:id"
                            ), {"id": item}).scalars())
                            index.replace(item, title=previous['title'], description=previous['description'],
                                          tags=tags, extracted_text=previous['extracted_text'])
                    for tag_id in tag_ids:
                        conn.execute(text(
                            "DELETE FROM catalog_tags WHERE id=:id AND NOT EXISTS "
                            "(SELECT 1 FROM catalog_item_tags WHERE tag_id=:id)"
                        ), {"id": tag_id})
                conn.execute(text(
                    "UPDATE catalog_sync_state SET item_count=(SELECT COUNT(*) FROM catalog_origins "
                    "WHERE source_kind='eagle' AND stale=0) WHERE source_kind='eagle'"
                ))
        self._invalidate()
        return {"deleted_origins": len(targets), "deleted_items": deleted_items,
                "backup_path": str(backup) if backup else None}
