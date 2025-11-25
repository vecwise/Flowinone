"""Eagle integration helpers for Flowinone."""

import mimetypes
import os
import random
import time
from collections import OrderedDict
from datetime import datetime

from flask import abort

import src.eagle_api as EG
from .paths import (
    DEFAULT_THUMBNAIL_ROUTE,
    DEFAULT_VIDEO_THUMBNAIL_ROUTE,
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    _human_readable_size,
    _is_image_file,
    _is_video_file,
    _normalize_slashes,
)


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

    folder_links = []
    current_folder, parent_folder = _get_eagle_folder_context(eagle_folder_id)
    if current_folder:
        path_stack = []
        node = current_folder
        parent = parent_folder
        while parent:
            parent_id = parent.get("id")
            if parent_id:
                path_stack.append({
                    "id": parent_id,
                    "name": parent.get("name", parent_id),
                    "url": f"/EAGLE_folder/{parent_id}/"
                })
                grand = _get_eagle_folder_context(parent_id)[1]
                parent = grand
            else:
                break
        folder_links = list(reversed(path_stack))

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
    """
    response = EG.EAGLE_list_items(tags=[target_tag], orderBy="CREATEDATE")
    if response.get('status') == 'error':
        abort(500, description=f"Error fetching images with tag '{target_tag}': {response.get('data')}")

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
    image_items.sort(key=lambda x: x.get("name", ""))
    data = []

    base = EG.EAGLE_get_current_library_path()
    for image in image_items:
        image_id = image.get("id")
        image_name = image.get("name", "unknown")
        image_ext = image.get("ext", "jpg")
        image_path = f"/serve_image/{base}/images/{image_id}.info/{image_name}.{image_ext}"

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

    row = df[df["id"] == folder_id]
    if row.empty:
        return []

    children_infos = row.iloc[0]["children"]
    result = []

    for child_info in children_infos:
        child_id = child_info["id"]
        sub_name = child_info.get("name", f"(unnamed-{child_id})")
        path = f"/EAGLE_folder/{child_id}"

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
