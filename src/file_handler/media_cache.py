"""Compatibility facade for the queued bookmark thumbnail system.

Page requests call only SQLite and the local filesystem. Remote work is always
delegated to the durable thumbnail queue.
"""

from __future__ import annotations

from typing import Optional, Tuple

from .thumbnails.store import (
    DEFAULT_CACHE_DB as THUMBNAIL_CACHE_DB,
    DEFAULT_CACHE_DIR as THUMBNAIL_CACHE_DIR,
    PRIORITY_VISIBLE,
    ThumbnailLookup,
    compute_media_id,
    get_thumbnail_store,
)
from .thumbnails.urls import extract_youtube_id


CACHE_DATA_DIR = "data"


def lookup_thumbnail_for_bookmark(
    url: str,
    title: str,
    folder_info: Optional[dict] = None,
) -> ThumbnailLookup:
    return get_thumbnail_store().register_bookmark(
        url,
        title,
        folder_info,
        priority=PRIORITY_VISIBLE,
        enqueue_missing=True,
    )


def cache_thumbnail_for_bookmark(
    url: str,
    title: str,
    folder_info: Optional[dict] = None,
) -> Tuple[Optional[str], Optional[str]]:
    lookup = lookup_thumbnail_for_bookmark(url, title, folder_info)
    return lookup.route, lookup.sub_type


def get_cached_thumbnail_route(media_id: str) -> Optional[str]:
    path = get_thumbnail_store().get_thumbnail_path(media_id)
    return get_thumbnail_store().thumbnail_route(media_id) if path else None


def register_media_item(
    media_id: str,
    source: str,
    original_url: str,
    title: str,
    media_type: str,
    sub_type: Optional[str] = None,
    extra_metadata: Optional[dict] = None,
) -> None:
    """Retained for older callers; bookmark registration uses canonical IDs."""
    if source == "bookmark" and media_type == "bookmark":
        get_thumbnail_store().register_bookmark(
            original_url,
            title,
            extra_metadata,
            enqueue_missing=False,
        )
        return
    metadata = extra_metadata or {}
    store = get_thumbnail_store()
    with store.connect() as conn:
        conn.execute(
            """
            INSERT INTO media_items (id, source, original_url, title, media_type, sub_type, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                source=excluded.source,
                original_url=excluded.original_url,
                title=excluded.title,
                media_type=excluded.media_type,
                sub_type=excluded.sub_type,
                metadata=excluded.metadata,
                updated_at=CURRENT_TIMESTAMP
            """,
            (media_id, source, original_url, title, media_type, sub_type, str(metadata)),
        )
        conn.commit()


def get_media_sub_type(media_id: str) -> Optional[str]:
    store = get_thumbnail_store()
    with store.connect() as conn:
        row = conn.execute("SELECT sub_type FROM media_items WHERE id=?", (media_id,)).fetchone()
    return str(row["sub_type"]) if row and row["sub_type"] else None


def _compute_media_id(source: str, identifier: str) -> str:
    return compute_media_id(source, identifier)


__all__ = [
    "CACHE_DATA_DIR",
    "THUMBNAIL_CACHE_DB",
    "THUMBNAIL_CACHE_DIR",
    "cache_thumbnail_for_bookmark",
    "extract_youtube_id",
    "get_cached_thumbnail_route",
    "get_media_sub_type",
    "lookup_thumbnail_for_bookmark",
    "register_media_item",
]
