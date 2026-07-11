"""Explainable item relations, generated Collections, and related Collection scores."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from itertools import combinations
from typing import Any

from sqlalchemy import text

from src.flowinone.resource_library.curation import CollectionService
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
        self.collections = CollectionService(database)

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

    def generate_collections(self, *, minimum_size: int = 3, maximum_size: int = 100, limit: int = 20) -> list[dict[str, Any]]:
        """Create draft snapshots from connected metadata-similarity components."""
        with self.database.engine.connect() as conn:
            edges = list(conn.execute(text("SELECT source_item_id,target_item_id,score FROM item_relations WHERE relation_type='metadata_similar' AND score>=0.25 ORDER BY score DESC")).mappings())
        parent: dict[str, str] = {}
        def find(value: str) -> str:
            parent.setdefault(value, value)
            while parent[value] != value:
                parent[value] = parent[parent[value]]
                value = parent[value]
            return value
        def union(left: str, right: str) -> None:
            a, b = find(left), find(right)
            if a != b:
                parent[b] = a
        for edge in edges:
            union(edge["source_item_id"], edge["target_item_id"])
        groups: dict[str, list[str]] = defaultdict(list)
        for item_id in parent:
            groups[find(item_id)].append(item_id)
        generated: list[dict[str, Any]] = []
        for ids in sorted(groups.values(), key=len, reverse=True):
            if len(ids) < minimum_size or len(generated) >= limit:
                continue
            ids = ids[:maximum_size]
            items = [self.catalog.get(item_id) for item_id in ids]
            tag_counts: dict[str, int] = defaultdict(int)
            for item in items:
                for tag in item.get("tags") or []:
                    tag_counts[tag] += 1
            top_tags = sorted(tag_counts, key=lambda value: (-tag_counts[value], value))[:3]
            title = " · ".join(top_tags) if top_tags else f"Generated cluster {len(generated) + 1}"
            collection = self.collections.create(
                title, f"由 {self.version} 產生的 {len(items)} 項草稿。",
                membership_mode="generated", lifecycle_status="draft",
                generation={"algorithm": self.version, "top_tags": top_tags},
            )
            for position, item in enumerate(items):
                self.collections.add_item(
                    collection["id"], source_kind="catalog", source_id=item["id"],
                    catalog_item_id=item["id"], title=item["title"],
                    url=item.get("primary_detail_uri") or item.get("original_url") or "",
                    thumbnail=item.get("thumbnail_ref") or "", membership_source="cluster",
                    reason={"algorithm": self.version, "top_tags": top_tags},
                )
            generated.append(self.collections.get(collection["id"]))
        self.rebuild_collection_relations()
        return generated

    def rebuild_collection_relations(self) -> int:
        with self.database.engine.connect() as conn:
            rows = list(conn.execute(text("SELECT collection_id,catalog_item_id FROM collection_items WHERE catalog_item_id IS NOT NULL")).mappings())
            tag_rows = list(conn.execute(text("SELECT ci.collection_id,t.normalized_name FROM collection_items ci JOIN catalog_item_tags it ON it.catalog_item_id=ci.catalog_item_id JOIN catalog_tags t ON t.id=it.tag_id GROUP BY ci.collection_id,t.normalized_name")).mappings())
        members: dict[str, set[str]] = defaultdict(set)
        collection_tags: dict[str, set[str]] = defaultdict(set)
        for row in rows:
            members[row["collection_id"]].add(row["catalog_item_id"])
        for row in tag_rows:
            collection_tags[row["collection_id"]].add(row["normalized_name"])
        relations: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
        for left, right in combinations(sorted(members), 2):
            union = members[left] | members[right]
            overlap = members[left] & members[right]
            if overlap and union:
                score = len(overlap) / len(union)
                relations[(left, right)] = (score, {"shared_items": len(overlap), "related_item_edges": 0})
            shared_tags = collection_tags[left] & collection_tags[right]
            tag_union = collection_tags[left] | collection_tags[right]
            if shared_tags and tag_union:
                tag_score = len(shared_tags) / len(tag_union)
                if tag_score >= 0.1:
                    previous = relations.get((left, right), (0.0, {}))[0]
                    relations[(left, right)] = (
                        max(previous, tag_score),
                        {"shared_items": len(overlap), "shared_tags": sorted(shared_tags)[:8], "related_item_edges": 0},
                    )
        with self.database.engine.connect() as conn:
            cross_edges = list(
                conn.execute(
                    text(
                        """
                        SELECT a.collection_id AS left_id,b.collection_id AS right_id,
                               AVG(r.score) AS score,COUNT(*) AS edge_count
                        FROM collection_items a
                        JOIN item_relations r ON r.source_item_id=a.catalog_item_id
                        JOIN collection_items b ON b.catalog_item_id=r.target_item_id
                        WHERE a.collection_id < b.collection_id
                        GROUP BY a.collection_id,b.collection_id
                        HAVING COUNT(*)>=2 AND AVG(r.score)>=0.25
                        """
                    )
                ).mappings()
            )
        for edge in cross_edges:
            key = (edge["left_id"], edge["right_id"])
            previous = relations.get(key, (0.0, {}))[0]
            relations[key] = (
                max(previous, float(edge["score"])),
                {"shared_items": len(members[key[0]] & members[key[1]]), "related_item_edges": int(edge["edge_count"])},
            )
        now = utc_now_text()
        with self.database.engine.begin() as conn:
            conn.execute(text("DELETE FROM collection_relations WHERE relation_type='member_overlap'"))
            for (left, right), (score, reason) in relations.items():
                for source, target in ((left, right), (right, left)):
                    conn.execute(text("INSERT INTO collection_relations(source_collection_id,target_collection_id,relation_type,score,reason_json,computed_at) VALUES(:source,:target,'member_overlap',:score,:reason,:now)"), {"source": source, "target": target, "score": score, "reason": json.dumps(reason), "now": now})
        return len(relations) * 2

    def related_collections(self, collection_id: str, *, limit: int = 8) -> list[dict[str, Any]]:
        with self.database.engine.connect() as conn:
            rows = list(conn.execute(text("SELECT target_collection_id,score,reason_json FROM collection_relations WHERE source_collection_id=:id ORDER BY score DESC LIMIT :limit"), {"id": collection_id, "limit": limit}).mappings())
        return [{**self.collections.get(row["target_collection_id"]), "similarity_score": row["score"], "similarity_reason": json.loads(row["reason_json"] or "{}")} for row in rows]
