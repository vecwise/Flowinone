"""Typed browse destinations over existing Catalog relationships.

Targets identify a set; they do not persist another copy of its membership.
Eagle folder references include the indexed library signature to keep shared
links from silently changing meaning when the source library changes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlencode

from flask import g, has_request_context, url_for
from sqlalchemy import text

from src.file_handler.eagle_integration import get_eagle_catalog_folders, _extract_folder_ids

from .query import _json


@dataclass(frozen=True)
class BrowseTarget:
    kind: str
    reference: str
    library: str = ""

    @classmethod
    def parse(cls, value: str) -> "BrowseTarget":
        kind, separator, reference = str(value).partition(":")
        if not separator or kind not in {"tag", "person", "eagle_folder"} or not reference:
            raise ValueError("不支援的瀏覽入口。")
        if len(value) > 1000 or any(ord(char) < 32 for char in value):
            raise ValueError("瀏覽入口格式不正確。")
        if kind == "eagle_folder":
            library, separator, reference = reference.partition(":")
            if not separator or not reference or not re.fullmatch(r"[0-9a-f]{64}|legacy", library):
                raise ValueError("EAGLE 資料夾入口缺少有效的資源庫識別。")
            return cls(kind, reference, library)
        return cls(kind, reference)

    @property
    def key(self) -> str:
        reference = f"{self.library}:{self.reference}" if self.library else self.reference
        return f"{self.kind}:{reference}"


class BrowseTargetService:
    def __init__(self, database):
        self.database = database

    def eagle_library(self) -> str:
        from .eagle_library import EagleLibraryService

        primary = EagleLibraryService(self.database).primary()
        if primary:
            return primary["signature"]
        with self.database.engine.connect() as conn:
            return conn.execute(text(
                "SELECT source_signature FROM catalog_sync_state WHERE source_kind='eagle'"
            )).scalar() or "legacy"

    def _folder_names(self) -> dict[str, str]:
        library = self.eagle_library()
        cache = g.setdefault("catalog_folder_names", {}) if has_request_context() else {}
        cache_key = (str(self.database.path), library)
        if cache_key in cache:
            return cache[cache_key]
        with self.database.engine.connect() as conn:
            names = {str(_json(row["metadata_json"], {}).get("folder_id")): row["title"]
                     for row in conn.execute(text(
                         "SELECT i.title,o.metadata_json FROM catalog_origins o "
                         "JOIN catalog_items i ON i.id=o.catalog_item_id WHERE o.source_kind='eagle' "
                         "AND o.source_key LIKE :prefix AND o.stale=0 AND i.is_deleted=0"
                     ), {"prefix": f"folder:{library}:%"}).mappings()}
        if names:
            cache[cache_key] = names
            return names
        # Names are optional display metadata. Membership always comes from the
        # indexed origins, so unavailable Eagle never breaks a Catalog query.
        try:
            observed, names = get_eagle_catalog_folders()
        except Exception:
            cache[cache_key] = {}
            return {}
        cache[cache_key] = names if observed == library else {}
        return cache[cache_key]

    @staticmethod
    def entry(target: BrowseTarget, label: str, sources=()) -> dict:
        pairs = [("scope", "all"), *(("source", source) for source in sources), ("target", target.key)]
        return {"kind": target.kind, "target": target.key, "label": label,
                "url": url_for("catalog.navigator_page") + "?" + urlencode(pairs)}

    def describe(self, value: str) -> dict:
        target = BrowseTarget.parse(value)
        if target.kind == "eagle_folder":
            if target.library != self.eagle_library():
                raise LookupError(value)
            return {"kind": target.kind, "target": target.key,
                    "label": self._folder_names().get(target.reference, target.reference),
                    "prefix": "EAGLE 資料夾"}
        table, column, prefix = (
            ("catalog_tags", "name", "標籤") if target.kind == "tag"
            else ("people", "display_name", "人物")
        )
        with self.database.engine.connect() as conn:
            row = conn.execute(text(f"SELECT {column} AS label FROM {table} WHERE id=:id"),
                               {"id": target.reference}).mappings().first()
        if row is None:
            raise LookupError(value)
        return {"kind": target.kind, "target": target.key,
                "label": row["label"] or "未命名人物", "prefix": prefix}

    def predicate(self, value: str, *, sources, eagle_allowed: bool) -> tuple[str, dict]:
        target = BrowseTarget.parse(value)
        params = {"target_id": target.reference}
        if target.kind == "tag":
            return ("EXISTS (SELECT 1 FROM catalog_item_tags bt "
                    "WHERE bt.catalog_item_id=i.id AND bt.tag_id=:target_id)", params)
        if target.kind == "person":
            return ("EXISTS (SELECT 1 FROM item_people bp "
                    "WHERE bp.catalog_item_id=i.id AND bp.person_id=:target_id)", params)
        if not eagle_allowed or "eagle" not in sources or target.library != self.eagle_library():
            return "0=1", {}
        return ("""
            EXISTS (
                SELECT 1 FROM catalog_origins bo,
                json_each(CASE WHEN json_valid(bo.metadata_json)
                          THEN CASE WHEN json_type(bo.metadata_json,'$.folders')='array'
                               THEN json_extract(bo.metadata_json,'$.folders') ELSE '[]' END
                          ELSE '[]' END) bf
                WHERE bo.catalog_item_id=i.id AND bo.source_kind='eagle' AND bo.stale=0
                  AND CASE WHEN bf.type='text' THEN bf.value
                           WHEN bf.type='object' THEN COALESCE(
                               json_extract(bf.value,'$.id'),json_extract(bf.value,'$.folderId'),
                               json_extract(bf.value,'$.folder_id')) END=:target_id
            )
        """, params)

    def for_item(self, item_id: str, *, sources, eagle_allowed: bool) -> list[dict]:
        entries = []
        with self.database.engine.connect() as conn:
            tags = conn.execute(text(
                "SELECT DISTINCT t.id,t.name FROM catalog_tags t JOIN catalog_item_tags it "
                "ON it.tag_id=t.id WHERE it.catalog_item_id=:item ORDER BY t.name"
            ), {"item": item_id}).mappings()
            entries.extend(self.entry(BrowseTarget("tag", row["id"]), row["name"], sources) for row in tags)
            people = conn.execute(text(
                "SELECT DISTINCT p.id,p.display_name FROM people p JOIN item_people ip "
                "ON ip.person_id=p.id WHERE ip.catalog_item_id=:item ORDER BY p.display_name,p.id"
            ), {"item": item_id}).mappings()
            entries.extend(self.entry(BrowseTarget("person", row["id"]),
                                      row["display_name"] or "未命名人物", sources) for row in people)
            origins = list(conn.execute(text(
                "SELECT metadata_json FROM catalog_origins WHERE catalog_item_id=:item "
                "AND source_kind='eagle' AND stale=0"
            ), {"item": item_id}).scalars()) if eagle_allowed and "eagle" in sources else []
        folder_ids = list(dict.fromkeys(folder_id for raw in origins
                         for folder_id in _extract_folder_ids(_json(raw, {}).get("folders"))))
        if folder_ids:
            library, names = self.eagle_library(), self._folder_names()
            entries.extend(self.entry(BrowseTarget("eagle_folder", folder_id, library),
                                      names.get(folder_id, folder_id), sources) for folder_id in folder_ids)
        return entries

    def for_origin(self, source: str, key: str, *, sources) -> list[dict]:
        from .browse import CatalogService
        from .query import CatalogQuery

        if source not in sources:
            return []
        with self.database.engine.connect() as conn:
            item_id = conn.execute(text(
                "SELECT catalog_item_id FROM catalog_origins WHERE source_kind=:source "
                "AND (source_key=:key OR source_path=:key) AND stale=0 LIMIT 1"
            ), {"source": source, "key": key}).scalar()
        if not item_id:
            return []
        catalog = CatalogService(self.database)
        if not catalog.matching_ids(CatalogQuery.create(sources=sources), [item_id]):
            return []
        return self.for_item(item_id, sources=sources, eagle_allowed=catalog._eagle_allowed())
