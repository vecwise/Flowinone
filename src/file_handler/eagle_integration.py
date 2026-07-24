"""Eagle integration helpers for Flowinone."""

import mimetypes
import os
import random
import time
from collections import OrderedDict
from datetime import datetime

import src.eagle_api as EG
from src.eagle_api.models import EaglePage
from .models import ExternalServiceError, MediaDetail, MediaEntry, MediaNotFound, PageMetadata
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


_EAGLE_CACHE_TTL_SECONDS = 60.0
_EAGLE_STATUS_CACHE = {"timestamp": 0.0, "value": False}
_EAGLE_LIBRARY_CACHE = {"timestamp": 0.0, "value": None}
_EAGLE_CAPABILITIES_CACHE = {"timestamp": 0.0, "value": None}


def _cache_value(cache, loader, force=False):
    now = time.time()
    if not force and cache["value"] is not None and now - cache["timestamp"] < _EAGLE_CACHE_TTL_SECONDS:
        return cache["value"]
    value = loader()
    if getattr(value, "get", lambda *_: None)("status") == "success" or hasattr(value, "available"):
        cache.update({"timestamp": now, "value": value})
    return value


def _get_eagle_library_info(force=False):
    return _cache_value(_EAGLE_LIBRARY_CACHE, EG.EAGLE_get_library_info, force)


def get_eagle_capabilities(force=False):
    """Return cached v2/AI capability information for Flowinone feature decisions."""
    return _cache_value(_EAGLE_CAPABILITIES_CACHE, EG.EAGLE_get_capabilities, force)


def _get_eagle_library_path():
    response = _get_eagle_library_info()
    if response.get("status") != "success":
        raise ExternalServiceError(f"Failed to fetch Eagle library info: {response.get('data')}")
    path = (response.get("data") or {}).get("path")
    if not path:
        raise ExternalServiceError("Eagle v2 library path is unavailable")
    return path


def _pagination_from_response(response):
    page = response.get("pagination") or {}
    try:
        total = int(page.get("total"))
        offset = int(page.get("offset") or 0)
        limit = int(page.get("limit") or 1)
    except (TypeError, ValueError):
        return None
    item_count = len(response.get("data") or [])
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "previous_offset": max(0, offset - limit) if offset else None,
        "next_offset": offset + item_count if offset + item_count < total else None,
    }


def is_eagle_available(force: bool = False) -> bool:
    now = time.time()
    if not force and now - _EAGLE_STATUS_CACHE["timestamp"] < 60:
        return _EAGLE_STATUS_CACHE["value"]
    try:
        available = bool(get_eagle_capabilities(force=force).available)
    except Exception:
        available = False
    _EAGLE_STATUS_CACHE.update({"timestamp": now, "value": available})
    return available


def get_eagle_folders():
    """
    獲取 Eagle API 提供的所有資料夾資訊
    """
    response = _get_eagle_library_info()
    if response.get("status") != "success":
        raise ExternalServiceError(f"Failed to fetch Eagle folders: {response.get('data')}")

    metadata = PageMetadata(
        name="All Eagle Folders",
        category="collections",
        tags=["eagle", "folders"],
        path="/EAGLE_folder",
        thumbnail_route=DEFAULT_THUMBNAIL_ROUTE,
        filesystem_path=_get_eagle_library_path()
    )

    data: list[MediaEntry] = []
    for folder in response.get("data", {}).get("folders", []):
        folder_id = folder.get("id")
        folder_name = folder.get("name", "Unnamed Folder")

        folder_response = EG.EAGLE_list_items(folders=[folder_id], limit=1, fields=["id", "name", "ext"])
        image_items = folder_response.get("data", [])
        thumbnail_path = _eagle_item_media_path(image_items[0], _get_eagle_library_path()) if image_items else DEFAULT_THUMBNAIL_ROUTE

        data.append(MediaEntry(
            name=folder_name,
            id=folder_id,
            url=f"/EAGLE_folder/{folder_id}/",
            thumbnail_route=thumbnail_path,
            item_path=None,
            media_type="folder"
        ))

    return metadata, data


def get_eagle_images_by_folderid(eagle_folder_id, offset=0, limit=120):
    """
    獲取 Eagle API 提供的指定資料夾內的圖片資訊，符合 EAGLE API 格式
    """
    response = EG.EAGLE_list_items(folders=[eagle_folder_id], offset=offset, limit=limit)
    if response.get("status") != "success":
        raise ExternalServiceError(f"Failed to fetch images from Eagle folder: {response.get('data')}")

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

    metadata = PageMetadata(
        name=folder_name,
        category="folder",
        tags=[],
        path=f"/EAGLE_folder/{eagle_folder_id}",
        thumbnail_route=DEFAULT_THUMBNAIL_ROUTE,
        filesystem_path=None,
        folders=folder_links,
        pagination=_pagination_from_response(response),
    )
    image_items = response.get("data", [])
    data = _format_eagle_items(image_items, sort_by="name")
    return metadata, data


def get_eagle_images_by_tag(target_tag, offset=0, limit=120):
    """
    從 Eagle API 獲取所有帶有指定標籤的圖片，符合 EAGLE API 格式。
    """
    response = EG.EAGLE_list_items(tags=[target_tag], offset=offset, limit=limit)
    if response.get('status') == 'error':
        raise ExternalServiceError(f"Error fetching images with tag '{target_tag}': {response.get('data')}")

    metadata = PageMetadata(
        name=f"Images with Tag: {target_tag}",
        category="tag",
        tags=[target_tag],
        path=f"/EAGLE_tag/{target_tag}",
        thumbnail_route=DEFAULT_THUMBNAIL_ROUTE,
        filesystem_path=None,
        pagination=_pagination_from_response(response),
    )

    image_items = response.get("data", [])
    data = _format_eagle_items(image_items, sort_by="name")
    return metadata, data


def get_eagle_tags():
    """
    從 Eagle API 取得所有標籤資訊，整理給前端使用。
    """
    response = EG.EAGLE_get_tags()
    if response.get("status") != "success":
        raise ExternalServiceError(f"Failed to fetch Eagle tags: {response.get('data')}")

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

    metadata = PageMetadata(
        name="EAGLE Tags",
        category="tag-list",
        tags=[],
        path="/EAGLE_tags",
        thumbnail_route=DEFAULT_THUMBNAIL_ROUTE,
        filesystem_path=None
    )

    return metadata, tags


def search_eagle_items(keyword, offset=0, limit=120):
    """Use Eagle v2 full-text search, including AND/OR/NOT query syntax."""
    response = EG.EAGLE_query_items(keyword, offset=offset, limit=limit)
    if response.get("status") != "success":
        raise ExternalServiceError(f"Failed to search Eagle items: {response.get('data')}")

    raw_items = response.get("data", [])
    # item/query already returns relevance-aware results; preserve that order.
    data = _format_eagle_items(raw_items, sort_by=None)

    metadata = PageMetadata(
        name=f"Search Results: {keyword}",
        category="search",
        tags=[keyword],
        path=f"/search?query={keyword}",
        thumbnail_route=DEFAULT_THUMBNAIL_ROUTE,
        filesystem_path=_get_eagle_library_path(),
        pagination=_pagination_from_response(response),
    )

    return metadata, data


def get_eagle_smart_folders():
    """Return Eagle v2 smart folders as browsable Flowinone collections."""
    response = EG.EAGLE_get_smart_folders()
    if response.get("status") != "success":
        raise ExternalServiceError(f"Failed to fetch Eagle smart folders: {response.get('data')}")

    metadata = PageMetadata(
        name="Eagle Smart Folders",
        category="smart-collections",
        tags=["eagle", "smart-folders"],
        path="/EAGLE_smart_folders/",
        thumbnail_route=DEFAULT_THUMBNAIL_ROUTE,
        filesystem_path=_get_eagle_library_path(),
    )
    data: list[MediaEntry] = []
    for folder in response.get("data") or []:
        folder_id = folder.get("id")
        if not folder_id:
            continue
        items_response = EG.EAGLE_get_smart_folder_items(folder_id, fields=["id", "name", "ext"])
        items = items_response.get("data") or [] if items_response.get("status") == "success" else []
        thumbnail = _format_eagle_items(items[:1], sort_by=None)[0].thumbnail_route if items else DEFAULT_THUMBNAIL_ROUTE
        data.append(MediaEntry(
            id=folder_id,
            name=folder.get("name") or "Unnamed Smart Folder",
            url=f"/EAGLE_smart_folder/{folder_id}/",
            thumbnail_route=thumbnail,
            item_path=None,
            media_type="folder",
            description=folder.get("description") or f"{folder.get('imageCount', 0)} items",
        ))
    return metadata, data


def get_eagle_images_by_smart_folder_id(smart_folder_id, offset=0, limit=120):
    response = EG.EAGLE_get_smart_folder_items(smart_folder_id, offset=offset, limit=limit)
    if response.get("status") != "success":
        raise ExternalServiceError(f"Failed to fetch Eagle smart folder: {response.get('data')}")
    folders_response = EG.EAGLE_get_smart_folders()
    folder = next((entry for entry in folders_response.get("data") or [] if entry.get("id") == smart_folder_id), None)
    metadata = PageMetadata(
        name=(folder or {}).get("name") or smart_folder_id,
        category="smart-folder",
        tags=[],
        path=f"/EAGLE_smart_folder/{smart_folder_id}/",
        thumbnail_route=DEFAULT_THUMBNAIL_ROUTE,
        filesystem_path=None,
        pagination=_pagination_from_response(response),
    )
    return metadata, _format_eagle_items(response.get("data") or [], sort_by="name")


def get_eagle_stream_items(offset=0, limit=30):
    """
    取得 Eagle 圖片/影片串流用的項目清單。
    """
    try:
        response = EG.EAGLE_list_items(limit=limit, offset=offset)
    except Exception as exc:
        raise ExternalServiceError(f"Failed to fetch Eagle stream items: {exc}") from exc

    if response.get("status") != "success":
        raise ExternalServiceError(f"Failed to fetch Eagle stream items: {response.get('data')}")

    raw_items = response.get("data", []) or []
    return _format_eagle_items(raw_items, sort_by="modificationTime", reverse=True)


def get_eagle_catalog_source(*, force=False):
    """Return stable and mutable identifiers for the active Eagle library."""
    response = _get_eagle_library_info(force=force)
    if response.get("status") != "success":
        raise ExternalServiceError(
            f"Failed to fetch Eagle library info: {response.get('data')}"
        )
    data = response.get("data") or {}
    identity = data.get("id") or data.get("libraryId") or data.get("path")
    if not identity:
        raise ExternalServiceError("Eagle v2 library identity is unavailable")
    version = (
        data.get("modifiedAt")
        or data.get("modificationTime")
        or data.get("updatedAt")
        or ""
    )
    return {"identity": str(identity), "version": str(version)}


def get_eagle_catalog_page(offset=0, limit=1000):
    """Load one lightweight, ordered Eagle page for Catalog synchronization."""
    try:
        response = EG.EAGLE_list_items(
            limit=limit,
            offset=offset,
            fields=[
                "id",
                "name",
                "ext",
                "url",
                "website",
                "tags",
                "folders",
                "annotation",
                "importedAt",
                "modifiedAt",
                "modificationTime",
            ],
        )
    except Exception as exc:
        raise ExternalServiceError(
            f"Failed to fetch Eagle catalog page: {exc}"
        ) from exc
    if response.get("status") != "success":
        raise ExternalServiceError(
            f"Failed to fetch Eagle catalog page: {response.get('data')}"
        )
    pagination = response.get("pagination") or {}
    raw_items = response.get("data") or []
    try:
        total = int(pagination["total"])
        page_offset = int(pagination.get("offset") or 0)
        page_limit = int(pagination.get("limit") or limit)
    except (KeyError, TypeError, ValueError) as exc:
        raise ExternalServiceError("Eagle catalog page is missing pagination") from exc

    library_path = _get_eagle_library_path()
    items = []
    for raw in raw_items:
        item = dict(raw)
        item_id = str(item.get("id") or "")
        name = str(item.get("name") or "Untitled")
        ext = str(item.get("ext") or "jpg").lower()
        if not item_id:
            # Retain malformed records in the page length so offset pagination
            # never skips a following valid record.  The Catalog layer will
            # reject the record and refuse deletion cleanup if totals diverge.
            items.append({"id": "", "name": name, "ext": ext})
            continue
        media_path = _eagle_item_media_path(item, library_path)
        thumbnail = (
            f"/serve_image/{library_path}/images/{item_id}.info/{name}_thumbnail.png"
            if ext == "mp4"
            else media_path
        )
        items.append(
            {
                "id": item_id,
                "name": name,
                "ext": ext,
                "media_type": "video" if ext in VIDEO_EXTENSIONS else "image",
                "thumbnail_route": thumbnail,
                "original_url": item.get("website") or item.get("url") or "",
                "tags": list(item.get("tags") or []),
                "folders": list(item.get("folders") or []),
                "description": str(item.get("annotation") or ""),
                "captured_at": str(item.get("importedAt") or ""),
                "modified_at": str(
                    item.get("modifiedAt") or item.get("modificationTime") or ""
                ),
            }
        )
    return EaglePage(
        items=items,
        total=total,
        offset=page_offset,
        limit=page_limit,
    )


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


def _iter_eagle_folders(nodes, parent=None):
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        yield node, parent
        yield from _iter_eagle_folders(node.get("children"), node)


def _eagle_folder_index():
    """Build an id lookup from the cached v2 library folder tree."""
    response = _get_eagle_library_info()
    if response.get("status") != "success":
        return {}
    folders = (response.get("data") or {}).get("folders") or []
    return {
        folder_id: {"folder": folder, "parent": parent}
        for folder, parent in _iter_eagle_folders(folders)
        if (folder_id := folder.get("id"))
    }


def _build_eagle_folder_links(folder_ids):
    """
    將 folder id 轉換成可供前端使用的連結資訊。
    """
    folder_ids = _extract_folder_ids(folder_ids)
    if not folder_ids:
        return []

    lookup = {
        folder_id: entry["folder"].get("name") or folder_id
        for folder_id, entry in _eagle_folder_index().items()
    }

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
    entry = _eagle_folder_index().get(folder_id)
    if not entry:
        return None, None
    return entry["folder"], entry["parent"]


def _build_eagle_similar_items(current_item_id, tags, folder_ids, limit=6):
    """
    根據標籤或資料夾推薦相似項目。
    """
    candidate_map = OrderedDict()
    candidate_scores = {}
    used_ai = False

    # Prefer v2 AI visual similarity when the optional Eagle plugin is ready.
    try:
        if get_eagle_capabilities().ai_ready:
            ai_response = EG.EAGLE_ai_search_similar(current_item_id, limit=limit + 1)
            if ai_response.get("status") == "success":
                payload = ai_response.get("data") or {}
                results = payload.get("results") if isinstance(payload, dict) else []
                for result in results or []:
                    try:
                        match = EG.EagleAIResult.from_response_data(result)
                    except (AttributeError, TypeError, ValueError):
                        continue
                    raw = match.item
                    if raw.get("id") != current_item_id:
                        candidate_map.setdefault(raw["id"], raw)
                        candidate_scores[raw["id"]] = match.score
                        used_ai = True
    except Exception:
        pass

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
            resp = EG.EAGLE_list_items(tags=[tag], limit=120)
        except Exception:
            continue
        _accumulate_from_response(resp)
        if len(candidate_map) >= limit * 2:
            break

    if not candidate_map and folder_ids:
        primary_folders = folder_ids[:2]
        for folder_id in primary_folders:
            try:
                resp = EG.EAGLE_list_items(folders=[folder_id], limit=120)
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

    # AI responses are score-sorted. Keep that order; legacy tag/folder fallback
    # remains sampled so the detail page does not become repetitive.
    sampled_raw = candidate_list[:sample_size] if used_ai else random.sample(candidate_list, sample_size)
    formatted_candidates = _format_eagle_items(sampled_raw, sort_by=None)
    formatted_map = {item.id: item for item in formatted_candidates if getattr(item, "id", None)}

    similar_items = []
    for raw in sampled_raw:
        item_id = raw.get("id")
        formatted = formatted_map.get(item_id)
        if not formatted:
            continue
        media_type = formatted.media_type
        detail_path = f"/EAGLE_video/{item_id}/" if media_type == "video" else f"/EAGLE_image/{item_id}/"
        score = candidate_scores.get(item_id)
        description = f"AI similarity {float(score):.0%}" if score is not None else None
        similar_items.append(MediaEntry(
            id=item_id,
            name=formatted.name or "Untitled",
            url=detail_path,
            thumbnail_route=formatted.thumbnail_route or DEFAULT_THUMBNAIL_ROUTE,
            item_path=formatted.item_path,
            media_type=media_type,
            ext=formatted.ext,
            description=description,
        ))

    return similar_items


def get_eagle_video_details(item_id):
    """
    從 Eagle API 取得單一影片項目的詳細資訊並組合成播放器頁面需要的結構。
    """
    response = EG.EAGLE_get_item_info(item_id)
    if response.get("status") != "success":
        raise ExternalServiceError(f"Failed to fetch Eagle item info: {response.get('data')}")

    item = response.get("data")
    if not item or isinstance(item, list):
        raise MediaNotFound("Video item not found.")

    raw_ext = item.get("ext") or ""
    ext = raw_ext.lower().lstrip(".")
    file_name = item.get("name") or item_id
    file_name_with_ext = item.get("fileName")

    if not ext and file_name_with_ext:
        _, inferred_ext = os.path.splitext(file_name_with_ext)
        ext = inferred_ext.lower().lstrip(".")

    if ext not in VIDEO_EXTENSIONS:
        raise MediaNotFound("Requested Eagle item is not a video.")

    base_library_path = _get_eagle_library_path()
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
        raise MediaNotFound("Video file not found on disk.")

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

    metadata = PageMetadata(
        name=item.get("name") or os.path.basename(video_path),
        category="eagle-video",
        tags=tags,
        path=f"/EAGLE_video/{item_id}/",
        thumbnail_route=thumbnail_route,
        filesystem_path=normalized_abs_path,
        description=item.get("annotation") or item.get("note"),
        folders=folder_links,
        similar=[entry.to_dict() for entry in similar_items],
        ext=resolved_ext
    )

    video_data = MediaDetail(
        name=metadata.name,
        relative_path=relative_path,
        source_url=stream_route,
        original_url=original_url,
        thumbnail_route=thumbnail_route,
        mime_type=mimetypes.guess_type(video_path)[0] or "video/mp4",
        size_bytes=file_size,
        size_display=_human_readable_size(file_size),
        modified_time=modified_time.strftime("%Y-%m-%d %H:%M"),
        parent_url=None,
        download_url=stream_route,
        folders=folder_links,
        ext=resolved_ext
    )

    return metadata, video_data


def get_eagle_image_details(item_id):
    """
    從 Eagle API 取得單一圖片項目的詳細資訊並組合成展示頁面需要的結構。
    """
    response = EG.EAGLE_get_item_info(item_id)
    if response.get("status") != "success":
        raise ExternalServiceError(f"Failed to fetch Eagle item info: {response.get('data')}")

    item = response.get("data")
    if not item or isinstance(item, list):
        raise MediaNotFound("Image item not found.")

    raw_ext = item.get("ext") or ""
    ext = raw_ext.lower().lstrip(".")
    file_name = item.get("name") or item_id
    file_name_with_ext = item.get("fileName")

    base_library_path = _get_eagle_library_path()
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
        raise MediaNotFound("Image file not found on disk.")

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

    metadata = PageMetadata(
        name=item.get("name") or os.path.basename(image_path),
        category="eagle-image",
        tags=tags,
        path=f"/EAGLE_image/{item_id}/",
        thumbnail_route=stream_route,
        filesystem_path=normalized_abs_path,
        description=item.get("annotation") or item.get("note"),
        folders=folder_links,
        similar=[entry.to_dict() for entry in similar_items],
        ext=resolved_ext or None
    )

    image_data = MediaDetail(
        name=metadata.name,
        relative_path=relative_path,
        source_url=stream_route,
        original_url=original_url,
        thumbnail_route=stream_route,
        mime_type=mimetypes.guess_type(image_path)[0] or f"image/{resolved_ext or 'jpeg'}",
        size_bytes=file_size,
        size_display=_human_readable_size(file_size),
        modified_time=modified_time.strftime("%Y-%m-%d %H:%M"),
        parent_url=None,
        download_url=stream_route,
        folders=folder_links,
        ext=resolved_ext or None
    )

    return metadata, image_data


def _eagle_item_media_path(item, library_path):
    item_id = item.get("id")
    item_name = item.get("name") or "unknown"
    item_ext = item.get("ext") or "jpg"
    return f"/serve_image/{library_path}/images/{item_id}.info/{item_name}.{item_ext}"


def _sort_eagle_items(items, sort_by, reverse=False):
    if sort_by is None:
        return list(items)

    def key(item):
        value = item.get(sort_by)
        if sort_by == "name":
            return str(value or "").casefold()
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    return sorted(items, key=key, reverse=reverse)


def _format_eagle_items(image_items, *, sort_by="name", reverse=False, library_path=None):
    """Map Eagle items without mutating their API order unless sorting is requested."""
    image_items = _sort_eagle_items(image_items or [], sort_by, reverse)
    data: list[MediaEntry] = []

    base = library_path or _get_eagle_library_path()
    for image in image_items:
        image_id = image.get("id")
        if not image_id:
            continue
        image_name = image.get("name", "unknown")
        image_ext = image.get("ext", "jpg")
        image_path = _eagle_item_media_path(image, base)

        normalized_ext = (image_ext or "").lower()
        is_video = normalized_ext in VIDEO_EXTENSIONS
        if normalized_ext == "mp4":
            thumbnail_route = f"/serve_image/{base}/images/{image_id}.info/{image_name}_thumbnail.png"
        else:
            thumbnail_route = image_path

        data.append(MediaEntry(
            id=image_id,
            name=image_name,
            url=image_path,
            thumbnail_route=thumbnail_route,
            item_path=os.path.abspath(os.path.join(base, "images", f"{image_id}.info", f"{image_name}.{image_ext}")),
            media_type="video" if is_video else "image",
            ext=normalized_ext or None
        ))

    return data


def get_subfolders_info(folder_id):
    """
    根據指定的 folder_id，取出其 children（子資料夾 id list），
    並組成符合前端展示格式的 list of dict。
    """
    folder, _ = _get_eagle_folder_context(folder_id)
    if not folder:
        return []

    children_infos = folder.get("children") or []
    result = []
    base = _get_eagle_library_path()

    for child_info in children_infos:
        child_id = child_info["id"]
        sub_name = child_info.get("name", f"(unnamed-{child_id})")
        path = f"/EAGLE_folder/{child_id}"

        folder_response = EG.EAGLE_list_items(folders=[child_id], limit=1, fields=["id", "name", "ext"])
        thumbnail_route = DEFAULT_THUMBNAIL_ROUTE
        if folder_response.get("status") == "success" and folder_response.get("data"):
            first_img = folder_response["data"][0]
            thumbnail_route = _eagle_item_media_path(first_img, base)

        result.append(MediaEntry(
            name=sub_name,
            url=path,
            thumbnail_route=thumbnail_route,
            item_path=None,
            media_type="folder"
        ))

    return result
