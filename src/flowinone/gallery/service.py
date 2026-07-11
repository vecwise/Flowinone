"""Source adapters and query orchestration for the independent Gallery domain."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Iterable

from src.file_handler.chrome_bookmarks import iter_chrome_bookmark_records
from src.file_handler.eagle_integration import get_eagle_stream_items
from src.file_handler.item_db import fetch_items
from src.file_handler.media_cache import lookup_thumbnail_for_bookmark
from src.file_handler.models import BookmarkError, BookmarkNotFound, ExternalServiceError
from src.file_handler.paths import DEFAULT_THUMBNAIL_ROUTE

from .models import GALLERY_SOURCES, GalleryItem, GalleryPage, GalleryQuery


SourceLoader = Callable[[], Iterable[GalleryItem]]


class InvalidGalleryCursor(ValueError):
    """Raised when a cursor does not belong to the current query."""


def _local_items() -> list[GalleryItem]:
    """Read up to 10k indexed media rows and discard folders/stale paths."""
    output: list[GalleryItem] = []
    offset = 0
    while offset < 10_000:
        payload = fetch_items(limit=1000, offset=offset)
        rows = payload.get("items") or []
        if not rows:
            break
        for sequence, row in enumerate(rows, start=offset):
            media_type = str(row.get("item_type") or "")
            absolute_path = str(row.get("absolute_path") or "")
            if media_type not in {"image", "video"} or not os.path.isfile(absolute_path):
                continue
            output.append(
                GalleryItem(
                    id=f"local:{row['item_id']}",
                    source="local",
                    media_type=media_type,
                    title=str(row.get("name") or "Untitled"),
                    thumbnail_url=str(row.get("thumbnail_route") or DEFAULT_THUMBNAIL_ROUTE),
                    tags=list(row.get("tags") or []),
                    ext=row.get("ext"),
                    created_at=row.get("updated_at"),
                    relative_path=row.get("relative_path"),
                    is_available=True,
                    sequence=sequence,
                )
            )
        offset += len(rows)
        if offset >= int(payload.get("total") or 0):
            break
    return output


def _eagle_items() -> list[GalleryItem]:
    entries = get_eagle_stream_items(offset=0, limit=500)
    output: list[GalleryItem] = []
    for sequence, entry in enumerate(entries):
        raw = entry.to_dict() if hasattr(entry, "to_dict") else dict(entry)
        media_type = str(raw.get("media_type") or "")
        item_id = str(raw.get("id") or "")
        if not item_id or media_type not in {"image", "video"}:
            continue
        output.append(
            GalleryItem(
                id=f"eagle:{item_id}",
                source="eagle",
                media_type=media_type,
                title=str(raw.get("name") or "Untitled"),
                thumbnail_url=str(raw.get("thumbnail_route") or DEFAULT_THUMBNAIL_ROUTE),
                tags=list(raw.get("tags") or []),
                ext=raw.get("ext"),
                relative_path=item_id,
                sequence=sequence,
            )
        )
    return output


def _bookmark_items() -> list[GalleryItem]:
    output: list[GalleryItem] = []
    for sequence, record in enumerate(iter_chrome_bookmark_records()):
        url = str(record.get("url") or "")
        if not url:
            continue
        title = str(record.get("title") or url)
        folder_path = str(record.get("folder_path") or "")
        thumbnail = lookup_thumbnail_for_bookmark(url, title, {"folder_path": folder_path})
        item_id = hashlib.sha1(url.encode("utf-8", "ignore")).hexdigest()
        tags = [part.strip() for part in folder_path.split("/") if part.strip()]
        output.append(
            GalleryItem(
                id=f"bookmarks:{item_id}",
                source="bookmarks",
                media_type="bookmark",
                title=title,
                thumbnail_url=thumbnail.route or DEFAULT_THUMBNAIL_ROUTE,
                tags=tags[-4:],
                ext=thumbnail.sub_type,
                description=folder_path or None,
                original_url=url,
                sequence=sequence,
            )
        )
    return output


class GalleryService:
    """Combine independent sources into one URL-driven, flat browse result."""

    def __init__(
        self,
        source_loaders: dict[str, SourceLoader] | None = None,
        *,
        cache_ttl_seconds: float = 30.0,
    ):
        self.source_loaders = source_loaders or {
            "local": _local_items,
            "eagle": _eagle_items,
            "bookmarks": _bookmark_items,
        }
        self.cache_ttl_seconds = max(0.0, float(cache_ttl_seconds))
        self._source_cache: dict[str, tuple[float, list[GalleryItem]]] = {}
        self._cache_lock = threading.Lock()

    def _load_source(self, source: str, loader: SourceLoader) -> list[GalleryItem]:
        now = time.monotonic()
        with self._cache_lock:
            cached = self._source_cache.get(source)
            if cached and now - cached[0] <= self.cache_ttl_seconds:
                return [replace(item) for item in cached[1]]
        loaded = list(loader())
        with self._cache_lock:
            self._source_cache[source] = (now, loaded)
        return [replace(item) for item in loaded]

    @staticmethod
    def _signature(query: GalleryQuery) -> str:
        payload = {
            "q": query.q,
            "sources": query.sources,
            "type": query.media_type,
            "sort": query.sort,
            "seed": query.seed,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:16]

    @classmethod
    def _decode_cursor(cls, query: GalleryQuery) -> int:
        if not query.cursor:
            return 0
        try:
            padding = "=" * (-len(query.cursor) % 4)
            payload = json.loads(base64.urlsafe_b64decode(query.cursor + padding))
            if payload.get("signature") != cls._signature(query):
                raise InvalidGalleryCursor("cursor 與目前 Gallery 篩選條件不相符")
            return max(0, int(payload.get("offset") or 0))
        except InvalidGalleryCursor:
            raise
        except Exception as exc:
            raise InvalidGalleryCursor("無效的 Gallery cursor") from exc

    @classmethod
    def _encode_cursor(cls, query: GalleryQuery, offset: int) -> str:
        payload = json.dumps(
            {"offset": offset, "signature": cls._signature(query)},
            separators=(",", ":"),
        ).encode("utf-8")
        return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    @staticmethod
    def _search_score(item: GalleryItem, query: str) -> float:
        if not query:
            return 0.0
        terms = [term.casefold() for term in query.split() if term]
        title = item.title.casefold()
        tags = " ".join(item.tags).casefold()
        description = (item.description or "").casefold()
        score = 0.0
        for term in terms:
            score += 6.0 if term in title else 0.0
            score += 3.0 if term in tags else 0.0
            score += 1.0 if term in description else 0.0
            score += 0.5 if term in item.source else 0.0
        return score

    def list_items(self, query: GalleryQuery) -> GalleryPage:
        offset = self._decode_cursor(query)
        candidates: list[GalleryItem] = []
        source_counts = {source: 0 for source in GALLERY_SOURCES}
        source_errors: dict[str, str] = {}

        for source in query.sources:
            loader = self.source_loaders.get(source)
            if loader is None:
                continue
            try:
                loaded = self._load_source(source, loader)
            except (BookmarkNotFound, BookmarkError, ExternalServiceError, OSError) as exc:
                source_errors[source] = "來源暫時不可用，請確認來源應用程式或設定"
                continue
            except Exception as exc:  # Source failures must not blank the other sources.
                source_errors[source] = "來源暫時不可用，請稍後重試"
                continue
            source_counts[source] = len(loaded)
            candidates.extend(loaded)

        if query.media_type:
            candidates = [item for item in candidates if item.media_type == query.media_type]

        if query.q:
            for item in candidates:
                item.relevance = self._search_score(item, query.q)
            candidates = [item for item in candidates if item.relevance > 0]

        if query.sort == "random":
            seed = query.seed or 1
            candidates.sort(
                key=lambda item: hashlib.sha256(f"{seed}:{item.id}".encode()).hexdigest()
            )
        elif query.sort == "title":
            candidates.sort(key=lambda item: (item.title.casefold(), item.id))
        elif query.sort == "relevance" and query.q:
            candidates.sort(key=lambda item: (-item.relevance, item.title.casefold(), item.id))
        elif query.sort == "newest":
            candidates.sort(key=lambda item: (item.created_at or "", -item.sequence), reverse=True)
        else:
            candidates.sort(key=lambda item: (item.created_at or "", -item.sequence), reverse=True)

        total = len(candidates)
        items = candidates[offset : offset + query.limit]
        next_offset = offset + len(items)
        next_cursor = self._encode_cursor(query, next_offset) if next_offset < total else None
        return GalleryPage(
            items=items,
            total=total,
            next_cursor=next_cursor,
            query=query,
            source_counts=source_counts,
            source_errors=source_errors,
        )


__all__ = ["GalleryService", "InvalidGalleryCursor"]
