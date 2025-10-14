import html
import json
import os
import random
import re
import sqlite3
import hashlib
import time
from collections import OrderedDict
from datetime import datetime
from urllib.parse import quote, urlparse
from typing import Optional, Tuple
import mimetypes
import requests
import src.eagle_api as EG
from flask import abort
from config import (
    DB_route_internal,
    DB_route_external,
    CHROME_BOOKMARK_PATH,
    SPECIAL_THUMBNAIL_DOMAINS,
)


IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
VIDEO_EXTENSIONS = {"mp4", "mov", "avi", "mkv", "webm", "m4v"}
DEFAULT_THUMBNAIL_ROUTE = "/static/default_thumbnail.svg"
DEFAULT_VIDEO_THUMBNAIL_ROUTE = "/static/default_video_thumbnail.svg"

CACHE_DATA_DIR = "data"
THUMBNAIL_CACHE_DIR = os.path.join(CACHE_DATA_DIR, "thumbnails")
THUMBNAIL_CACHE_DB = os.path.join(CACHE_DATA_DIR, "cache.db")

_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9"
}


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


_EAGLE_STATUS_CACHE = {"timestamp": 0.0, "value": False}


def is_eagle_available(force: bool = False) -> bool:
    now = time.time()
    if not force and now - _EAGLE_STATUS_CACHE["timestamp"] < 60:
        return _EAGLE_STATUS_CACHE["value"]
    try:
        response = EG.EAGLE_get_library_info()
        available = response.get("status") == "success"
    except Exception:
        available = False
    _EAGLE_STATUS_CACHE.update({"timestamp": now, "value": available})
    return available


def has_chrome_bookmarks() -> bool:
    return os.path.isfile(CHROME_BOOKMARK_PATH)


def has_db_main() -> bool:
    return os.path.isdir(DB_route_external)


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


def _normalize_source(src):
    return "external" if src == "external" else "internal"


def _normalize_slashes(path):
    return path.replace("\\", "/")


def _is_image_file(filename):
    return os.path.splitext(filename)[1].lower().lstrip(".") in IMAGE_EXTENSIONS


def _is_video_file(filename):
    return os.path.splitext(filename)[1].lower().lstrip(".") in VIDEO_EXTENSIONS


def _build_file_route(abs_path, src):
    normalized = _normalize_slashes(abs_path)
    if src == "external":
        return f"/serve_image/{normalized}"
    return f"/{normalized}"

def _build_image_url(rel_path, src):
    normalized_src = _normalize_source(src)
    normalized_path = _normalize_slashes(rel_path or "")
    quoted_path = quote(normalized_path, safe="/")
    query = "?src=external" if normalized_src == "external" else "?src=internal"
    if quoted_path:
        return f"/image/{quoted_path}{query}"
    return f"/image/{query}"


def _build_folder_url(rel_path, src):
    normalized_src = _normalize_source(src)
    normalized_path = _normalize_slashes(rel_path or "")
    quoted_path = quote(normalized_path, safe="/")

    if normalized_src == "external":
        return f"/both/{quoted_path}" if quoted_path else "/"

    if quoted_path:
        return f"/both/{quoted_path}/?src=internal"
    return "/?src=internal"


def _build_video_url(rel_path, src):
    normalized = _normalize_slashes(rel_path)
    quoted_path = quote(normalized, safe="/")
    query = "?src=external" if _normalize_source(src) == "external" else "?src=internal"
    return f"/video/{quoted_path}{query}"


def _find_video_thumbnail(abs_video_path, src):
    base, _ = os.path.splitext(abs_video_path)
    candidates = []
    for ext in IMAGE_EXTENSIONS:
        candidates.append(f"{base}_thumbnail.{ext}")
        candidates.append(f"{base}.{ext}")

    for candidate in candidates:
        if os.path.isfile(candidate):
            return _build_file_route(candidate, src)

    return DEFAULT_VIDEO_THUMBNAIL_ROUTE


def _find_directory_thumbnail(abs_folder_path, src):
    for root, _, files in os.walk(abs_folder_path):
        for file_name in sorted(files):
            abs_file_path = os.path.join(root, file_name)
            if _is_image_file(file_name):
                return _build_file_route(abs_file_path, src)
            if _is_video_file(file_name):
                return _find_video_thumbnail(abs_file_path, src)
    return DEFAULT_THUMBNAIL_ROUTE


def _build_folder_entry(display_name, abs_path, rel_path, src):
    return {
        "name": display_name,
        "thumbnail_route": _find_directory_thumbnail(abs_path, src),
        "url": _build_folder_url(rel_path, src),
        "item_path": os.path.abspath(abs_path),
        "media_type": "folder",
        "ext": None
    }


def _build_image_entry(display_name, abs_path, rel_path, src):
    file_route = _build_file_route(abs_path, src)
    ext = os.path.splitext(display_name)[1].lstrip(".").lower()
    return {
        "name": display_name,
        "thumbnail_route": file_route,
        "url": _build_image_url(rel_path, src),
        "item_path": os.path.abspath(abs_path),
        "media_type": "image",
        "ext": ext or None
    }


def _build_video_entry(display_name, abs_path, rel_path, src):
    ext = os.path.splitext(display_name)[1].lstrip(".").lower()
    return {
        "name": display_name,
        "thumbnail_route": _find_video_thumbnail(abs_path, src),
        "url": _build_video_url(rel_path, src),
        "item_path": os.path.abspath(abs_path),
        "media_type": "video",
        "ext": ext or None
    }


def _collect_directory_entries(base_dir, relative_path, src):
    normalized_src = _normalize_source(src)
    target_dir = os.path.join(base_dir, relative_path) if relative_path else base_dir
    if not os.path.isdir(target_dir):
        abort(404)

    try:
        entries = sorted(os.listdir(target_dir))
    except FileNotFoundError:
        abort(404)

    folders, files = [], []
    for entry in entries:
        if entry.startswith("."):
            continue
        abs_entry = os.path.join(target_dir, entry)
        rel_entry = os.path.relpath(abs_entry, base_dir)
        rel_entry = _normalize_slashes(rel_entry)

        if os.path.isdir(abs_entry):
            folders.append(_build_folder_entry(entry, abs_entry, rel_entry, normalized_src))
        elif _is_image_file(entry):
            files.append(_build_image_entry(entry, abs_entry, rel_entry, normalized_src))
        elif _is_video_file(entry):
            files.append(_build_video_entry(entry, abs_entry, rel_entry, normalized_src))

    return folders + files


def _human_readable_size(num_bytes):
    if num_bytes < 1024:
        return f"{num_bytes} B"
    units = ["KB", "MB", "GB", "TB", "PB"]
    size = float(num_bytes)
    for unit in units:
        size /= 1024.0
        if size < 1024.0 or unit == units[-1]:
            return f"{size:.2f} {unit}"

    return f"{size:.2f} PB"


def _safe_relative_path(path):
    if not path:
        return ""
    normalized = _normalize_slashes(os.path.normpath(path))
    if normalized.startswith("../") or normalized == "..":
        abort(403)
    if os.path.isabs(path):
        abort(403)
    return normalized

def get_all_folders_info(src):
    """
    取得 DB_route 內的所有子資料夾資訊，符合 EAGLE API 格式
    src: internal or external
    """

    normalized_src = _normalize_source(src)
    base_dir = DB_route_external if normalized_src == "external" else DB_route_internal

    if not os.path.isdir(base_dir):
        abort(404)

    metadata = {
        "name": "All Collections",
        "category": "collections",
        "tags": ["collection", "group", "Main"],
        "path": "/" if normalized_src == "external" else "/?src=internal",
        "thumbnail_route": DEFAULT_THUMBNAIL_ROUTE,
        "filesystem_path": os.path.abspath(base_dir)
    }

    data = _collect_directory_entries(base_dir, "", normalized_src)
    return metadata, data

def get_folder_images(folder_path, src=None):
    """
    取得指定資料夾內的所有圖片，符合 EAGLE API 格式
    從任意資料夾（base_dir + folder_path）中取得圖片
    """

    normalized_src = _normalize_source(src)
    safe_folder_path = _safe_relative_path(folder_path)
    base_dir = DB_route_external if normalized_src == "external" else DB_route_internal

    target_dir = os.path.join(base_dir, safe_folder_path) if safe_folder_path else base_dir
    if not os.path.isdir(target_dir):
        abort(404)
    
    metadata = {
        "name": os.path.basename(safe_folder_path.rstrip("/")) if safe_folder_path else os.path.basename(os.path.normpath(base_dir)),
        "category": "folder",
        "tags": [],
        "path": _build_folder_url(safe_folder_path, normalized_src),
        "thumbnail_route": _find_directory_thumbnail(target_dir, normalized_src),
        "filesystem_path": os.path.abspath(target_dir)
    }

    data = _collect_directory_entries(base_dir, safe_folder_path, normalized_src)
    return metadata, data


def get_video_details(video_path, src=None):
    """
    取得影片詳細資訊與播放所需路徑。
    """
    normalized_src = _normalize_source(src)
    safe_video_path = _safe_relative_path(video_path)
    base_dir = DB_route_external if normalized_src == "external" else DB_route_internal
    target_path = os.path.join(base_dir, safe_video_path) if safe_video_path else base_dir

    if not os.path.isfile(target_path) or not _is_video_file(target_path):
        abort(404)

    file_name = os.path.basename(safe_video_path) if safe_video_path else os.path.basename(target_path)
    file_ext = os.path.splitext(file_name)[1].lstrip(".").lower()
    file_size = os.path.getsize(target_path)
    modified_time = datetime.fromtimestamp(os.path.getmtime(target_path))
    thumbnail_route = _find_video_thumbnail(target_path, normalized_src)
    source_url = _build_file_route(target_path, normalized_src)
    mime_type = mimetypes.guess_type(file_name)[0] or "video/mp4"

    parent_relative = _normalize_slashes(os.path.dirname(safe_video_path))
    parent_url = _build_folder_url(parent_relative, normalized_src) if parent_relative else ("/" if normalized_src == "external" else "/?src=internal")

    folder_links = []
    if parent_relative:
        folder_links.append({
            "name": os.path.basename(parent_relative) or parent_relative,
            "url": parent_url
        })
    else:
        root_name = os.path.basename(os.path.normpath(DB_route_external if normalized_src == "external" else DB_route_internal)) or "Root"
        folder_links.append({
            "name": root_name,
            "url": parent_url
        })

    similar_items = _build_local_similar_items(target_path, base_dir, normalized_src, limit=6)

    metadata = {
        "name": file_name,
        "category": "video",
        "tags": [],
        "path": _build_video_url(safe_video_path, normalized_src),
        "thumbnail_route": thumbnail_route,
        "filesystem_path": os.path.abspath(os.path.dirname(target_path)),
        "folders": folder_links,
        "similar": similar_items,
        "ext": file_ext or None
    }

    video_data = {
        "name": file_name,
        "relative_path": safe_video_path,
        "source_url": source_url,
        "thumbnail_route": thumbnail_route,
        "original_url": None,
        "mime_type": mime_type,
        "size_bytes": file_size,
        "size_display": _human_readable_size(file_size),
        "modified_time": modified_time.strftime("%Y-%m-%d %H:%M"),
        "parent_url": parent_url,
        "download_url": source_url,
        "folders": folder_links,
        "ext": file_ext or None
    }

    return metadata, video_data


def get_image_details(image_path, src=None):
    """
    取得圖片詳細資訊與展示所需路徑。
    """
    normalized_src = _normalize_source(src)
    safe_image_path = _safe_relative_path(image_path)
    base_dir = DB_route_external if normalized_src == "external" else DB_route_internal
    target_path = os.path.join(base_dir, safe_image_path) if safe_image_path else base_dir

    if not os.path.isfile(target_path) or not _is_image_file(target_path):
        abort(404)

    file_name = os.path.basename(safe_image_path) if safe_image_path else os.path.basename(target_path)
    file_ext = os.path.splitext(file_name)[1].lstrip(".").lower()
    file_size = os.path.getsize(target_path)
    modified_time = datetime.fromtimestamp(os.path.getmtime(target_path))
    source_url = _build_file_route(target_path, normalized_src)
    mime_type = mimetypes.guess_type(file_name)[0] or "image/jpeg"

    parent_relative = _normalize_slashes(os.path.dirname(safe_image_path))
    parent_url = _build_folder_url(parent_relative, normalized_src) if parent_relative else ("/" if normalized_src == "external" else "/?src=internal")

    folder_links = []
    if parent_relative:
        folder_links.append({
            "name": os.path.basename(parent_relative) or parent_relative,
            "url": parent_url
        })
    else:
        root_name = os.path.basename(os.path.normpath(DB_route_external if normalized_src == "external" else DB_route_internal)) or "Root"
        folder_links.append({
            "name": root_name,
            "url": parent_url
        })

    similar_items = _build_local_similar_items(target_path, base_dir, normalized_src, limit=6)

    metadata = {
        "name": file_name,
        "category": "image",
        "tags": [],
        "path": _build_image_url(safe_image_path, normalized_src),
        "thumbnail_route": source_url,
        "filesystem_path": os.path.abspath(target_path),
        "folders": folder_links,
        "similar": similar_items,
        "ext": file_ext or None
    }

    image_data = {
        "name": file_name,
        "relative_path": safe_image_path,
        "source_url": source_url,
        "thumbnail_route": source_url,
        "original_url": None,
        "mime_type": mime_type,
        "size_bytes": file_size,
        "size_display": _human_readable_size(file_size),
        "modified_time": modified_time.strftime("%Y-%m-%d %H:%M"),
        "parent_url": parent_url,
        "download_url": source_url,
        "folders": folder_links,
        "ext": file_ext or None
    }

    return metadata, image_data

def get_eagle_folders():
    """
    獲取 Eagle API 提供的所有資料夾資訊
    """
    response = EG.EAGLE_get_library_info()
    if response.get("status") != "success":
        abort(500, description=f"Failed to fetch Eagle folders: {response.get('data')}")

    metadata = {
        "name": "All Eagle Folders",
        "category": "collections",
        "tags": ["eagle", "folders"],
        "path": "/EAGLE_folder",
        "thumbnail_route": DEFAULT_THUMBNAIL_ROUTE,
        "filesystem_path": EG.EAGLE_get_current_library_path()
    }

    data = []
    for folder in response.get("data", {}).get("folders", []):
        folder_id = folder.get("id")
        folder_name = folder.get("name", "Unnamed Folder")

        # 取得 Eagle 資料夾內的縮圖
        folder_response = EG.EAGLE_list_items(folders=[folder_id])
        image_items = folder_response.get("data", [])
        image_items.sort(key=lambda x: x.get("name", ""))
        thumbnail_path = f"/serve_image/{EG.EAGLE_get_current_library_path()}/images/{image_items[0]['id']}.info/{image_items[0]['name']}.{image_items[0]['ext']}" if image_items else DEFAULT_THUMBNAIL_ROUTE

        data.append({
            "name": folder_name,
            "id": folder_id,
            "url": f"/EAGLE_folder/{folder_id}/",
            "thumbnail_route": thumbnail_path,
            "item_path": None,
            "media_type": "folder",
            "ext": None
        })

    return metadata, data

def get_eagle_images_by_folderid(eagle_folder_id):
    """
    獲取 Eagle API 提供的指定資料夾內的圖片資訊，符合 EAGLE API 格式
    """
    response = EG.EAGLE_list_items(folders=[eagle_folder_id])
    if response.get("status") != "success":
        abort(500, description=f"Failed to fetch images from Eagle folder: {response.get('data')}")

    # df = EG.EAGLE_get_folders_df()
    # row = df[df["id"] == eagle_folder_id]  ###並沒有recursive地找...
    # if row.empty:
    #     return []
    # folder_name = row.iloc[0]["name"]
    folder_links = []
    current_folder, parent_folder = _get_eagle_folder_context(eagle_folder_id)
    if parent_folder:
        parent_id = parent_folder.get("id")
        parent_name = parent_folder.get("name", parent_id)
        if parent_id:
            folder_links.append({
                "id": parent_id,
                "name": parent_name,
                "url": f"/EAGLE_folder/{parent_id}/"
            })

    folder_name = current_folder.get("name") if current_folder else eagle_folder_id

    metadata = {
        "name": folder_name,
        "category": "folder",
        "tags": [],
        "path": f"/EAGLE_folder/{eagle_folder_id}",
        "thumbnail_route": DEFAULT_THUMBNAIL_ROUTE,
        "filesystem_path": None,
        "folders": folder_links
    }
    image_items = response.get("data", [])
    data = _format_eagle_items(image_items)
    return metadata, data

def get_eagle_images_by_tag(target_tag):
    """
    從 Eagle API 獲取所有帶有指定標籤的圖片，符合 EAGLE API 格式。

    Args:
        target_tag (str): 要查詢的標籤。

    Returns:
        (metadata, data): 以符合 EAGLE API 樣式的 `metadata` 與 `data`
    """
    # 從 Eagle API 獲取帶有該標籤的圖片
    response = EG.EAGLE_list_items(tags=[target_tag], orderBy="CREATEDATE")
    if response.get('status') == 'error':
        abort(500, description=f"Error fetching images with tag '{target_tag}': {response.get('data')}")

    # 設定 metadata
    metadata = {
        "name": f"Images with Tag: {target_tag}",
        "category": "tag",
        "tags": [target_tag],
        "path": f"/EAGLE_tag/{target_tag}",
        "thumbnail_route": DEFAULT_THUMBNAIL_ROUTE,
        "filesystem_path": None
    }

    image_items = response.get("data", [])
    data = _format_eagle_items(image_items)
    return metadata, data

def get_eagle_tags():
    """
    從 Eagle API 取得所有標籤資訊，整理給前端使用。
    """
    response = EG.EAGLE_get_tags()
    if response.get("status") != "success":
        abort(500, description=f"Failed to fetch Eagle tags: {response.get('data')}")

    raw_data = response.get("data", [])
    if isinstance(raw_data, dict):
        tag_entries = raw_data.get("tags") or raw_data.get("data") or []
    else:
        tag_entries = raw_data or []

    tags = []
    for entry in tag_entries:
        if isinstance(entry, dict):
            tag_name = entry.get("name") or entry.get("tag") or entry.get("title")
            count_value = (
                entry.get("count")
                or entry.get("itemCount")
                or entry.get("itemsCount")
                or entry.get("childCount")
            )
        else:
            tag_name = str(entry)
            count_value = None

        if not tag_name:
            continue

        try:
            count = int(count_value) if count_value is not None else None
        except (TypeError, ValueError):
            count = None

        tags.append({
            "name": tag_name,
            "count": count
        })

    tags.sort(key=lambda item: item["name"].lower())

    metadata = {
        "name": "EAGLE Tags",
        "category": "tag-list",
        "tags": [],
        "path": "/EAGLE_tags",
        "thumbnail_route": DEFAULT_THUMBNAIL_ROUTE,
        "filesystem_path": None
    }

    return metadata, tags

def search_eagle_items(keyword, limit=120):
    """透過 Eagle API 搜尋關鍵字並回傳格式化後的列表。"""
    response = EG.EAGLE_list_items(keyword=keyword, limit=limit, orderBy="CREATEDATE")
    if response.get("status") != "success":
        abort(500, description=f"Failed to search Eagle items: {response.get('data')}")

    raw_items = response.get("data", [])
    data = _format_eagle_items(raw_items)

    metadata = {
        "name": f"Search Results: {keyword}",
        "category": "search",
        "tags": [keyword],
        "path": f"/search?query={keyword}",
        "thumbnail_route": DEFAULT_THUMBNAIL_ROUTE,
        "filesystem_path": EG.EAGLE_get_current_library_path()
    }

    return metadata, data

def get_eagle_stream_items(offset=0, limit=30):
    """
    取得 Eagle 圖片/影片串流用的項目清單。
    """
    try:
        response = EG.EAGLE_list_items(limit=limit, offset=offset, orderBy="CREATEDATE")
    except Exception as exc:
        abort(500, description=f"Failed to fetch Eagle stream items: {exc}")

    if response.get("status") != "success":
        abort(500, description=f"Failed to fetch Eagle stream items: {response.get('data')}")

    raw_items = response.get("data", []) or []
    return _format_eagle_items(raw_items)


def _load_chrome_bookmarks():
    if not os.path.exists(CHROME_BOOKMARK_PATH):
        return {}
    try:
        with open(CHROME_BOOKMARK_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        abort(500, description="Failed to read Chrome bookmarks data")


def _find_chrome_node(bookmarks_root, path_parts):
    roots = bookmarks_root.get("roots", {}) if bookmarks_root else {}
    if not path_parts:
        return None, None, None

    first = path_parts[0]
    current = roots.get(first)
    if current is None:
        abort(404)

    parent = None
    parent_path = ""
    current_path = first

    for part in path_parts[1:]:
        if current.get("type") != "folder":
            abort(404)
        next_node = None
        for child in current.get("children", []):
            if child.get("id") == part:
                next_node = child
                break
        if next_node is None:
            abort(404)
        parent = current
        parent_path = current_path
        current = next_node
        current_path = f"{current_path}/{part}"

    return current, parent, parent_path


def get_chrome_bookmarks(folder_path=None):
    bookmarks = _load_chrome_bookmarks()
    roots = bookmarks.get("roots", {})
    safe_path = (folder_path or "bookmark_bar").strip("/")

    if not roots:
        abort(404)

    parts = [part for part in safe_path.split("/") if part]
    if not parts:
        abort(404)

    current, parent, parent_path = _find_chrome_node(bookmarks, parts)
    if current is None:
        abort(404)

    data = []
    current_name = current.get("name") or "(未命名資料夾)"
    metadata = {
        "name": current_name,
        "category": "chrome",
        "tags": ["chrome", "bookmarks"],
        "path": f"/chrome/{quote(safe_path, safe='/')}",
        "thumbnail_route": DEFAULT_THUMBNAIL_ROUTE,
        "filesystem_path": CHROME_BOOKMARK_PATH
    }

    if parent:
        parent_name = parent.get("name") or "上一層"
        parent_url = "/chrome/" if not parent_path else f"/chrome/{quote(parent_path, safe='/')}"
        metadata["folders"] = [{
            "name": parent_name,
            "url": parent_url
        }]
    else:
        # 根節點時提供快速切換到其他根資料夾
        for key in ["bookmark_bar", "other", "synced"]:
            node = roots.get(key)
            if not node or key == parts[0]:
                continue
            node_name = node.get("name") or key.replace("_", " ").title()
            data.append({
                "name": f"📁 {node_name}",
                "thumbnail_route": DEFAULT_THUMBNAIL_ROUTE,
                "url": f"/chrome/{quote(key, safe='/')}",
                "item_path": None,
                "media_type": "folder",
                "ext": None
            })

    children = current.get("children", [])
    for child in children:
        child_type = child.get("type")
        if child_type == "folder":
            child_name = child.get("name") or "(未命名資料夾)"
            child_path = f"{safe_path}/{child.get('id')}"
            data.append({
                "name": child_name,
                "thumbnail_route": DEFAULT_THUMBNAIL_ROUTE,
                "url": f"/chrome/{quote(child_path, safe='/')}",
                "item_path": None,
                "media_type": "folder",
                "ext": None
            })
        elif child_type == "url":
            child_name = child.get("name") or child.get("url")
            url = child.get("url")
            folder_meta = {"folder_path": safe_path}
            thumbnail, sub_type = _cache_thumbnail_for_bookmark(url, child_name, folder_meta)
            thumbnail = thumbnail or DEFAULT_THUMBNAIL_ROUTE
            ext = sub_type
            data.append({
                "name": child_name,
                "thumbnail_route": thumbnail,
                "url": url,
                "item_path": url,
                "media_type": "bookmark",
                "ext": ext
            })

    return metadata, data


def get_chrome_youtube_bookmarks():
    bookmarks = _load_chrome_bookmarks()
    roots = bookmarks.get("roots", {})
    results = []

    def _walk(node, path_labels):
        node_type = node.get("type")
        if node_type == "folder":
            label = node.get("name") or "(未命名資料夾)"
            new_path = path_labels + [label]
            for child in node.get("children", []):
                _walk(child, new_path)
        elif node_type == "url":
            url = node.get("url")
            video_id = _extract_youtube_id(url)
            if not video_id:
                return
            label = node.get("name") or url
            folder_meta = {"folder_path": " / ".join(filter(None, path_labels))}
            thumbnail, sub_type = _cache_thumbnail_for_bookmark(url, label, folder_meta)
            thumbnail = thumbnail or DEFAULT_THUMBNAIL_ROUTE
            sub_type = sub_type or "youtube"
            results.append({
                "name": label,
                "thumbnail_route": thumbnail,
                "url": url,
                "item_path": url,
                "media_type": "bookmark",
                "ext": sub_type,
                "description": folder_meta.get("folder_path")
            })

    for key in ["bookmark_bar", "other", "synced", "mobile"]:
        node = roots.get(key)
        if not node:
            continue
        root_label = node.get("name") or key.replace("_", " ").title()
        _walk(node, [root_label])

    metadata = {
        "name": "YouTube 書籤",
        "category": "chrome-youtube",
        "tags": ["chrome", "youtube", "bookmarks"],
        "path": "/chrome_youtube/",
        "thumbnail_route": DEFAULT_THUMBNAIL_ROUTE,
        "filesystem_path": CHROME_BOOKMARK_PATH
    }

    return metadata, results

def _extract_folder_ids(raw_folders):
    """
    將 Eagle 回傳的 folder 資訊整理成 id list。
    """
    if not raw_folders:
        return []

    ids = OrderedDict()

    if not isinstance(raw_folders, (list, tuple, set)):
        raw_folders = [raw_folders]

    for entry in raw_folders:
        folder_id = None
        if isinstance(entry, str):
            folder_id = entry
        elif isinstance(entry, dict):
            folder_id = (
                entry.get("id")
                or entry.get("folderId")
                or entry.get("folder_id")
            )
        if folder_id:
            folder_id = str(folder_id).strip()
            if folder_id:
                ids.setdefault(folder_id, None)

    return list(ids.keys())


def _build_eagle_folder_links(folder_ids):
    """
    將 folder id 轉換成可供前端使用的連結資訊。
    """
    folder_ids = _extract_folder_ids(folder_ids)
    if not folder_ids:
        return []

    try:
        df = EG.EAGLE_get_folders_df_all(flatten=True)
    except Exception:
        df = None

    lookup = {}
    if df is not None and getattr(df, "empty", True) is False:
        for _, row in df.iterrows():
            row_id = str(row.get("id") or "").strip()
            if not row_id:
                continue
            lookup[row_id] = row.get("name") or row_id

    links = []
    seen = OrderedDict()
    for folder_id in folder_ids:
        if folder_id in seen:
            continue
        seen[folder_id] = None
        folder_name = lookup.get(folder_id, folder_id)
        links.append({
            "id": folder_id,
            "name": folder_name,
            "url": f"/EAGLE_folder/{folder_id}/"
        })

    links.sort(key=lambda item: item["name"].lower())
    return links


def _normalize_item_tags(raw_tags):
    """
    將 Eagle item 的標籤轉換成字串 list。
    """
    if not raw_tags:
        return []

    tags = OrderedDict()
    if isinstance(raw_tags, str):
        raw_tags = [raw_tags]

    for entry in raw_tags:
        tag = None
        if isinstance(entry, str):
            tag = entry
        elif isinstance(entry, dict):
            tag = entry.get("name") or entry.get("tag")
        if tag:
            normalized = tag.strip()
            if normalized:
                tags.setdefault(normalized, None)

    return list(tags.keys())


def _get_eagle_folder_context(folder_id):
    """
    取得指定 Eagle 資料夾及其父資料夾資訊。
    Returns (current_folder, parent_folder)
    """
    response = EG.EAGLE_get_library_info()
    if response.get("status") != "success":
        return None, None

    folders = response.get("data", {}).get("folders", [])

    def _search(nodes, parent=None):
        for node in nodes:
            if node.get("id") == folder_id:
                return node, parent
            result = _search(node.get("children", []), node)
            if result is not None:
                return result
        return None

    found = _search(folders)
    if not found:
        return None, None
    return found


def _build_local_similar_items(target_path, base_dir, src, limit=6):
    """
    根據同資料夾內容挑選相似的本地項目。
    """
    parent_dir = os.path.dirname(target_path)
    try:
        entries = os.listdir(parent_dir)
    except (FileNotFoundError, PermissionError):
        return []

    candidates = []
    for entry in entries:
        if entry.startswith("."):
            continue
        abs_entry = os.path.join(parent_dir, entry)
        if abs_entry == target_path or not os.path.isfile(abs_entry):
            continue

        try:
            rel_entry = os.path.relpath(abs_entry, base_dir)
        except ValueError:
            continue
        rel_entry = _normalize_slashes(rel_entry)

        if _is_image_file(entry):
            candidates.append({
                "id": rel_entry,
                "name": os.path.splitext(entry)[0] or entry,
                "path": _build_image_url(rel_entry, src),
                "thumbnail_route": _build_file_route(abs_entry, src),
                "media_type": "image",
                "ext": os.path.splitext(entry)[1].lstrip(".").lower() or None
            })
        elif _is_video_file(entry):
            candidates.append({
                "id": rel_entry,
                "name": os.path.splitext(entry)[0] or entry,
                "path": _build_video_url(rel_entry, src),
                "thumbnail_route": _find_video_thumbnail(abs_entry, src),
                "media_type": "video",
                "ext": os.path.splitext(entry)[1].lstrip(".").lower() or None
            })

    if not candidates:
        return []

    sample_size = min(limit, len(candidates))
    if sample_size <= 0:
        return []

    return random.sample(candidates, sample_size)


def _build_eagle_similar_items(current_item_id, tags, folder_ids, limit=6):
    """
    根據標籤或資料夾推薦相似項目。
    """
    candidate_map = OrderedDict()

    def _accumulate_from_response(response):
        if response.get("status") != "success":
            return
        for raw in response.get("data", []) or []:
            other_id = raw.get("id")
            if not other_id or other_id == current_item_id:
                continue
            if other_id in candidate_map:
                continue
            candidate_map[other_id] = raw

    primary_tags = tags[:2] if tags else []
    for tag in primary_tags:
        try:
            resp = EG.EAGLE_list_items(tags=[tag], limit=120, orderBy="MODIFIEDDATE")
        except Exception:
            continue
        _accumulate_from_response(resp)
        if len(candidate_map) >= limit * 2:
            break

    if not candidate_map and folder_ids:
        primary_folders = folder_ids[:2]
        for folder_id in primary_folders:
            try:
                resp = EG.EAGLE_list_items(folders=[folder_id], limit=120, orderBy="MODIFIEDDATE")
            except Exception:
                continue
            _accumulate_from_response(resp)
            if len(candidate_map) >= limit * 2:
                break

    if not candidate_map:
        return []

    candidate_list = list(candidate_map.values())
    sample_size = min(limit, len(candidate_list))
    if sample_size == 0:
        return []

    sampled_raw = random.sample(candidate_list, sample_size)
    formatted_candidates = _format_eagle_items(sampled_raw)
    formatted_map = {item["id"]: item for item in formatted_candidates if item.get("id")}

    similar_items = []
    for raw in sampled_raw:
        item_id = raw.get("id")
        formatted = formatted_map.get(item_id)
        if not formatted:
            continue
        media_type = formatted.get("media_type")
        detail_path = f"/EAGLE_video/{item_id}/" if media_type == "video" else f"/EAGLE_image/{item_id}/"
        similar_items.append({
            "id": item_id,
            "name": formatted.get("name") or "Untitled",
            "path": detail_path,
            "thumbnail_route": formatted.get("thumbnail_route") or DEFAULT_THUMBNAIL_ROUTE,
            "media_type": media_type,
            "ext": formatted.get("ext")
        })

    return similar_items


def get_eagle_video_details(item_id):
    """
    從 Eagle API 取得單一影片項目的詳細資訊並組合成播放器頁面需要的結構。
    """
    response = EG.EAGLE_get_item_info(item_id)
    if response.get("status") != "success":
        abort(500, description=f"Failed to fetch Eagle item info: {response.get('data')}")

    item = response.get("data")
    if not item or isinstance(item, list):
        abort(404, description="Video item not found.")

    raw_ext = item.get("ext") or ""
    ext = raw_ext.lower().lstrip(".")
    file_name = item.get("name") or item_id
    file_name_with_ext = item.get("fileName")

    if not ext and file_name_with_ext:
        _, inferred_ext = os.path.splitext(file_name_with_ext)
        ext = inferred_ext.lower().lstrip(".")

    if ext not in VIDEO_EXTENSIONS:
        abort(404, description="Requested Eagle item is not a video.")

    base_library_path = EG.EAGLE_get_current_library_path()
    item_dir = os.path.join(base_library_path, "images", f"{item_id}.info")

    candidate_files = []
    if file_name:
        candidate_files.append(f"{file_name}.{ext}")
    if file_name_with_ext:
        candidate_files.append(file_name_with_ext)
    candidate_files.append(f"{item_id}.{ext}")

    video_path = None
    for candidate in candidate_files:
        candidate_path = os.path.join(item_dir, candidate)
        if os.path.isfile(candidate_path):
            video_path = candidate_path
            break

    if video_path is None and os.path.isdir(item_dir):
        for entry in os.listdir(item_dir):
            if _is_video_file(entry):
                video_path = os.path.join(item_dir, entry)
                file_name, ext = os.path.splitext(entry)
                ext = ext.lstrip(".").lower()
                break

    if video_path is None:
        abort(404, description="Video file not found on disk.")

    normalized_abs_path = _normalize_slashes(os.path.abspath(video_path))
    relative_path = _normalize_slashes(os.path.relpath(video_path, base_library_path))
    file_size = os.path.getsize(video_path)
    modified_time = datetime.fromtimestamp(os.path.getmtime(video_path))

    stream_route = f"/serve_image/{normalized_abs_path}"

    thumbnail_route = DEFAULT_VIDEO_THUMBNAIL_ROUTE
    if os.path.isdir(item_dir):
        stem = os.path.splitext(os.path.basename(video_path))[0]
        for image_ext in IMAGE_EXTENSIONS:
            candidate_thumb = os.path.join(item_dir, f"{stem}_thumbnail.{image_ext}")
            if os.path.isfile(candidate_thumb):
                thumbnail_route = f"/serve_image/{_normalize_slashes(os.path.abspath(candidate_thumb))}"
                break

    tags = item.get("tags") or []
    tags = _normalize_item_tags(tags)

    original_url = item.get("website") or item.get("url")
    folder_ids = _extract_folder_ids(item.get("folders"))
    fallback_folder = item.get("folderId") or item.get("folder_id")
    if not folder_ids and fallback_folder:
        folder_ids = _extract_folder_ids([fallback_folder])
    folder_links = _build_eagle_folder_links(folder_ids)
    similar_items = _build_eagle_similar_items(item_id, tags, folder_ids)
    resolved_ext = ext or os.path.splitext(video_path)[1].lstrip(".").lower() or None

    metadata = {
        "name": item.get("name") or os.path.basename(video_path),
        "category": "eagle-video",
        "tags": tags,
        "path": f"/EAGLE_video/{item_id}/",
        "thumbnail_route": thumbnail_route,
        "filesystem_path": normalized_abs_path,
        "description": item.get("annotation") or item.get("note"),
        "folders": folder_links,
        "similar": similar_items,
        "ext": resolved_ext
    }

    video_data = {
        "name": metadata["name"],
        "relative_path": relative_path,
        "source_url": stream_route,
        "original_url": original_url,
        "thumbnail_route": thumbnail_route,
        "mime_type": mimetypes.guess_type(video_path)[0] or "video/mp4",
        "size_bytes": file_size,
        "size_display": _human_readable_size(file_size),
        "modified_time": modified_time.strftime("%Y-%m-%d %H:%M"),
        "parent_url": None,
        "download_url": stream_route,
        "folders": folder_links,
        "ext": resolved_ext
    }

    return metadata, video_data

def get_eagle_image_details(item_id):
    """
    從 Eagle API 取得單一圖片項目的詳細資訊並組合成展示頁面需要的結構。
    """
    response = EG.EAGLE_get_item_info(item_id)
    if response.get("status") != "success":
        abort(500, description=f"Failed to fetch Eagle item info: {response.get('data')}")

    item = response.get("data")
    if not item or isinstance(item, list):
        abort(404, description="Image item not found.")

    raw_ext = item.get("ext") or ""
    ext = raw_ext.lower().lstrip(".")
    file_name = item.get("name") or item_id
    file_name_with_ext = item.get("fileName")

    base_library_path = EG.EAGLE_get_current_library_path()
    item_dir = os.path.join(base_library_path, "images", f"{item_id}.info")

    candidate_files = []
    if file_name_with_ext:
        candidate_files.append(file_name_with_ext)
    if file_name:
        candidate_files.append(f"{file_name}.{ext}" if ext else file_name)
    candidate_files.append(f"{item_id}.{ext}" if ext else item_id)

    image_path = None
    resolved_ext = ext
    for candidate in candidate_files:
        if not candidate:
            continue
        candidate_path = os.path.join(item_dir, candidate)
        if os.path.isfile(candidate_path):
            resolved_ext = os.path.splitext(candidate)[1].lstrip(".").lower()
            if resolved_ext in IMAGE_EXTENSIONS:
                image_path = candidate_path
                break

    if image_path is None and os.path.isdir(item_dir):
        for entry in os.listdir(item_dir):
            entry_ext = os.path.splitext(entry)[1].lstrip(".").lower()
            if entry_ext in IMAGE_EXTENSIONS:
                image_path = os.path.join(item_dir, entry)
                resolved_ext = entry_ext
                break

    if image_path is None:
        abort(404, description="Image file not found on disk.")

    normalized_abs_path = _normalize_slashes(os.path.abspath(image_path))
    relative_path = _normalize_slashes(os.path.relpath(image_path, base_library_path))
    file_size = os.path.getsize(image_path)
    modified_time = datetime.fromtimestamp(os.path.getmtime(image_path))

    stream_route = f"/serve_image/{normalized_abs_path}"

    tags = _normalize_item_tags(item.get("tags"))
    original_url = item.get("website") or item.get("url")
    folder_ids = _extract_folder_ids(item.get("folders"))
    fallback_folder = item.get("folderId") or item.get("folder_id")
    if not folder_ids and fallback_folder:
        folder_ids = _extract_folder_ids([fallback_folder])
    folder_links = _build_eagle_folder_links(folder_ids)
    similar_items = _build_eagle_similar_items(item_id, tags, folder_ids)

    metadata = {
        "name": item.get("name") or os.path.basename(image_path),
        "category": "eagle-image",
        "tags": tags,
        "path": f"/EAGLE_image/{item_id}/",
        "thumbnail_route": stream_route,
        "filesystem_path": normalized_abs_path,
        "description": item.get("annotation") or item.get("note"),
        "folders": folder_links,
        "similar": similar_items,
        "ext": resolved_ext or None
    }

    image_data = {
        "name": metadata["name"],
        "relative_path": relative_path,
        "source_url": stream_route,
        "original_url": original_url,
        "thumbnail_route": stream_route,
        "mime_type": mimetypes.guess_type(image_path)[0] or f"image/{resolved_ext or 'jpeg'}",
        "size_bytes": file_size,
        "size_display": _human_readable_size(file_size),
        "modified_time": modified_time.strftime("%Y-%m-%d %H:%M"),
        "parent_url": None,
        "download_url": stream_route,
        "folders": folder_links,
        "ext": resolved_ext or None
    }

    return metadata, image_data

def _format_eagle_items(image_items):
    """
    將 Eagle 圖片清單格式化成 EAGLE API 樣式的 data list。
    """
    image_items.sort(key=lambda x: x.get("name", "")) # 按名稱排序
    data = []

    base = EG.EAGLE_get_current_library_path()
    for image in image_items:
        image_id = image.get("id")
        image_name = image.get("name", "unknown")
        image_ext = image.get("ext", "jpg")
        image_path = f"/serve_image/{base}/images/{image_id}.info/{image_name}.{image_ext}"

        # 特別處理影片縮圖
        normalized_ext = (image_ext or "").lower()
        is_video = normalized_ext in VIDEO_EXTENSIONS
        if normalized_ext == "mp4":
            thumbnail_route = f"/serve_image/{base}/images/{image_id}.info/{image_name}_thumbnail.png"
        else:
            thumbnail_route = image_path

        data.append({
            "id": image_id,
            "name": image_name,
            "url": image_path,
            "thumbnail_route": thumbnail_route,
            "item_path": os.path.abspath(os.path.join(base, "images", f"{image_id}.info", f"{image_name}.{image_ext}")),
            "media_type": "video" if is_video else "image",
            "ext": normalized_ext or None
        })

    return data

def get_subfolders_info(folder_id):
    """
    根據指定的 folder_id，取出其 children（子資料夾 id list），
    並組成符合前端展示格式的 list of dict。
    """
    df = EG.EAGLE_get_folders_df()

    # 找出指定 folder row
    row = df[df["id"] == folder_id]
    if row.empty:
        return []

    children_infos = row.iloc[0]["children"]  # 是 list
    result = []

    for child_info in children_infos:
        child_id = child_info["id"]
        # child_row = df[df["id"] == child_info["id"]]
        # if child_row.empty:
            # continue

        # child = child_row.iloc[0]
        sub_name = child_info.get("name", f"(unnamed-{child_id})")
        path = f"/EAGLE_folder/{child_id}"

        # 嘗試取一張圖作為縮圖
        folder_response = EG.EAGLE_list_items(folders=[child_id])
        thumbnail_route = DEFAULT_THUMBNAIL_ROUTE
        if folder_response.get("status") == "success" and folder_response.get("data"):
            first_img = folder_response["data"][0]
            image_id = first_img["id"]
            image_name = first_img["name"]
            image_ext = first_img["ext"]
            base = EG.EAGLE_get_current_library_path()
            thumbnail_route = f"/serve_image/{base}/images/{image_id}.info/{image_name}.{image_ext}"

        result.append({
            "name": f"📁 {sub_name}",
            "url": path,
            "thumbnail_route": thumbnail_route,
            "item_path": None,
            "media_type": "folder",
            "ext": None
        })

    return result
