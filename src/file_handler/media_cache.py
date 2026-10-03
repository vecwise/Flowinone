"""Bookmark thumbnail lookup through the durable thumbnail queue.

Page requests call only SQLite and the local filesystem. Remote work is always
delegated to the durable thumbnail queue.
"""

from __future__ import annotations

from typing import Optional

from .thumbnails.store import (
    PRIORITY_VISIBLE,
    ThumbnailLookup,
    get_thumbnail_store,
)


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


__all__ = ["lookup_thumbnail_for_bookmark"]
