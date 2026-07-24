"""Shared presentation helpers for Flowinone Flask blueprints."""

from __future__ import annotations

import os
from functools import wraps
from pathlib import Path
from typing import Any

from flask import abort, current_app, g, request, url_for

from config import DB_route_external, DB_route_internal
from src.file_handler import (
    is_eagle_available,
    has_chrome_bookmarks,
    has_db_main,
)
from src.file_handler.thumbnails.store import get_thumbnail_store


def path_is_within_roots(target_path: str, roots: list[str]) -> bool:
    """Return whether a path is contained by one of the configured roots."""
    try:
        normalized_target = os.path.abspath(target_path)
        for root in roots:
            if not root:
                continue
            normalized_root = os.path.abspath(root)
            if os.path.commonpath([normalized_target, normalized_root]) == normalized_root:
                return True
    except ValueError:
        return False
    return False


def to_dict(obj: Any) -> dict:
    """Convert project dataclasses or mappings to a plain dictionary."""
    return obj.to_dict() if hasattr(obj, "to_dict") else obj


def serialize_payload(metadata: Any, data: list[Any]) -> tuple[dict, list[dict]]:
    return to_dict(metadata), [to_dict(item) for item in data]


def serialize_detail(detail: Any) -> dict:
    return to_dict(detail)


def format_bytes(size_bytes: Any) -> str | None:
    if size_bytes is None:
        return None
    try:
        size = float(size_bytes)
    except (TypeError, ValueError):
        return None

    units = ["B", "KB", "MB", "GB", "TB"]
    unit_index = 0
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1
    if unit_index == 0:
        return f"{int(size)} {units[unit_index]}"
    return f"{size:.1f} {units[unit_index]}"


def media_type_label(media_type: str | None, ext: str | None = None) -> str:
    media_type = (media_type or "item").lower()
    ext = (ext or "").lower()
    if media_type == "folder":
        return "Collection"
    if media_type == "bookmark":
        return "Bookmark"
    if media_type == "video":
        return f"{ext.upper()} Video" if ext else "Video"
    if media_type == "image":
        return f"{ext.upper()} Image" if ext else "Image"
    if media_type == "file":
        return f"{ext.upper()} File" if ext else "File"
    return media_type.replace("_", " ").title()


def eagle_detail_url(item: dict, return_to: str | None = None) -> str:
    item_id = item.get("id")
    media_type = item.get("media_type")
    if item_id and media_type == "video":
        return url_for("eagle.view_eagle_video", item_id=item_id, return_to=return_to)
    if item_id and media_type == "image":
        return url_for("eagle.view_eagle_image", item_id=item_id, return_to=return_to)
    return item.get("url") or item.get("path") or "#"


def content_card_from_media(
    item: Any,
    source_label: str = "Eagle",
    why: str | None = None,
    lane: str | None = None,
    return_to: str | None = None,
) -> dict:
    item = to_dict(item)
    media_type = item.get("media_type", "item")
    ext = item.get("ext")
    tags = item.get("tags") or item.get("folder_labels") or []
    if isinstance(tags, str):
        tags = [tags]
    if not tags and ext:
        tags = [ext]

    title = item.get("name") or item.get("title") or "Untitled"
    url = eagle_detail_url(item, return_to=return_to)
    meta_bits = [source_label, media_type_label(media_type, ext)]
    path_display = item.get("path_display") or item.get("relative_path")
    if path_display:
        meta_bits.append(path_display)
    return {
        "id": item.get("id") or item.get("item_id"),
        "title": title,
        "name": title,
        "url": url,
        "path": url,
        "thumbnail_route": item.get("thumbnail_route")
        or url_for("static", filename="default_thumbnail.svg"),
        "media_type": media_type,
        "type_label": media_type_label(media_type, ext),
        "source_label": source_label,
        "meta_line": " · ".join(bit for bit in meta_bits if bit),
        "tags": tags[:4],
        "why": why or "最近可用",
        "lane": lane or "explore",
        "status": item.get("status") or "ready",
        "target_blank": media_type == "bookmark" or str(url).startswith("http"),
        "primary_action": "開啟",
        "is_available": bool(url and url != "#"),
        "disabled_reason": None,
    }


def content_card_from_db_item(
    item: dict, why: str | None = None, lane: str | None = None
) -> dict:
    item_type = item.get("item_type") or "file"
    relative_path = item.get("relative_path") or ""
    absolute_path = item.get("absolute_path") or ""
    is_available = bool(absolute_path and os.path.exists(absolute_path))
    is_allowed = is_available and path_is_within_roots(
        absolute_path, [DB_route_external, DB_route_internal]
    )
    item_url = None
    if is_allowed:
        source = "external"
        if (
            DB_route_internal
            and os.path.abspath(DB_route_internal) != os.path.abspath(DB_route_external)
            and path_is_within_roots(absolute_path, [DB_route_internal])
        ):
            source = "internal"
        if item_type == "folder":
            item_url = url_for("local.view_both", folder_path=relative_path, src=source)
        elif item_type == "video":
            item_url = url_for("media.view_video", video_path=relative_path, src=source)
        elif item_type == "image":
            item_url = url_for("media.view_image", image_path=relative_path, src=source)
        else:
            item_url = url_for("local.open_filesystem_path", path=absolute_path)

    ext = item.get("ext")
    tags = item.get("tags") or []
    size_display = format_bytes(item.get("size_bytes"))
    meta_bits = ["Local DB", media_type_label(item_type, ext)]
    if size_display:
        meta_bits.append(size_display)
    return {
        "id": item.get("item_id"),
        "title": item.get("name") or "Untitled",
        "name": item.get("name") or "Untitled",
        "url": item_url,
        "path": item_url,
        "thumbnail_route": item.get("thumbnail_route")
        if is_allowed and item.get("thumbnail_route")
        else url_for("static", filename="default_thumbnail.svg"),
        "media_type": item_type,
        "type_label": media_type_label(item_type, ext),
        "source_label": item.get("data_source") or "filesystem",
        "meta_line": " · ".join(meta_bits),
        "tags": tags[:4],
        "why": why or "來自本機資料庫",
        "lane": lane or "local",
        "status": "indexed" if is_allowed else "stale",
        "target_blank": False,
        "primary_action": "開啟" if is_allowed else "來源已失效",
        "updated_at": item.get("updated_at"),
        "missing_thumbnail": not item.get("thumbnail_route"),
        "missing_tags": not bool(tags),
        "is_available": is_allowed,
        "disabled_reason": None
        if is_allowed
        else "檔案已移動、刪除，或不在目前資料庫根目錄",
    }


def content_card_from_resource(
    item: dict, why: str | None = None, lane: str | None = None
) -> dict:
    media_id = item.get("thumbnail_media_id")
    if item.get("thumbnail_path"):
        thumbnail_route = url_for(
            "resource_library.resource_asset", resource_id=item["id"], kind="thumbnail"
        )
    elif media_id and get_thumbnail_store().get_thumbnail_path(media_id):
        thumbnail_route = url_for(
            "chrome.serve_bookmark_thumbnail", media_id=media_id
        )
    else:
        thumbnail_route = url_for("static", filename="default_thumbnail.svg")
    source_type = item.get("source_type") or "resource"
    meta_bits = ["Resource", source_type]
    if item.get("domain"):
        meta_bits.append(item["domain"])
    detail_url = url_for(
        "resource_library.resource_detail", resource_id=item["id"]
    )
    return {
        "id": item.get("id"),
        "title": item.get("title") or "Untitled",
        "name": item.get("title") or "Untitled",
        "url": detail_url,
        "path": detail_url,
        "thumbnail_route": thumbnail_route,
        "media_type": "resource",
        "type_label": source_type.replace("_", " ").title(),
        "source_label": "Resource Library",
        "meta_line": " · ".join(meta_bits),
        "tags": (item.get("tag_names") or [])[:4],
        "why": why
        or item.get("why_this_matters")
        or item.get("summary_one_line")
        or "查看資源資料",
        "lane": lane or "resource",
        "status": "ready",
        "target_blank": False,
        "primary_action": "開啟",
        "is_available": True,
        "disabled_reason": None,
    }


def clone_card(card: dict, why: str | None = None, lane: str | None = None) -> dict:
    cloned = dict(card)
    cloned["tags"] = list(card.get("tags") or [])
    if why is not None:
        cloned["why"] = why
    if lane is not None:
        cloned["lane"] = lane
    return cloned


def build_shelf(
    title: str,
    kicker: str,
    items: list[dict],
    layout: str = "rail",
    action_label: str | None = None,
    action_url: str | None = None,
) -> dict:
    return {
        "title": title,
        "kicker": kicker,
        "items": items,
        "layout": layout,
        "action_label": action_label,
        "action_url": action_url,
    }


def decorate_related_items(items: list[dict] | None, metadata: dict | None = None) -> list[dict]:
    decorated = []
    metadata = metadata or {}
    tags = metadata.get("tags") or []
    folders = metadata.get("folders") or []
    for item in items or []:
        decorated.append(
            content_card_from_media(
                item,
                source_label="Related",
                why=item.get("description")
                or ("同 tag" if tags else "同來源資料夾" if folders else "相鄰項目"),
                lane="up-next",
            )
        )
    return decorated


def catalog_related_for_origin(
    source_kind: str, source_key: str, limit: int = 12
) -> list[dict]:
    try:
        from sqlalchemy import text

        from src.flowinone.catalog.discovery import DiscoveryService
        from src.flowinone.resource_library.database import get_resource_database

        configured = current_app.config.get("FLOWINONE_RESOURCE_DB_PATH")
        database = get_resource_database(Path(configured) if configured else None)
        with database.engine.connect() as conn:
            item_id = conn.execute(
                text(
                    "SELECT catalog_item_id FROM catalog_origins "
                    "WHERE source_kind=:source "
                    "AND (source_key=:key OR source_path=:key) AND stale=0 LIMIT 1"
                ),
                {"source": source_kind, "key": source_key},
            ).scalar_one_or_none()
        if not item_id:
            return []
        return [
            {
                "title": item["title"],
                "url": item.get("primary_detail_uri")
                or item.get("original_url")
                or "#",
                "thumbnail_route": item.get("thumbnail_ref")
                or url_for("static", filename="default_thumbnail.svg"),
                "why": "、".join((item.get("reason") or {}).get("shared") or [])
                or item.get("relation_type", "相關項目"),
            }
            for item in DiscoveryService(database).related_items(item_id, limit=limit)
        ]
    except Exception:
        return []


def merge_related(
    primary: list[dict], catalog_items: list[dict], limit: int = 18
) -> list[dict]:
    seen = set()
    output = []
    for item in [*primary, *catalog_items]:
        key = item.get("url") or item.get("title")
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(item)
        if len(output) >= limit:
            break
    return output


def build_detail_actions(media_kind: str, metadata: dict | None) -> list[dict]:
    metadata = metadata or {}
    actions = [
        {
            "id": "back",
            "label": "返回",
            "description": "回到上一個瀏覽頁或來源資料夾。",
            "kind": "secondary",
        },
        {
            "id": "related",
            "label": "找相似" if media_kind != "video" else "看相關",
            "description": "依 tag 與資料夾脈絡繼續探索。",
            "kind": "primary",
        },
    ]
    if metadata.get("filesystem_path"):
        actions.append(
            {
                "id": "folder",
                "label": "開啟資料夾",
                "description": "查看原始檔案位置。",
                "kind": "secondary",
                "url": url_for(
                    "local.open_filesystem_path", path=metadata["filesystem_path"]
                ),
            }
        )
    return actions


def compute_feature_flags() -> dict[str, bool]:
    chrome_available = has_chrome_bookmarks()
    return {
        "eagle": is_eagle_available(),
        "chrome": chrome_available,
        "youtube": chrome_available,
        "db": has_db_main(),
    }


def get_feature_flags() -> dict[str, bool]:
    if not hasattr(g, "feature_flags"):
        g.feature_flags = compute_feature_flags()
    return g.feature_flags


def require_feature(flag_name: str):
    """Reject a source route when its backing integration is unavailable."""

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not get_feature_flags().get(flag_name):
                abort(404)
            return view_func(*args, **kwargs)

        return wrapped

    return decorator


def normalize_current_url() -> str:
    current_url = request.full_path
    return current_url[:-1] if current_url and current_url.endswith("?") else current_url
