"""Validated Catalog filter values and their stable query signatures."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

CATALOG_SOURCES = ("local", "eagle", "bookmarks", "resources")
CATALOG_SORTS = (
    "newest", "recently_added", "relevance", "title", "random",
    "most_viewed", "recently_viewed", "favorites",
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
    folder: str = ""
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
            folder=str(values.get("folder") or "").strip()[:500],
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

    def filter_signature(self) -> str:
        """Return the cache key for metadata that is independent of paging.

        Exact totals depend on every filtering input, while the current
        availability facets intentionally remain catalog-wide.  Both are
        unchanged when a user advances a cursor or switches a
        presentation-only sort.  Keeping those dimensions out of the key lets
        successive Navigator pages reuse metadata safely without crossing
        filters.
        """
        payload = self.public_dict()
        for field in ("cursor", "limit", "sort", "seed"):
            payload.pop(field, None)
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:20]

    def public_dict(self) -> dict[str, Any]:
        return {
            "q": self.q,
            "scope": self.scope,
            "sources": list(self.sources),
            "type": self.item_type or None,
            "folder": self.folder or None,
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

