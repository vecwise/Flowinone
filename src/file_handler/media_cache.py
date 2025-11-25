"""Thumbnail cache and bookmark thumbnail helpers."""

import html
import json
import os
import re
import sqlite3
import hashlib
from typing import Optional, Tuple
from urllib.parse import urlparse

import requests
from config import SPECIAL_THUMBNAIL_DOMAINS

from .paths import _build_file_route


CACHE_DATA_DIR = "data"
THUMBNAIL_CACHE_DIR = os.path.join(CACHE_DATA_DIR, "thumbnails")
THUMBNAIL_CACHE_DB = os.path.join(CACHE_DATA_DIR, "cache.db")

_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9"
}

_CACHE_INITIALISED = False


def _ensure_cache_setup():
    global _CACHE_INITIALISED
    if _CACHE_INITIALISED:
        return
    os.makedirs(CACHE_DATA_DIR, exist_ok=True)
    os.makedirs(THUMBNAIL_CACHE_DIR, exist_ok=True)
    conn = sqlite3.connect(THUMBNAIL_CACHE_DB)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS media_items (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                original_url TEXT,
                title TEXT,
                media_type TEXT,
                sub_type TEXT,
                metadata TEXT,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS thumbnails (
                media_id TEXT PRIMARY KEY REFERENCES media_items(id) ON DELETE CASCADE,
                local_path TEXT NOT NULL,
                fetched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                source TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_media_items_source ON media_items(source)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_media_items_type ON media_items(media_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_media_items_url ON media_items(original_url)")
    finally:
        conn.commit()
        conn.close()
    _CACHE_INITIALISED = True


def _get_cache_connection():
    _ensure_cache_setup()
    conn = sqlite3.connect(THUMBNAIL_CACHE_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _compute_media_id(source: str, identifier: str) -> str:
    base = f"{source}|{identifier}".encode("utf-8", "ignore")
    return hashlib.sha1(base).hexdigest()


def _register_media_item(media_id: str, source: str, original_url: str, title: str,
                         media_type: str, sub_type: Optional[str] = None, extra_metadata: Optional[dict] = None) -> None:
    metadata_json = json.dumps(extra_metadata, ensure_ascii=False) if extra_metadata else None
    with _get_cache_connection() as conn:
        conn.execute(
            """
            INSERT INTO media_items (id, source, original_url, title, media_type, sub_type, metadata, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                source=excluded.source,
                original_url=excluded.original_url,
                title=excluded.title,
                media_type=excluded.media_type,
                sub_type=excluded.sub_type,
                metadata=excluded.metadata,
                updated_at=CURRENT_TIMESTAMP
            """,
            (media_id, source, original_url, title, media_type, sub_type, metadata_json)
        )
        conn.commit()


def _get_media_sub_type(media_id: str) -> Optional[str]:
    with _get_cache_connection() as conn:
        cur = conn.execute("SELECT sub_type FROM media_items WHERE id=?", (media_id,))
        row = cur.fetchone()
    return row[0] if row and row[0] else None


def _get_cached_thumbnail_route(media_id: str) -> Optional[str]:
    with _get_cache_connection() as conn:
        cur = conn.execute("SELECT local_path FROM thumbnails WHERE media_id=?", (media_id,))
        row = cur.fetchone()
    if not row:
        return None
    local_path = row[0]
    if not local_path or not os.path.isfile(local_path):
        return None
    return _build_file_route(local_path, "external")


def _infer_extension(content_type: str | None, url: str | None) -> str:
    if content_type:
        content_type = content_type.lower()
        if "jpeg" in content_type or "jpg" in content_type:
            return "jpg"
        if "png" in content_type:
            return "png"
        if "webp" in content_type:
            return "webp"
        if "gif" in content_type:
            return "gif"
    if url:
        path = urlparse(url).path
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        if ext in {"jpg", "jpeg", "png", "gif", "webp"}:
            return "jpg" if ext == "jpeg" else ext
    return "jpg"


def _download_image(url: str) -> Tuple[Optional[bytes], Optional[str]]:
    if not url:
        return None, None
    try:
        resp = requests.get(
            url,
            timeout=8,
            headers=_DEFAULT_HEADERS,
            stream=True
        )
        if resp.status_code == 200 and resp.content:
            return resp.content, resp.headers.get("Content-Type")
    except requests.RequestException:
        return None, None
    return None, None


def _store_thumbnail_bytes(media_id: str, image_bytes: bytes, content_type: Optional[str],
                           source_tag: Optional[str], origin_url: Optional[str]) -> str:
    extension = _infer_extension(content_type, origin_url)
    filename = f"{media_id}.{extension}"
    os.makedirs(THUMBNAIL_CACHE_DIR, exist_ok=True)
    abs_path = os.path.abspath(os.path.join(THUMBNAIL_CACHE_DIR, filename))
    with open(abs_path, "wb") as fh:
        fh.write(image_bytes)
    with _get_cache_connection() as conn:
        conn.execute(
            """
            INSERT INTO thumbnails (media_id, local_path, fetched_at, source)
            VALUES (?, ?, CURRENT_TIMESTAMP, ?)
            ON CONFLICT(media_id) DO UPDATE SET
                local_path=excluded.local_path,
                fetched_at=CURRENT_TIMESTAMP,
                source=excluded.source
            """,
            (media_id, abs_path, source_tag)
        )
        conn.commit()
    return _build_file_route(abs_path, "external")


def _extract_youtube_id(url):
    if not url:
        return None
    if "youtube.com/watch" in url and "v=" in url:
        return url.split("v=")[1].split("&")[0]
    if "youtu.be/" in url:
        return url.split("youtu.be/")[1].split("?")[0]
    return None


def _get_youtube_thumbnail(video_id):
    if not video_id:
        return None
    return f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"


def _fetch_page(url: str) -> Optional[str]:
    if not url:
        return None
    try:
        response = requests.get(url, timeout=6, headers=_DEFAULT_HEADERS)
        if response.status_code == 200:
            return response.text
    except requests.RequestException:
        return None
    return None


def _fetch_og_image(url):
    html_text = _fetch_page(url)
    if not html_text:
        return None
    match = re.search(
        r"<meta[^>]+property=['\"]og:image['\"][^>]*content=['\"]([^'\"]+)",
        html_text,
        re.IGNORECASE
    )
    if match:
        return html.unescape(match.group(1))
    return None


def _get_special_site_thumbnail(url):
    domain = urlparse(url).netloc.lower()
    for site in SPECIAL_THUMBNAIL_DOMAINS:
        if site in domain:
            return _fetch_og_image(url)
    return None


def _cache_thumbnail_for_bookmark(url: str, title: str, folder_info: Optional[dict] = None) -> Tuple[Optional[str], Optional[str]]:
    identifier = url or title or "bookmark"
    media_id = _compute_media_id("bookmark", identifier)
    metadata = folder_info or {}
    video_id = _extract_youtube_id(url)
    sub_type = "youtube" if video_id else None

    _register_media_item(
        media_id,
        source="bookmark",
        original_url=url,
        title=title,
        media_type="bookmark",
        sub_type=sub_type,
        extra_metadata=metadata
    )

    cached_route = _get_cached_thumbnail_route(media_id)
    if cached_route:
        return cached_route, _get_media_sub_type(media_id)

    thumbnail_url = _get_youtube_thumbnail(video_id) if video_id else None
    if not thumbnail_url:
        special_thumb = _get_special_site_thumbnail(url)
        if special_thumb:
            thumbnail_url = special_thumb
            sub_type = sub_type or "special"

    if not thumbnail_url:
        return None, sub_type

    image_bytes, content_type = _download_image(thumbnail_url)
    if not image_bytes:
        return None, sub_type

    _register_media_item(
        media_id,
        source="bookmark",
        original_url=url,
        title=title,
        media_type="bookmark",
        sub_type=sub_type,
        extra_metadata=metadata
    )

    route = _store_thumbnail_bytes(media_id, image_bytes, content_type, sub_type or "bookmark", thumbnail_url)
    return route, sub_type


def cache_thumbnail_for_bookmark(url: str, title: str, folder_info: Optional[dict] = None) -> Tuple[Optional[str], Optional[str]]:
    return _cache_thumbnail_for_bookmark(url, title, folder_info)


def get_cached_thumbnail_route(media_id: str) -> Optional[str]:
    return _get_cached_thumbnail_route(media_id)


def register_media_item(media_id: str, source: str, original_url: str, title: str,
                        media_type: str, sub_type: Optional[str] = None, extra_metadata: Optional[dict] = None) -> None:
    _register_media_item(media_id, source, original_url, title, media_type, sub_type, extra_metadata)


def get_media_sub_type(media_id: str) -> Optional[str]:
    return _get_media_sub_type(media_id)


def extract_youtube_id(url: str) -> Optional[str]:
    return _extract_youtube_id(url)


__all__ = [
    "CACHE_DATA_DIR",
    "cache_thumbnail_for_bookmark",
    "get_cached_thumbnail_route",
    "register_media_item",
    "get_media_sub_type",
    "extract_youtube_id",
]
