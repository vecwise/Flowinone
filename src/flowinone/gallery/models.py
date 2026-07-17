"""Read models and validated query state for the Gallery domain."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable


GALLERY_SOURCES = ("local", "eagle", "bookmarks")
GALLERY_MEDIA_TYPES = ("image", "video", "bookmark")
GALLERY_SORTS = (
    "newest", "recently_added", "relevance", "title", "random",
    "most_viewed", "recently_viewed", "favorites", "similar",
)
GALLERY_VIEWS = ("grid", "compact")


def _allowed(values: Iterable[str], allowlist: tuple[str, ...]) -> tuple[str, ...]:
    selected: list[str] = []
    for raw in values:
        for value in str(raw).split(","):
            normalized = value.strip().lower()
            if normalized in allowlist and normalized not in selected:
                selected.append(normalized)
    return tuple(selected)


@dataclass(frozen=True)
class GalleryQuery:
    """URL-serializable Gallery query with allowlisted values only."""

    q: str = ""
    sources: tuple[str, ...] = GALLERY_SOURCES
    media_type: str = ""
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
    view: str = "grid"
    limit: int = 48
    cursor: str = ""

    @classmethod
    def create(
        cls,
        *,
        q: Any = "",
        sources: Iterable[str] = (),
        media_type: Any = "",
        tags: Iterable[str] = (),
        tag_mode: Any = "any",
        favorite: Any = False,
        unviewed: Any = False,
        duration_min: Any = None,
        duration_max: Any = None,
        added_from: Any = "",
        added_to: Any = "",
        sort: Any = "recently_added",
        seed: Any = 0,
        view: Any = "grid",
        limit: Any = 48,
        cursor: Any = "",
    ) -> "GalleryQuery":
        normalized_sources = _allowed(sources, GALLERY_SOURCES) or GALLERY_SOURCES
        normalized_type = str(media_type or "").strip().lower()
        if normalized_type not in GALLERY_MEDIA_TYPES:
            normalized_type = ""
        normalized_sort = str(sort or "recently_added").strip().lower()
        if normalized_sort not in GALLERY_SORTS:
            normalized_sort = "recently_added"
        normalized_view = str(view or "grid").strip().lower()
        if normalized_view not in GALLERY_VIEWS:
            normalized_view = "grid"
        try:
            normalized_seed = max(0, int(seed or 0))
        except (TypeError, ValueError):
            normalized_seed = 0
        try:
            normalized_limit = max(1, min(int(limit or 48), 96))
        except (TypeError, ValueError):
            normalized_limit = 48
        normalized_tags = tuple(
            dict.fromkeys(
                value.strip().casefold()
                for raw in tags
                for value in str(raw).split(",")
                if value.strip()
            )
        )
        def optional_number(value):
            try:
                return float(value) if value not in (None, "") else None
            except (TypeError, ValueError):
                return None
        return cls(
            q=str(q or "").strip()[:300],
            sources=normalized_sources,
            media_type=normalized_type,
            tags=normalized_tags,
            tag_mode="all" if str(tag_mode).lower() == "all" else "any",
            favorite=favorite in (True, 1, "1", "true", "yes"),
            unviewed=unviewed in (True, 1, "1", "true", "yes"),
            duration_min=optional_number(duration_min),
            duration_max=optional_number(duration_max),
            added_from=str(added_from or "")[:40],
            added_to=str(added_to or "")[:40],
            sort=normalized_sort,
            seed=normalized_seed,
            view=normalized_view,
            limit=normalized_limit,
            cursor=str(cursor or "").strip()[:500],
        )

    def public_dict(self) -> dict[str, Any]:
        return {
            "q": self.q,
            "sources": list(self.sources),
            "type": self.media_type or None,
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
            "view": self.view,
            "limit": self.limit,
            "cursor": self.cursor or None,
        }


@dataclass
class GalleryItem:
    """A flat browse item; folder hierarchy is metadata, never an item."""

    id: str
    source: str
    media_type: str
    title: str
    thumbnail_url: str
    tags: list[str] = field(default_factory=list)
    ext: str | None = None
    created_at: str | None = None
    description: str | None = None
    relative_path: str | None = None
    original_url: str | None = None
    detail_uri: str | None = None
    duration_seconds: float | None = None
    favorite: bool = False
    open_count: int = 0
    is_available: bool = True
    sequence: int = 0
    relevance: float = 0.0

    def public_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("relative_path", None)
        payload.pop("sequence", None)
        payload.pop("relevance", None)
        payload["source"] = {
            "key": self.source,
            "name": {"local": "本機", "eagle": "EAGLE", "bookmarks": "書籤"}[self.source],
        }
        return payload


@dataclass
class GalleryPage:
    items: list[GalleryItem]
    total: int
    next_cursor: str | None
    query: GalleryQuery
    source_counts: dict[str, int]
    source_errors: dict[str, str]


__all__ = [
    "GALLERY_MEDIA_TYPES",
    "GALLERY_SORTS",
    "GALLERY_SOURCES",
    "GALLERY_VIEWS",
    "GalleryItem",
    "GalleryPage",
    "GalleryQuery",
]
