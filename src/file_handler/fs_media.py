"""Local filesystem media handling for Flowinone."""

import mimetypes
import os
import random
from datetime import datetime

from config import DB_route_internal, DB_route_external
from .models import (
    AccessDenied,
    FolderNotFound,
    MediaDetail,
    MediaEntry,
    MediaNotFound,
    PageMetadata,
)
from .paths import (
    DEFAULT_THUMBNAIL_ROUTE,
    _build_file_route,
    _build_folder_url,
    _build_image_url,
    _build_video_url,
    _collect_directory_entries,
    _find_directory_thumbnail,
    _find_video_thumbnail,
    _human_readable_size,
    _is_image_file,
    _is_video_file,
    _normalize_slashes,
    _normalize_source,
    _safe_relative_path,
)


def _build_folder_entry(display_name, abs_path, rel_path, src):
    return MediaEntry(
        name=display_name,
        thumbnail_route=_find_directory_thumbnail(abs_path, src),
        url=_build_folder_url(rel_path, src),
        item_path=os.path.abspath(abs_path),
        media_type="folder",
    )


def _build_image_entry(display_name, abs_path, rel_path, src):
    file_route = _build_file_route(abs_path, src)
    ext = os.path.splitext(display_name)[1].lstrip(".").lower()
    return MediaEntry(
        name=display_name,
        thumbnail_route=file_route,
        url=_build_image_url(rel_path, src),
        item_path=os.path.abspath(abs_path),
        media_type="image",
        ext=ext or None,
    )


def _build_video_entry(display_name, abs_path, rel_path, src):
    ext = os.path.splitext(display_name)[1].lstrip(".").lower()
    return MediaEntry(
        name=display_name,
        thumbnail_route=_find_video_thumbnail(abs_path, src),
        url=_build_video_url(rel_path, src),
        item_path=os.path.abspath(abs_path),
        media_type="video",
        ext=ext or None,
    )


def get_all_folders_info(src):
    """
    取得 DB_route 內的所有子資料夾資訊，符合 EAGLE API 格式
    src: internal or external
    """
    normalized_src = _normalize_source(src)
    base_dir = DB_route_external if normalized_src == "external" else DB_route_internal

    if not os.path.isdir(base_dir):
        raise FolderNotFound(base_dir)

    metadata = PageMetadata(
        name="All Collections",
        category="collections",
        tags=["collection", "group", "Main"],
        path="/" if normalized_src == "external" else "/?src=internal",
        thumbnail_route=DEFAULT_THUMBNAIL_ROUTE,
        filesystem_path=os.path.abspath(base_dir),
    )

    data = _collect_directory_entries(
        base_dir,
        "",
        normalized_src,
        folder_builder=_build_folder_entry,
        image_builder=_build_image_entry,
        video_builder=_build_video_entry
    )
    metadata.folders = [{
        "name": "Root",
        "url": _build_folder_url("", normalized_src)
    }]
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
        raise FolderNotFound(target_dir)
    
    metadata = PageMetadata(
        name=os.path.basename(safe_folder_path.rstrip("/")) if safe_folder_path else os.path.basename(os.path.normpath(base_dir)),
        category="folder",
        tags=[],
        path=_build_folder_url(safe_folder_path, normalized_src),
        thumbnail_route=_find_directory_thumbnail(target_dir, normalized_src),
        filesystem_path=os.path.abspath(target_dir)
    )

    data = _collect_directory_entries(
        base_dir,
        safe_folder_path,
        normalized_src,
        folder_builder=_build_folder_entry,
        image_builder=_build_image_entry,
        video_builder=_build_video_entry
    )
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
        raise MediaNotFound(target_path)

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

    metadata = PageMetadata(
        name=file_name,
        category="video",
        tags=[],
        path=_build_video_url(safe_video_path, normalized_src),
        thumbnail_route=thumbnail_route,
        filesystem_path=os.path.abspath(os.path.dirname(target_path)),
        folders=folder_links,
        similar=[item.to_dict() for item in similar_items],
        ext=file_ext or None
    )

    video_data = MediaDetail(
        name=file_name,
        relative_path=safe_video_path,
        source_url=source_url,
        thumbnail_route=thumbnail_route,
        original_url=None,
        mime_type=mime_type,
        size_bytes=file_size,
        size_display=_human_readable_size(file_size),
        modified_time=modified_time.strftime("%Y-%m-%d %H:%M"),
        parent_url=parent_url,
        download_url=source_url,
        folders=folder_links,
        ext=file_ext or None
    )

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
        raise MediaNotFound(target_path)

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

    metadata = PageMetadata(
        name=file_name,
        category="image",
        tags=[],
        path=_build_image_url(safe_image_path, normalized_src),
        thumbnail_route=source_url,
        filesystem_path=os.path.abspath(target_path),
        folders=folder_links,
        similar=[item.to_dict() for item in similar_items],
        ext=file_ext or None
    )

    image_data = MediaDetail(
        name=file_name,
        relative_path=safe_image_path,
        source_url=source_url,
        thumbnail_route=source_url,
        original_url=None,
        mime_type=mime_type,
        size_bytes=file_size,
        size_display=_human_readable_size(file_size),
        modified_time=modified_time.strftime("%Y-%m-%d %H:%M"),
        parent_url=parent_url,
        download_url=source_url,
        folders=folder_links,
        ext=file_ext or None
    )

    return metadata, image_data


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
            candidates.append(MediaEntry(
                id=rel_entry,
                name=os.path.splitext(entry)[0] or entry,
                url=_build_image_url(rel_entry, src),
                thumbnail_route=_build_file_route(abs_entry, src),
                item_path=os.path.abspath(abs_entry),
                media_type="image",
                ext=os.path.splitext(entry)[1].lstrip(".").lower() or None
            ))
        elif _is_video_file(entry):
            candidates.append(MediaEntry(
                id=rel_entry,
                name=os.path.splitext(entry)[0] or entry,
                url=_build_video_url(rel_entry, src),
                thumbnail_route=_find_video_thumbnail(abs_entry, src),
                item_path=os.path.abspath(abs_entry),
                media_type="video",
                ext=os.path.splitext(entry)[1].lstrip(".").lower() or None
            ))

    if not candidates:
        return []

    sample_size = min(limit, len(candidates))
    if sample_size <= 0:
        return []

    return random.sample(candidates, sample_size)


def has_db_main() -> bool:
    return os.path.isdir(DB_route_external)
