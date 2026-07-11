import os
import json
import platform
import random
import subprocess
from collections import Counter
from functools import wraps
from pathlib import Path
from urllib.parse import unquote
import click
from flask import Flask, render_template, abort, send_from_directory, request, redirect, url_for, jsonify, g, send_file, current_app
from src.file_handler import (
    AccessDenied,
    BookmarkNotFound,
    BookmarkError,
    ExternalServiceError,
    FolderNotFound,
    MediaNotFound,
    get_all_folders_info,
    get_folder_images,
    get_image_details,
    get_video_details,
    get_eagle_folders,
    get_eagle_images_by_folderid,
    get_eagle_images_by_tag,
    get_eagle_tags,
    get_eagle_smart_folders,
    get_eagle_images_by_smart_folder_id,
    search_eagle_items,
    get_eagle_stream_items,
    get_chrome_bookmarks,
    get_chrome_youtube_bookmarks,
    get_eagle_image_details,
    get_eagle_video_details,
    get_subfolders_info,
    is_eagle_available,
    update_item_database,
    update_missing_thumbnails,
    clear_thumbnails,
    fetch_items,
    has_chrome_bookmarks,
    has_db_main,
)
from config import DB_route_internal, DB_route_external
from src.file_handler.chrome_bookmarks import iter_chrome_bookmark_records
from src.file_handler.thumbnails.store import PRIORITY_MISSING, PRIORITY_VISIBLE, get_thumbnail_store
from src.file_handler.thumbnails.worker import ThumbnailWorker

SYSTEM_NAME = platform.system()
IS_MACOS = SYSTEM_NAME == "Darwin"
IS_WINDOWS = SYSTEM_NAME == "Windows"


def _path_is_within_roots(target_path, roots):
    """Ensure the requested path stays inside one of the configured roots."""
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


def _open_in_file_manager(target_path):
    """Open a folder in the host file manager."""
    if IS_MACOS:
        subprocess.Popen(["open", target_path])
    elif IS_WINDOWS:
        os.startfile(target_path)  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", target_path])


def _to_dict(obj):
    """Convert dataclasses or plain objects to dict for templates."""
    return obj.to_dict() if hasattr(obj, "to_dict") else obj


def _serialize_payload(metadata, data):
    meta_dict = _to_dict(metadata)
    items = [_to_dict(item) for item in data]
    return meta_dict, items


def _serialize_detail(detail):
    return _to_dict(detail)


def _format_bytes(size_bytes):
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


def _media_type_label(media_type, ext=None):
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


def _eagle_detail_url(item, return_to=None):
    item_id = item.get("id")
    media_type = item.get("media_type")
    if item_id and media_type == "video":
        if return_to:
            return url_for("view_eagle_video", item_id=item_id, return_to=return_to)
        return url_for("view_eagle_video", item_id=item_id)
    if item_id and media_type == "image":
        if return_to:
            return url_for("view_eagle_image", item_id=item_id, return_to=return_to)
        return url_for("view_eagle_image", item_id=item_id)
    return item.get("url") or item.get("path") or "#"


def _content_card_from_media(item, source_label="Eagle", why=None, lane=None, return_to=None):
    item = _to_dict(item)
    media_type = item.get("media_type", "item")
    ext = item.get("ext")
    tags = item.get("tags") or item.get("folder_labels") or []
    if isinstance(tags, str):
        tags = [tags]
    if not tags and ext:
        tags = [ext]

    title = item.get("name") or item.get("title") or "Untitled"
    url = _eagle_detail_url(item, return_to=return_to)
    target_blank = media_type == "bookmark" or str(url).startswith("http")
    type_label = _media_type_label(media_type, ext)
    meta_bits = [source_label, type_label]
    path_display = item.get("path_display") or item.get("relative_path")
    if path_display:
        meta_bits.append(path_display)

    return {
        "id": item.get("id") or item.get("item_id"),
        "title": title,
        "name": title,
        "url": url,
        "path": url,
        "thumbnail_route": item.get("thumbnail_route") or url_for("static", filename="default_thumbnail.svg"),
        "media_type": media_type,
        "type_label": type_label,
        "source_label": source_label,
        "meta_line": " · ".join([bit for bit in meta_bits if bit]),
        "tags": tags[:4],
        "why": why or "最近可用",
        "lane": lane or "explore",
        "status": item.get("status") or "ready",
        "target_blank": target_blank,
        "primary_action": "開啟",
        "is_available": bool(url and url != "#"),
        "disabled_reason": None,
    }


def _content_card_from_db_item(item, why=None, lane=None):
    item_type = item.get("item_type") or "file"
    relative_path = item.get("relative_path") or ""
    absolute_path = item.get("absolute_path") or ""
    is_available = bool(absolute_path and os.path.exists(absolute_path))
    is_allowed = is_available and _path_is_within_roots(
        absolute_path,
        [DB_route_external, DB_route_internal],
    )
    item_url = None
    if is_allowed:
        source = "external"
        if (
            DB_route_internal
            and os.path.abspath(DB_route_internal) != os.path.abspath(DB_route_external)
            and _path_is_within_roots(absolute_path, [DB_route_internal])
        ):
            source = "internal"

        if item_type == "folder":
            item_url = url_for("view_both", folder_path=relative_path, src=source)
        elif item_type == "video":
            item_url = url_for("view_video", video_path=relative_path, src=source)
        elif item_type == "image":
            item_url = url_for("view_image", image_path=relative_path, src=source)
        else:
            item_url = url_for("open_filesystem_path", path=absolute_path)

    ext = item.get("ext")
    tags = item.get("tags") or []
    size_display = _format_bytes(item.get("size_bytes"))
    meta_bits = ["Local DB", _media_type_label(item_type, ext)]
    if size_display:
        meta_bits.append(size_display)

    return {
        "id": item.get("item_id"),
        "title": item.get("name") or "Untitled",
        "name": item.get("name") or "Untitled",
        "url": item_url,
        "path": item_url,
        "thumbnail_route": (
            item.get("thumbnail_route")
            if is_allowed and item.get("thumbnail_route")
            else url_for("static", filename="default_thumbnail.svg")
        ),
        "media_type": item_type,
        "type_label": _media_type_label(item_type, ext),
        "source_label": item.get("data_source") or "filesystem",
        "meta_line": " · ".join(meta_bits),
        "tags": tags[:4],
        "why": why or "來自本機資料庫",
        "lane": lane or "library",
        "status": "archived" if item.get("is_archived") else "inbox" if is_allowed else "stale",
        "target_blank": False,
        "primary_action": "開啟" if is_allowed else "來源已失效",
        "updated_at": item.get("updated_at"),
        "missing_thumbnail": not item.get("thumbnail_route"),
        "missing_tags": not bool(tags),
        "is_available": is_allowed,
        "disabled_reason": None if is_allowed else "檔案已移動、刪除，或不在目前資料庫根目錄",
    }


def _content_card_from_resource(item, why=None, lane=None):
    """Adapt a durable Resource Library record to the shared homepage card read model."""
    media_id = item.get("thumbnail_media_id")
    if item.get("thumbnail_path"):
        thumbnail_route = url_for(
            "resource_library.resource_asset",
            resource_id=item["id"],
            kind="thumbnail",
        )
    elif media_id:
        thumbnail_route = (
            url_for("serve_bookmark_thumbnail", media_id=media_id)
            if get_thumbnail_store().get_thumbnail_path(media_id)
            else url_for("static", filename="default_thumbnail.svg")
        )
    else:
        thumbnail_route = url_for("static", filename="default_thumbnail.svg")
    source_type = item.get("source_type") or "resource"
    meta_bits = ["Resource", source_type]
    if item.get("domain"):
        meta_bits.append(item["domain"])
    return {
        "id": item.get("id"),
        "title": item.get("title") or "Untitled",
        "name": item.get("title") or "Untitled",
        "url": url_for("resource_library.resource_detail", resource_id=item["id"]),
        "path": url_for("resource_library.resource_detail", resource_id=item["id"]),
        "thumbnail_route": thumbnail_route,
        "media_type": "resource",
        "type_label": source_type.replace("_", " ").title(),
        "source_label": "Resource Library",
        "meta_line": " · ".join(meta_bits),
        "tags": (item.get("tag_names") or [])[:4],
        "why": why or item.get("why_this_matters") or item.get("summary_one_line") or "等待整理",
        "lane": lane or "resource",
        "status": item.get("reading_state") or "inbox",
        "target_blank": False,
        "primary_action": "整理",
        "is_available": True,
        "disabled_reason": None,
    }


def _clone_card(card, why=None, lane=None):
    """Copy a card before assigning shelf-specific recommendation context."""
    cloned = dict(card)
    cloned["tags"] = list(card.get("tags") or [])
    if why is not None:
        cloned["why"] = why
    if lane is not None:
        cloned["lane"] = lane
    return cloned


def _build_shelf(title, kicker, items, layout="rail", action_label=None, action_url=None):
    return {
        "title": title,
        "kicker": kicker,
        "items": items,
        "layout": layout,
        "action_label": action_label,
        "action_url": action_url,
    }


def _decorate_related_items(items, metadata=None):
    decorated = []
    metadata = metadata or {}
    tags = metadata.get("tags") or []
    folders = metadata.get("folders") or []
    for item in items or []:
        card = _content_card_from_media(
            item,
            source_label="Related",
            why="同 tag" if tags else "同集合" if folders else "相鄰項目",
            lane="up-next",
        )
        decorated.append(card)
    return decorated


def _build_detail_actions(media_kind, metadata):
    metadata = metadata or {}
    actions = [
        {
            "id": "back",
            "label": "返回",
            "description": "回到上一個 shelf 或集合頁。",
            "kind": "secondary",
        },
        {
            "id": "related",
            "label": "找相似",
            "description": "依 tag 與資料夾脈絡繼續探索。",
            "kind": "primary",
        },
        {
            "id": "note",
            "label": "轉成筆記",
            "description": "把個人筆記與 AI summary 分開沉澱。",
            "kind": "secondary",
        },
    ]
    if metadata.get("filesystem_path"):
        actions.append({
            "id": "folder",
            "label": "開啟資料夾",
            "description": "查看原始檔案位置。",
            "kind": "secondary",
            "url": url_for("open_filesystem_path", path=metadata["filesystem_path"]),
        })
    if media_kind == "video":
        actions[1]["label"] = "看相關"
    return actions


def _get_inspiration_collections():
    """Load collection choices without making media browsing depend on the resource DB."""
    try:
        from src.flowinone.resource_library.curation import CollectionService
        from src.flowinone.resource_library.database import get_resource_database

        configured = current_app.config.get("FLOWINONE_RESOURCE_DB_PATH")
        database = get_resource_database(Path(configured)) if configured else get_resource_database()
        return CollectionService(database).list()
    except Exception:
        current_app.logger.exception("Failed to load inspiration collections")
        return []


def _compute_feature_flags():
    eagle_available = is_eagle_available()
    chrome_available = has_chrome_bookmarks()
    db_available = has_db_main()
    return {
        "eagle": eagle_available,
        "chrome": chrome_available,
        "youtube": chrome_available,
        "db": db_available
    }


def _get_feature_flags():
    if not hasattr(g, "feature_flags"):
        g.feature_flags = _compute_feature_flags()
    return g.feature_flags


def require_feature(flag_name):
    """Decorator to enforce feature availability on a route."""
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            flags = _get_feature_flags()
            if not flags.get(flag_name):
                abort(404)
            return view_func(*args, **kwargs)
        return wrapped
    return decorator


def _normalize_current_url():
    """Strip the trailing ? from request.full_path to keep return_to clean."""
    current_url = request.full_path
    if current_url and current_url.endswith('?'):
        return current_url[:-1]
    return current_url


def _attach_detail_urls(items, current_url):
    """Attach detail URLs (with return_to) to media items in-place."""
    for item in items:
        item_id = item.get("id")
        if not item_id:
            continue
        if item.get("media_type") == "video":
            item["url"] = url_for("view_eagle_video", item_id=item_id, return_to=current_url)
        elif item.get("media_type") == "image":
            item["url"] = url_for("view_eagle_image", item_id=item_id, return_to=current_url)
    return items


def _render_media_view(template_name, folder_path, source=None):
    """Shared renderer for folder-based media views."""
    try:
        if source is None:
            metadata, data = get_folder_images(folder_path)
        else:
            metadata, data = get_folder_images(folder_path, source)
    except AccessDenied:
        abort(403)
    except (FolderNotFound, MediaNotFound, BookmarkNotFound):
        abort(404)
    except ExternalServiceError as exc:
        abort(500, description=str(exc))
    metadata_dict, data_list = _serialize_payload(metadata, data)
    return render_template(template_name, metadata=metadata_dict, data=data_list)


def _build_index_context(flags, active_mode="explore"):
    """Prepare task-oriented homepage shelves and exclude unavailable records."""
    mode_copy = {
        "explore": ("探索台", "從最近內容、視覺線索與主題入口開始。"),
        "process": ("整理台", "處理待標記、待補縮圖與失效索引。"),
        "project": ("專案台", "沿集合與 tag 找回可重用素材。"),
        "review": ("回顧台", "檢視近期內容與 metadata 較完整的項目。"),
    }
    if active_mode not in mode_copy:
        active_mode = "explore"

    context = {
        "active_mode": active_mode,
        "mode_heading": mode_copy[active_mode][0],
        "mode_description": mode_copy[active_mode][1],
        "modes": [
            {
                "id": mode_id,
                "label": label,
                "url": url_for("index", mode=mode_id),
            }
            for mode_id, (label, _) in mode_copy.items()
        ],
        "hero_item": None,
        "home_shelves": [],
        "tag_cloud": [],
        "show_tag_cloud": active_mode in {"explore", "project", "review"},
        "source_summary": [],
        "action_queue": [],
        "stale_db_count": 0,
        "fallback_heading": "尚未有可探索內容",
        "fallback_message": "連接 Eagle、本機資料庫或 Chrome 書籤後，這裡會形成你的內容工作台。",
        "has_content": False,
    }

    all_cards = []
    eagle_cards = []
    db_cards = []
    resource_cards = []
    image_cards = []
    video_cards = []
    folder_cards = []

    if flags.get("eagle"):
        try:
            current_url = _normalize_current_url()
            stream_payload = get_eagle_stream_items(offset=0, limit=48)
            eagle_cards = [
                _content_card_from_media(
                    item,
                    source_label="Eagle",
                    why="Eagle 最近更新",
                    lane="continue",
                    return_to=current_url,
                )
                for item in stream_payload
            ]
            all_cards.extend(eagle_cards)
            image_cards.extend(card for card in eagle_cards if card["media_type"] == "image")
            video_cards.extend(card for card in eagle_cards if card["media_type"] == "video")

            _, folder_data_raw = get_eagle_folders()
            folder_cards.extend(
                _content_card_from_media(
                    item,
                    source_label="Eagle",
                    why="Eagle 集合入口",
                    lane="source",
                )
                for item in folder_data_raw
            )

            _, tag_data = get_eagle_tags()
            context["tag_cloud"].extend([
                {
                    "name": tag.get("name", "").strip(),
                    "count": tag.get("count"),
                    "url": url_for("view_images_by_tag", target_tag=tag.get("name", "").strip()),
                    "source": "Eagle",
                }
                for tag in tag_data[:24]
                if tag.get("name", "").strip()
            ])
            context["source_summary"].append({
                "label": "Eagle",
                "value": len(eagle_cards),
                "hint": "首頁取樣",
                "tone": "ready",
                "url": url_for("eagle_stream"),
            })
        except Exception:
            current_app.logger.exception("Failed to build Eagle homepage context")
            context["source_summary"].append({
                "label": "Eagle",
                "value": "離線",
                "hint": "無法讀取來源",
                "tone": "warning",
                "url": None,
            })

    if flags.get("db"):
        try:
            payload = fetch_items(limit=1000, offset=0)
            raw_db_cards = [
                _content_card_from_db_item(item, why="本機資料庫", lane="inbox")
                for item in payload.get("items", [])
            ]
            db_cards = [card for card in raw_db_cards if card.get("is_available")]
            stale_count = sum(not card.get("is_available") for card in raw_db_cards)
            context["stale_db_count"] = stale_count

            all_cards.extend(db_cards)
            image_cards.extend(card for card in db_cards if card["media_type"] == "image")
            video_cards.extend(card for card in db_cards if card["media_type"] == "video")
            folder_cards.extend(card for card in db_cards if card["media_type"] == "folder")

            tag_counts = Counter()
            for card in db_cards:
                tag_counts.update(card.get("tags") or [])
            for tag_name, count in tag_counts.most_common(16):
                context["tag_cloud"].append({
                    "name": tag_name,
                    "count": count,
                    "url": url_for("view_item_db", tag=tag_name),
                    "source": "Local DB",
                })

            total = payload.get("total", len(raw_db_cards))
            context["source_summary"].append({
                "label": "Local DB",
                "value": f"{len(db_cards)} / {total}",
                "hint": "可用 / 已索引" if not stale_count else f"{stale_count} 筆需同步",
                "tone": "ready" if not stale_count else "warning",
                "url": url_for("view_item_db"),
            })
        except Exception:
            current_app.logger.exception("Failed to build DB homepage context")

    if flags.get("chrome"):
        context["source_summary"].append({
            "label": "Chrome",
            "value": "可用",
            "hint": "書籤來源",
            "tone": "ready",
            "url": url_for("view_chrome_root"),
        })

    try:
        from src.flowinone.resource_library.database import get_resource_database
        from src.flowinone.resource_library.service import ResourceService

        configured_resource_db = current_app.config.get("FLOWINONE_RESOURCE_DB_PATH")
        resource_service = ResourceService(
            get_resource_database(Path(configured_resource_db))
            if configured_resource_db
            else None
        )
        resource_page = resource_service.repository.list(
            dispositions=("active",),
            page=1,
            per_page=48,
        )
        resource_cards = [
            _content_card_from_resource(item, lane="resource")
            for item in resource_page.items
        ]
        all_cards.extend(resource_cards)
        image_cards.extend(
            card
            for card, item in zip(resource_cards, resource_page.items)
            if item.get("source_type") == "image"
        )
        video_cards.extend(
            card
            for card, item in zip(resource_cards, resource_page.items)
            if item.get("source_type") == "video"
        )
        context["source_summary"].append({
            "label": "Resources",
            "value": resource_page.total,
            "hint": "可整理資源",
            "tone": "ready",
            "url": url_for("resource_library.resource_index"),
        })
    except Exception:
        current_app.logger.exception("Failed to build Resource Library homepage context")

    cleanup_items = [
        card for card in db_cards
        if card.get("missing_thumbnail") or card.get("missing_tags")
    ]
    tagged_cards = sorted(
        (card for card in all_cards if card.get("tags")),
        key=lambda card: len(card.get("tags") or []),
        reverse=True,
    )

    shelves = []
    if active_mode == "explore":
        if all_cards:
            shelves.append(_build_shelf(
                "繼續探索",
                "最近可用，不需要先回到資料表",
                [_clone_card(card, "最近更新", "continue") for card in all_cards[:10]],
                action_label="查看全部" if flags.get("eagle") else None,
                action_url=url_for("eagle_stream") if flags.get("eagle") else None,
            ))
        if image_cards:
            sample = random.sample(image_cards, min(10, len(image_cards)))
            shelves.append(_build_shelf(
                "視覺岔路",
                "小批次隨機探索，每次停在可控制的範圍",
                [_clone_card(card, "隨機靈感", "inspiration") for card in sample],
            ))
        if video_cards:
            shelves.append(_build_shelf(
                "接著觀看",
                "不自動播放，由你決定下一個",
                [_clone_card(card, "影片候選", "watch-next") for card in video_cards[:8]],
            ))
        if folder_cards:
            shelves.append(_build_shelf(
                "集合入口",
                "沿資料夾與內容集合進入脈絡",
                [_clone_card(card, "集合入口", "source") for card in folder_cards[:10]],
                layout="compact",
                action_label="所有集合" if flags.get("eagle") else None,
                action_url=url_for("list_all_eagle_folder") if flags.get("eagle") else None,
            ))
    elif active_mode == "process":
        if resource_cards:
            shelves.append(_build_shelf(
                "Resource Inbox",
                "把未讀素材轉成可搜尋、可整併的個人知識",
                [_clone_card(card, "等待閱讀或整理", "resource-inbox") for card in resource_cards[:12]],
                action_label="打開 Resource Flow",
                action_url=url_for("resource_library.resource_index"),
            ))
        if db_cards:
            shelves.append(_build_shelf(
                "待整理 Inbox",
                "補上 tags、筆記與專案關聯，讓內容可以再被找到",
                [_clone_card(card, "等待整理", "inbox") for card in db_cards[:12]],
                action_label="檢查索引",
                action_url=url_for("view_item_db"),
            ))
        if cleanup_items:
            cleanup_cards = []
            for card in cleanup_items[:12]:
                if card.get("missing_thumbnail") and card.get("missing_tags"):
                    reason = "缺縮圖與 tags"
                elif card.get("missing_thumbnail"):
                    reason = "缺縮圖"
                else:
                    reason = "缺 tags"
                cleanup_cards.append(_clone_card(card, reason, "cleanup"))
            shelves.append(_build_shelf(
                "清理佇列",
                "先修復會直接改善掃描與推薦品質的缺口",
                cleanup_cards,
                layout="compact",
                action_label="補縮圖",
                action_url=url_for("update_thumbnails_route"),
            ))
    elif active_mode == "project":
        if folder_cards:
            shelves.append(_build_shelf(
                "專案與集合",
                "以集合為邊界整理素材，不讓檔案散回總庫",
                [_clone_card(card, "集合脈絡", "project") for card in folder_cards[:16]],
                layout="compact",
                action_label="所有集合" if flags.get("eagle") else None,
                action_url=url_for("list_all_eagle_folder") if flags.get("eagle") else None,
            ))
        if tagged_cards:
            shelves.append(_build_shelf(
                "可重用素材",
                "已有主題線索，適合拉進下一個專案",
                [_clone_card(card, "已有 metadata", "project-ready") for card in tagged_cards[:12]],
            ))
    elif active_mode == "review":
        if tagged_cards:
            shelves.append(_build_shelf(
                "Metadata 較完整",
                "依可解釋的 metadata 完整度排序，不假裝成熱門排行",
                [_clone_card(card, f"{len(card.get('tags') or [])} 個標記線索", "review") for card in tagged_cards[:12]],
                layout="compact",
            ))
        if all_cards:
            shelves.append(_build_shelf(
                "近期取樣",
                "回看最近進入 Flowinone 的內容",
                [_clone_card(card, "最近更新", "recent") for card in all_cards[:12]],
            ))

    hero_candidates = all_cards
    if active_mode == "process":
        hero_candidates = resource_cards or db_cards
        if not hero_candidates and context["stale_db_count"]:
            context["fallback_heading"] = "本機索引需要同步"
            context["fallback_message"] = (
                f"目前 {context['stale_db_count']} 筆紀錄的來源已不存在，"
                "同步後再開始整理。"
            )
    elif active_mode == "project":
        hero_candidates = folder_cards or tagged_cards
    elif active_mode == "review":
        hero_candidates = tagged_cards or all_cards

    if hero_candidates:
        context["hero_item"] = _clone_card(hero_candidates[0], "從這裡繼續", "featured")

    actions = [{
        "label": "整理 Resource Inbox",
        "description": "閱讀、標記、建立靈感集合並升級為 Obsidian 筆記。",
        "url": url_for("resource_library.resource_index"),
        "enabled": True,
        "tone": "neutral",
    }]
    if context["stale_db_count"]:
        actions.append({
            "label": "同步本機索引",
            "description": f"{context['stale_db_count']} 筆來源已移動或刪除，先清掉失效入口。",
            "url": url_for("update_item_db_route"),
            "enabled": True,
            "tone": "warning",
        })
    elif flags.get("db"):
        actions.append({
            "label": "整理 Inbox",
            "description": "把新內容連到 tags、筆記與專案。",
            "url": url_for("view_item_db"),
            "enabled": True,
            "tone": "neutral",
        })
    if flags.get("eagle"):
        actions.append({
            "label": "沿 tag 探索",
            "description": "從主題線索開出下一條內容路徑。",
            "url": url_for("list_eagle_tags"),
            "enabled": True,
            "tone": "neutral",
        })
    if flags.get("chrome"):
        actions.append({
            "label": "處理書籤",
            "description": "回到尚未沉澱成筆記的網頁素材。",
            "url": url_for("view_chrome_root"),
            "enabled": True,
            "tone": "neutral",
        })
    context["home_shelves"] = shelves
    context["action_queue"] = actions[:3]
    context["has_content"] = bool(all_cards or folder_cards or context["tag_cloud"])
    return context


def register_routes_debug(app):
    @app.route('/debug/')
    def debug_print():
        # df_folders_info = EG.EAGLE_get_folders_df()
        # print(df_folders_info.shape)
        # print(df_folders_info.columns)
        df_to_post = [
            {'author': 'Lynn','title': 'Blog Post 1','content': 'First post content','date_posted': 'September 3, 2018'},
            {'author': 'Lydia','title': 'Blog Post 2','content': 'Second post content','date_posted': 'September 6, 2018'}
            ]
        return render_template('test_arg.html', title='All in One', df_to_post=df_to_post)

def register_routes(app):
    """
    註冊 Flask 路由
    """

    _register_context_processors(app)
    _register_index_routes(app)
    _register_filesystem_routes(app)
    _register_folder_routes(app)
    _register_item_db_routes(app)
    _register_item_db_debug_routes(app)
    _register_chrome_routes(app)
    _register_eagle_routes(app)
    _register_media_routes(app)
    _register_thumbnail_cli(app)
    from src.flowinone.resource_library.blueprint import register_resource_library
    from src.flowinone.entry_system.blueprint import register_entry_system
    from src.flowinone.gallery.blueprint import register_gallery

    register_resource_library(app)
    register_entry_system(app)
    register_gallery(app)


def _register_context_processors(app):
    @app.context_processor
    def inject_feature_flags():
        return {"feature_flags": _get_feature_flags()}


def _register_index_routes(app):
    @app.route('/')
    def index():
        """Default to BUILD; preserve old query-mode links as the content library."""
        if request.args.get("mode"):
            return redirect(url_for("content_library", mode=request.args.get("mode")))
        return redirect(url_for("entry_system.build_dashboard"))

    @app.route('/library/')
    def content_library():
        """Legacy content workbench, retained as a secondary discovery surface."""
        flags = _get_feature_flags()
        active_mode = request.args.get("mode", "explore").strip().lower()
        context = _build_index_context(flags, active_mode=active_mode)
        return render_template('index.html', **context)


def _register_filesystem_routes(app):
    @app.route('/open_path/')
    def open_filesystem_path():
        """Open the requested path in the local file manager."""
        raw_path = request.args.get('path')
        if not raw_path:
            abort(400)

        decoded_path = os.path.abspath(unquote(raw_path))
        if not os.path.exists(decoded_path):
            abort(404)

        allowed_roots = [DB_route_external, DB_route_internal]
        if not _path_is_within_roots(decoded_path, allowed_roots):
            abort(403)

        target_directory = decoded_path if os.path.isdir(decoded_path) else os.path.dirname(decoded_path)
        if not target_directory:
            abort(404)

        try:
            _open_in_file_manager(target_directory)
        except Exception as exc:
            abort(500, description=f"Failed to open path: {exc}")

        return redirect(request.referrer or url_for('index'))


def _register_folder_routes(app):
    @app.route('/both/<path:folder_path>/')
    def view_both(folder_path):
        """
        取得指定資料夾內的所有圖片
        src: internal or external
        """
        source = request.args.get('src', 'external')
        return _render_media_view('view_both.html', folder_path, source)

    @app.route('/grid/<path:folder_path>/')
    def view_grid(folder_path):
        """取得指定資料夾內的所有圖片（Grid 模式）"""
        return _render_media_view('view_grid.html', folder_path)

    @app.route('/slide/<path:folder_path>/')
    def view_slide(folder_path):
        """取得指定資料夾內的所有圖片（Slide 模式）"""
        return _render_media_view('view_slide.html', folder_path)

    @app.route('/collections/')
    @require_feature("db")
    def view_collections():
        """顯示 DB main 目錄，使用 view_both 版型"""
        source = request.args.get('src', 'external')
        try:
            metadata, data = get_all_folders_info(source)
        except AccessDenied:
            abort(403)
        except (FolderNotFound, MediaNotFound, BookmarkNotFound):
            abort(404)
        except ExternalServiceError as exc:
            abort(500, description=str(exc))
        metadata_dict, data_list = _serialize_payload(metadata, data)
        return render_template('view_both.html', metadata=metadata_dict, data=data_list)


def _register_item_db_routes(app):
    @app.route('/update_db')
    @require_feature("db")
    def update_item_db_route():
        """Crawl tagged folders and persist items into the central DB."""
        base_path = request.args.get("base") or DB_route_external
        try:
            result = update_item_database(base_path)
        except FileNotFoundError:
            abort(404, description="指定的資料夾不存在，請確認 DB_route_external。")
        except Exception as exc:
            abort(500, description=f"更新 item DB 失敗: {exc}")

        wants_json = request.args.get("format") == "json" or request.accept_mimetypes.best == "application/json"
        if wants_json:
            return jsonify(result)

        return render_template(
            "update_db_result.html",
            title="Update Item DB",
            result=result,
        )

    @app.route('/update_thumbnails')
    @require_feature("db")
    def update_thumbnails_route():
        """Populate thumbnails for items missing thumbnail_route."""
        base_path = request.args.get("base") or DB_route_external
        force = request.args.get("force", "").lower() in {"1", "true", "yes", "y"}
        try:
            result = update_missing_thumbnails(base_path, force=force)
        except FileNotFoundError:
            abort(404, description="指定的資料夾不存在，請確認 DB_route_external。")
        except Exception as exc:
            abort(500, description=f"更新 thumbnails 失敗: {exc}")

        wants_json = request.args.get("format") == "json" or request.accept_mimetypes.best == "application/json"
        if wants_json:
            return jsonify(result)

        return render_template(
            "update_thumbnails_result.html",
            title="Update Thumbnails",
            result=result,
            forced=force,
        )

    @app.route('/clear_thumbnails')
    @require_feature("db")
    def clear_thumbnails_route():
        """Clear thumbnail_route for all items (or a specific base)."""
        base_path = request.args.get("base")
        try:
            result = clear_thumbnails(base_path)
        except Exception as exc:
            abort(500, description=f"清除 thumbnails 失敗: {exc}")

        wants_json = request.args.get("format") == "json" or request.accept_mimetypes.best == "application/json"
        if wants_json:
            return jsonify(result)

        return render_template(
            "clear_thumbnails_result.html",
            title="Clear Thumbnails",
            result=result,
        )


def _register_item_db_debug_routes(app):
    @app.route('/item_db')
    @require_feature("db")
    def view_item_db():
        """Display indexed items and surface stale filesystem records."""
        try:
            limit = int(request.args.get("limit", 200))
            offset = int(request.args.get("offset", 0))
        except ValueError:
            abort(400, description="limit/offset 需為數字")

        try:
            payload = fetch_items(limit=limit, offset=offset)
        except Exception as exc:
            abort(500, description=f"讀取 item DB 失敗: {exc}")

        selected_tag = request.args.get("tag", "").strip()
        if selected_tag:
            payload["items"] = [
                item for item in payload.get("items", [])
                if selected_tag in (item.get("tags") or [])
            ]

        item_cards = [
            _content_card_from_db_item(item, why="本機資料庫", lane="inbox")
            for item in payload.get("items", [])
        ]
        db_rows = [
            {"item": item, "card": card}
            for item, card in zip(payload.get("items", []), item_cards)
        ]
        available_count = sum(card.get("is_available") for card in item_cards)
        stale_count = len(item_cards) - available_count

        wants_json = request.args.get("format") == "json" or request.accept_mimetypes.best == "application/json"
        if wants_json:
            return jsonify(payload)

        return render_template(
            "item_db_view.html",
            title="Item DB",
            payload=payload,
            items=payload.get("items", []),
            item_cards=item_cards,
            db_rows=db_rows,
            available_count=available_count,
            stale_count=stale_count,
            selected_tag=selected_tag,
        )


def _register_chrome_routes(app):
    @app.get('/api/bookmark-thumbnails/status')
    def bookmark_thumbnail_status():
        raw_ids = request.args.get('ids', '')
        media_ids = [value.strip() for value in raw_ids.split(',') if value.strip()]
        if not media_ids:
            return jsonify({"items": []})
        if len(media_ids) > 200:
            abort(400, description="At most 200 thumbnail IDs can be checked at once")
        return jsonify({"items": get_thumbnail_store().get_statuses(media_ids)})

    @app.post('/api/bookmark-thumbnails/enqueue')
    def enqueue_bookmark_thumbnails():
        payload = request.get_json(silent=True) or {}
        media_ids = payload.get("ids") or []
        if not isinstance(media_ids, list) or len(media_ids) > 200:
            abort(400, description="ids must be a list with at most 200 entries")
        force = bool(payload.get("force"))
        priority = PRIORITY_VISIBLE
        if payload.get("priority") is not None:
            try:
                priority = max(PRIORITY_VISIBLE, min(PRIORITY_MISSING, int(payload["priority"])))
            except (TypeError, ValueError):
                abort(400, description="priority must be an integer")
        store = get_thumbnail_store()
        queued = [
            str(media_id)
            for media_id in media_ids
            if store.enqueue_job(str(media_id), priority=priority, force=force)
        ]
        return jsonify({"queued": queued, "items": store.get_statuses(media_ids)})

    @app.get('/api/bookmark-thumbnails/<media_id>/image')
    def serve_bookmark_thumbnail(media_id):
        if len(media_id) != 40 or any(char not in '0123456789abcdef' for char in media_id.lower()):
            abort(404)
        path = get_thumbnail_store().get_thumbnail_path(media_id)
        if not path:
            abort(404)
        return send_file(path, conditional=True, max_age=3600)

    @app.route('/chrome/')
    @require_feature("chrome")
    def view_chrome_root():
        """預設顯示書籤列 (bookmark_bar)。"""
        focus_mode = request.args.get('mode')
        if focus_mode:
            return redirect(url_for('view_chrome_folder', folder_path='bookmark_bar', mode=focus_mode))
        return redirect(url_for('view_chrome_folder', folder_path='bookmark_bar'))

    @app.route('/chrome/<path:folder_path>/')
    @require_feature("chrome")
    def view_chrome_folder(folder_path):
        """瀏覽 Chrome 書籤資料夾。"""
        focus_mode = request.args.get('mode')
        try:
            metadata, data = get_chrome_bookmarks(folder_path, focus_mode)
        except BookmarkNotFound:
            abort(404)
        except BookmarkError as exc:
            abort(500, description=str(exc))
        except ExternalServiceError as exc:
            abort(500, description=str(exc))
        metadata_dict, data_list = _serialize_payload(metadata, data)
        return render_template('view_both.html', metadata=metadata_dict, data=data_list)

    @app.route('/chrome_youtube/')
    @require_feature("youtube")
    def view_chrome_youtube():
        """專門顯示 YouTube 書籤"""
        try:
            metadata, data = get_chrome_youtube_bookmarks()
        except BookmarkNotFound:
            abort(404)
        except BookmarkError as exc:
            abort(500, description=str(exc))
        except ExternalServiceError as exc:
            abort(500, description=str(exc))
        metadata_dict, data_list = _serialize_payload(metadata, data)
        return render_template('view_both.html', metadata=metadata_dict, data=data_list)


def _register_thumbnail_cli(app):
    @app.cli.command("thumbnails-sync")
    @click.option("--missing", is_flag=True, help="Queue bookmarks without a local thumbnail.")
    @click.option("--force", is_flag=True, help="Refresh thumbnails even when a cache entry exists.")
    @click.option("--domain", help="Only process one hostname or parent domain.")
    @click.option("--limit", type=click.IntRange(min=1), help="Maximum number of bookmarks to process.")
    def thumbnails_sync(missing, force, domain, limit):
        """Register Chrome bookmarks, queue work, and process ready jobs."""
        store = get_thumbnail_store()
        registered = 0
        for bookmark in iter_chrome_bookmark_records():
            store.register_bookmark(
                bookmark["url"],
                bookmark["title"],
                {"folder_path": bookmark.get("folder_path") or ""},
                enqueue_missing=False,
            )
            registered += 1
        queue_result = store.sync_missing(force=force, domain=domain, limit=limit)
        processed = ThumbnailWorker(store=store, domain_filter=domain).run_until_idle(max_jobs=limit)
        click.echo(json.dumps({
            "registered": registered,
            "missing_only": bool(missing or not force),
            "processed": processed,
            **queue_result,
        }, ensure_ascii=False))


def _register_eagle_routes(app):
    @app.route('/EAGLE_folders/')
    @require_feature("eagle")
    def list_all_eagle_folder():
        """列出所有 Eagle 資料夾，並符合 EAGLE API 樣式"""
        try:
            metadata, data = get_eagle_folders()
        except ExternalServiceError as exc:
            abort(500, description=str(exc))
        metadata_dict, data_list = _serialize_payload(metadata, data)
        return render_template('view_both.html', metadata=metadata_dict, data=data_list)

    @app.route('/EAGLE_tags/')
    @require_feature("eagle")
    def list_eagle_tags():
        """列出 Eagle 中的所有標籤並提供連結"""
        try:
            metadata, tags = get_eagle_tags()
        except ExternalServiceError as exc:
            abort(500, description=str(exc))
        metadata_dict = _to_dict(metadata)
        return render_template("eagle_tags.html", metadata=metadata_dict, tags=tags)

    @app.route('/EAGLE_smart_folders/')
    @require_feature("eagle")
    def list_eagle_smart_folders():
        """List Eagle v2 smart folders as dynamic collections."""
        try:
            metadata, data = get_eagle_smart_folders()
        except ExternalServiceError as exc:
            abort(500, description=str(exc))
        metadata_dict, data_list = _serialize_payload(metadata, data)
        return render_template('view_both.html', metadata=metadata_dict, data=data_list)

    @app.route('/EAGLE_smart_folder/<smart_folder_id>/')
    @require_feature("eagle")
    def view_eagle_smart_folder(smart_folder_id):
        """Browse items matched by an Eagle v2 smart folder."""
        try:
            metadata, data = get_eagle_images_by_smart_folder_id(smart_folder_id)
        except ExternalServiceError as exc:
            abort(500, description=str(exc))
        metadata_dict, data_list = _serialize_payload(metadata, data)
        _attach_detail_urls(data_list, _normalize_current_url())
        return render_template('view_both.html', metadata=metadata_dict, data=data_list)

    @app.route('/EAGLE_folder/<eagle_folder_id>/')
    @require_feature("eagle")
    def view_eagle_folder(eagle_folder_id):
        """顯示指定 Eagle 資料夾 ID 下的所有圖片"""
        try:
            metadata, data = get_eagle_images_by_folderid(eagle_folder_id)
            subfolders = get_subfolders_info(eagle_folder_id)
            data = subfolders + data
        except MediaNotFound:
            abort(404)
        except ExternalServiceError as exc:
            abort(500, description=str(exc))

        metadata_dict, data_list = _serialize_payload(metadata, data)
        current_url = _normalize_current_url()
        _attach_detail_urls(data_list, current_url)

        return render_template('view_both.html', metadata=metadata_dict, data=data_list)

    @app.route('/EAGLE_tag/<target_tag>/')
    @require_feature("eagle")
    def view_images_by_tag(target_tag):
        """
        顯示所有帶有指定標籤的圖片，並符合 EAGLE API 格式。

        Args:
            target_tag (str): 要查詢的標籤。

        Returns:
            渲染的 HTML 頁面，顯示所有具有該標籤的圖片。
        """
        try:
            metadata, data = get_eagle_images_by_tag(target_tag)
        except ExternalServiceError as exc:
            abort(500, description=str(exc))
        metadata_dict, data_list = _serialize_payload(metadata, data)

        current_url = _normalize_current_url()
        _attach_detail_urls(data_list, current_url)

        return render_template('view_both.html', metadata=metadata_dict, data=data_list)

    @app.route('/search')
    @require_feature("eagle")
    def search_eagle():
        """使用 Eagle API 搜尋並顯示結果。"""
        keyword = request.args.get('query', '').strip()
        if not keyword:
            return redirect(request.referrer or url_for('index'))

        try:
            metadata, data = search_eagle_items(keyword)
        except ExternalServiceError as exc:
            abort(500, description=str(exc))
        metadata_dict, data_list = _serialize_payload(metadata, data)

        current_url = _normalize_current_url()
        _attach_detail_urls(data_list, current_url)

        return render_template('view_both.html', metadata=metadata_dict, data=data_list)

    @app.route('/EAGLE_stream/')
    @require_feature("eagle")
    def eagle_stream():
        """顯示手動分批載入的 Eagle 串流頁面。"""
        return render_template('eagle_stream.html')

    @app.route('/api/EAGLE_stream/')
    @require_feature("eagle")
    def eagle_stream_data():
        """提供 Eagle 串流頁面使用的資料"""
        try:
            offset = int(request.args.get('offset', 0))
            limit = int(request.args.get('limit', 30))
        except ValueError:
            abort(400, description="Invalid offset or limit")

        limit = max(1, min(limit, 60))
        offset = max(0, offset)

        try:
            data = get_eagle_stream_items(offset=offset, limit=limit)
        except ExternalServiceError as exc:
            abort(500, description=str(exc))
        data = [_to_dict(item) for item in data]
        items = []
        for item in data:
            item_id = item.get("id")
            if not item_id:
                continue
            if item.get("media_type") == "video":
                detail_url = url_for("view_eagle_video", item_id=item_id)
            else:
                detail_url = url_for("view_eagle_image", item_id=item_id)

            items.append({
                "id": item_id,
                "name": item.get("name"),
                "thumbnail_route": item.get("thumbnail_route"),
                "detail_url": detail_url,
                "media_type": item.get("media_type"),
                "ext": item.get("ext")
            })

        return jsonify({
            "items": items,
            "nextOffset": offset + len(items)
        })

    @app.route('/EAGLE_video/<item_id>/')
    @require_feature("eagle")
    def view_eagle_video(item_id):
        """顯示 Eagle 影片的詳細資訊與播放器頁面"""
        try:
            metadata, video = get_eagle_video_details(item_id)
        except MediaNotFound:
            abort(404)
        except ExternalServiceError as exc:
            abort(500, description=str(exc))
        metadata_dict = _to_dict(metadata)
        video_dict = _serialize_detail(video)
        return_to = request.args.get("return_to")
        if return_to:
            video_dict["parent_url"] = return_to
        else:
            video_dict["parent_url"] = request.referrer or url_for("index")
        related_items = _decorate_related_items(metadata_dict.get("similar"), metadata_dict)
        recommended_actions = _build_detail_actions("video", metadata_dict)
        return render_template(
            'video_player.html',
            metadata=metadata_dict,
            video=video_dict,
            related_items=related_items,
            recommended_actions=recommended_actions,
            inspiration_collections=_get_inspiration_collections(),
            inspiration_item={
                "source_kind": "eagle",
                "source_id": item_id,
                "title": metadata_dict.get("name") or video_dict.get("name"),
                "url": request.path,
                "thumbnail": video_dict.get("thumbnail_route"),
            },
        )

    @app.route('/EAGLE_image/<item_id>/')
    @require_feature("eagle")
    def view_eagle_image(item_id):
        """顯示 Eagle 圖片的詳細資訊與展示頁面"""
        try:
            metadata, image = get_eagle_image_details(item_id)
        except MediaNotFound:
            abort(404)
        except ExternalServiceError as exc:
            abort(500, description=str(exc))
        metadata_dict = _to_dict(metadata)
        image_dict = _serialize_detail(image)
        return_to = request.args.get("return_to")
        if return_to:
            image_dict["parent_url"] = return_to
        else:
            image_dict["parent_url"] = request.referrer or url_for("index")
        related_items = _decorate_related_items(metadata_dict.get("similar"), metadata_dict)
        recommended_actions = _build_detail_actions("image", metadata_dict)
        return render_template(
            'image_viewer.html',
            metadata=metadata_dict,
            image=image_dict,
            related_items=related_items,
            recommended_actions=recommended_actions,
            inspiration_collections=_get_inspiration_collections(),
            inspiration_item={
                "source_kind": "eagle",
                "source_id": item_id,
                "title": metadata_dict.get("name") or image_dict.get("name"),
                "url": request.path,
                "thumbnail": image_dict.get("thumbnail_route") or image_dict.get("source_url"),
            },
        )


def _register_media_routes(app):
    @app.route('/serve_image/<path:image_path>')
    def serve_image_by_full_path(image_path):
        """提供靜態圖片服務"""
        # Flask <path> 會吞掉開頭的「/」，補回來避免相對路徑被解讀成專案內路徑。
        decoded = unquote(image_path)
        if IS_WINDOWS:
            # Windows paths may include drive letters (e.g. C:/...), so don't force a leading slash
            decoded_path = os.path.abspath(decoded)
        else:
            if not decoded.startswith("/"):
                decoded = "/" + decoded
            decoded_path = os.path.abspath(decoded)

        if not os.path.isfile(decoded_path):
            abort(404)

        if IS_MACOS:
            directory, filename = os.path.split(decoded_path)
            return send_from_directory(directory, filename)

        return send_file(decoded_path)

    @app.route('/video/<path:video_path>')
    def view_video(video_path):
        """顯示影片播放頁面"""
        source = request.args.get('src', 'external')
        try:
            metadata, video = get_video_details(video_path, source)
        except AccessDenied:
            abort(403)
        except (FolderNotFound, MediaNotFound):
            abort(404)
        metadata_dict = _to_dict(metadata)
        video_dict = _serialize_detail(video)
        return render_template(
            'video_player.html',
            metadata=metadata_dict,
            video=video_dict,
            related_items=_decorate_related_items(metadata_dict.get("similar"), metadata_dict),
            recommended_actions=_build_detail_actions("video", metadata_dict),
            inspiration_collections=_get_inspiration_collections(),
            inspiration_item={
                "source_kind": "filesystem",
                "source_id": f"{source}:{video_dict.get('relative_path') or video_path}",
                "title": metadata_dict.get("name") or video_dict.get("name"),
                "url": request.path,
                "thumbnail": video_dict.get("thumbnail_route"),
            },
        )

    @app.route('/image/<path:image_path>')
    def view_image(image_path):
        """顯示圖片展示頁面"""
        source = request.args.get('src', 'external')
        try:
            metadata, image = get_image_details(image_path, source)
        except AccessDenied:
            abort(403)
        except (FolderNotFound, MediaNotFound):
            abort(404)
        metadata_dict = _to_dict(metadata)
        image_dict = _serialize_detail(image)
        return render_template(
            'image_viewer.html',
            metadata=metadata_dict,
            image=image_dict,
            related_items=_decorate_related_items(metadata_dict.get("similar"), metadata_dict),
            recommended_actions=_build_detail_actions("image", metadata_dict),
            inspiration_collections=_get_inspiration_collections(),
            inspiration_item={
                "source_kind": "filesystem",
                "source_id": f"{source}:{image_dict.get('relative_path') or image_path}",
                "title": metadata_dict.get("name") or image_dict.get("name"),
                "url": request.path,
                "thumbnail": image_dict.get("thumbnail_route") or image_dict.get("source_url"),
            },
        )
