"""Explainable item-to-item relations for renderer recommendations."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from itertools import combinations
from typing import Any

from sqlalchemy import text

from src.flowinone.resource_library.database import ResourceDatabase
from src.flowinone.resource_library.models import utc_now_text

from .service import CatalogService


def _tokens(value: str) -> set[str]:
    return {token.casefold() for token in re.findall(r"[\w\u3400-\u9fff]{3,}", value or "")}


class DiscoveryService:
    """Build bounded, versioned, explainable metadata relations."""

    version = "metadata-v1"

    def __init__(self, database: ResourceDatabase):
        self.database = database
        self.catalog = CatalogService(database)

    def rebuild_item_relations(self, *, limit: int = 20_000) -> dict[str, int]:
        with self.database.engine.connect() as conn:
            rows = list(
                conn.execute(
                    text(
                        """
                        SELECT i.id,i.title,
                               COALESCE((SELECT GROUP_CONCAT(t.normalized_name,char(31)) FROM catalog_tags t JOIN catalog_item_tags it ON it.tag_id=t.id WHERE it.catalog_item_id=i.id),'') AS tags,
                               COALESCE((SELECT GROUP_CONCAT(DISTINCT source_kind) FROM catalog_origins o WHERE o.catalog_item_id=i.id AND stale=0),'') AS sources
                        FROM catalog_items i WHERE i.is_deleted=0 LIMIT :limit
                        """
                    ),
                    {"limit": max(1, min(limit, 100_000))},
                ).mappings()
            )
        features: dict[str, set[str]] = {}
        buckets: dict[str, list[str]] = defaultdict(list)
        source_sets: dict[str, set[str]] = {}
        for row in rows:
            tags = {tag for tag in str(row["tags"]).split("\x1f") if tag}
            title_tokens = _tokens(row["title"])
            feature_set = {f"tag:{tag}" for tag in tags} | {f"word:{word}" for word in title_tokens}
            features[row["id"]] = feature_set
            source_sets[row["id"]] = {source for source in str(row["sources"]).split(",") if source}
            for feature in feature_set:
                if len(buckets[feature]) < 100:
                    buckets[feature].append(row["id"])
        candidates: set[tuple[str, str]] = set()
        for ids in buckets.values():
            candidates.update(tuple(sorted(pair)) for pair in combinations(ids, 2))
        relations: list[tuple[str, str, float, dict[str, Any]]] = []
        for left, right in candidates:
            shared = features[left] & features[right]
            union = features[left] | features[right]
            if not shared or not union:
                continue
            jaccard = len(shared) / len(union)
            same_source = bool(source_sets[left] & source_sets[right])
            score = min(1.0, jaccard + (0.1 if same_source else 0.0))
            if score < 0.25:
                continue
            relations.append((left, right, score, {"shared": sorted(shared)[:8], "same_source": same_source}))
        # Keep only the strongest bounded neighborhood per item. Common folder
        # tags can otherwise produce hundreds of thousands of low-value edges.
        neighborhoods: dict[str, list[tuple[str, float, dict[str, Any]]]] = defaultdict(list)
        for left, right, score, reason in relations:
            neighborhoods[left].append((right, score, reason))
            neighborhoods[right].append((left, score, reason))
        bounded = {
            source: sorted(values, key=lambda value: (-value[1], value[0]))[:12]
            for source, values in neighborhoods.items()
        }
        now = utc_now_text()
        inserted = 0
        with self.database.engine.begin() as conn:
            conn.execute(text("DELETE FROM item_relations WHERE algorithm_version=:version"), {"version": self.version})
            for source, values in bounded.items():
                for target, score, reason in values:
                    conn.execute(
                        text("INSERT INTO item_relations(source_item_id,target_item_id,relation_type,score,reason_json,algorithm_version,computed_at) VALUES(:source,:target,'metadata_similar',:score,:reason,:version,:now)"),
                        {"source": source, "target": target, "score": score, "reason": json.dumps(reason, ensure_ascii=False), "version": self.version, "now": now},
                    )
                    inserted += 1
        return {"items": len(rows), "relations": inserted}

    def related_items(self, item_id: str, *, limit: int = 18) -> list[dict[str, Any]]:
        with self.database.engine.connect() as conn:
            rows = list(
                conn.execute(
                    text("SELECT target_item_id,relation_type,score,reason_json FROM item_relations WHERE source_item_id=:id ORDER BY score DESC LIMIT :limit"),
                    {"id": item_id, "limit": max(1, min(limit, 100))},
                ).mappings()
            )
        output = []
        for row in rows:
            try:
                item = self.catalog.get(row["target_item_id"])
            except LookupError:
                continue
            output.append({**item, "relation_type": row["relation_type"], "score": row["score"], "reason": json.loads(row["reason_json"] or "{}")})
        return output
