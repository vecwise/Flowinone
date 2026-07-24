"""Local filesystem, index, and source-overview Flask routes."""

from __future__ import annotations

import json
import os
import platform
import random
import subprocess
from collections import Counter
from pathlib import Path
from urllib.parse import unquote

import click
from flask import Blueprint, abort, current_app, jsonify, redirect, render_template, request, url_for

from config import DB_route_external, DB_route_internal
from src.file_handler import (
    AccessDenied,
    BookmarkNotFound,
    ExternalServiceError,
    FolderNotFound,
    MediaNotFound,
    clear_thumbnails,
    fetch_items,
    get_all_folders_info,
    get_eagle_folders,
    get_eagle_stream_items,
    get_eagle_tags,
    get_folder_images,
    update_item_database,
    update_missing_thumbnails,
)
from src.file_handler.sidecars import SidecarService

from .common import (
    build_shelf,
    clone_card,
    content_card_from_db_item,
    content_card_from_media,
    content_card_from_resource,
    get_feature_flags,
    normalize_current_url,
    path_is_within_roots,
    require_feature,
    serialize_payload,
)


bp = Blueprint("local", __name__)
SYSTEM_NAME = platform.system()
IS_MACOS = SYSTEM_NAME == "Darwin"
IS_WINDOWS = SYSTEM_NAME == "Windows"


def _open_in_file_manager(target_path: str) -> None:
    if IS_MACOS:
        subprocess.Popen(["open", target_path])
    elif IS_WINDOWS:
        os.startfile(target_path)  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", target_path])


def _render_media_view(template_name: str, folder_path: str, source: str | None = None):
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
    metadata_dict, data_list = serialize_payload(metadata, data)
    return render_template(template_name, metadata=metadata_dict, data=data_list)


def _build_index_context(flags: dict[str, bool]) -> dict:
    context = {
        "active_mode": "sources",
        "mode_heading": "來源總覽",
        "mode_description": "查看目前可用的來源、媒體與 metadata。",
        "modes": [],
        "hero_item": None,
        "home_shelves": [],
        "tag_cloud": [],
        "show_tag_cloud": True,
        "source_summary": [],
        "action_queue": [],
        "stale_db_count": 0,
        "fallback_heading": "尚未有可探索內容",
        "fallback_message": "連接 Eagle、本機資料庫或 Chrome 書籤後，這裡會顯示可瀏覽的來源。",
        "has_content": False,
    }
    all_cards: list[dict] = []
    eagle_cards: list[dict] = []
    db_cards: list[dict] = []
    resource_cards: list[dict] = []
    image_cards: list[dict] = []
    video_cards: list[dict] = []
    folder_cards: list[dict] = []

    if flags.get("eagle"):
        try:
            current_url = normalize_current_url()
            stream_payload = get_eagle_stream_items(offset=0, limit=48)
            eagle_cards = [
                content_card_from_media(
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
                content_card_from_media(
                    item,
                    source_label="Eagle",
                    why="Eagle 集合入口",
                    lane="source",
                )
                for item in folder_data_raw
            )
            _, tag_data = get_eagle_tags()
            context["tag_cloud"].extend(
                {
                    "name": tag.get("name", "").strip(),
                    "count": tag.get("count"),
                    "url": url_for(
                        "eagle.view_images_by_tag",
                        target_tag=tag.get("name", "").strip(),
                    ),
                    "source": "Eagle",
                }
                for tag in tag_data[:24]
                if tag.get("name", "").strip()
            )
            context["source_summary"].append(
                {
                    "label": "Eagle",
                    "value": len(eagle_cards),
                    "hint": "首頁取樣",
                    "tone": "ready",
                    "url": url_for("eagle.eagle_stream"),
                }
            )
        except Exception:
            current_app.logger.exception("Failed to build Eagle homepage context")
            context["source_summary"].append(
                {
                    "label": "Eagle",
                    "value": "離線",
                    "hint": "無法讀取來源",
                    "tone": "warning",
                    "url": None,
                }
            )

    if flags.get("db"):
        try:
            payload = fetch_items(limit=1000, offset=0)
            raw_db_cards = [
                content_card_from_db_item(item, why="本機資料庫", lane="local")
                for item in payload.get("items", [])
            ]
            db_cards = [card for card in raw_db_cards if card.get("is_available")]
            stale_count = sum(not card.get("is_available") for card in raw_db_cards)
            context["stale_db_count"] = stale_count
            all_cards.extend(db_cards)
            image_cards.extend(card for card in db_cards if card["media_type"] == "image")
            video_cards.extend(card for card in db_cards if card["media_type"] == "video")
            folder_cards.extend(card for card in db_cards if card["media_type"] == "folder")
            tag_counts: Counter = Counter()
            for card in db_cards:
                tag_counts.update(card.get("tags") or [])
            context["tag_cloud"].extend(
                {
                    "name": tag_name,
                    "count": count,
                    "url": url_for("local.view_item_db", tag=tag_name),
                    "source": "Local DB",
                }
                for tag_name, count in tag_counts.most_common(16)
            )
            total = payload.get("total", len(raw_db_cards))
            context["source_summary"].append(
                {
                    "label": "Local DB",
                    "value": f"{len(db_cards)} / {total}",
                    "hint": "可用 / 已索引" if not stale_count else f"{stale_count} 筆需同步",
                    "tone": "ready" if not stale_count else "warning",
                    "url": url_for("local.view_item_db"),
                }
            )
        except Exception:
            current_app.logger.exception("Failed to build DB homepage context")

    if flags.get("chrome"):
        context["source_summary"].append(
            {
                "label": "Chrome",
                "value": "可用",
                "hint": "書籤來源",
                "tone": "ready",
                "url": url_for("chrome.view_chrome_root"),
            }
        )
    try:
        from src.flowinone.resource_library.database import get_resource_database
        from src.flowinone.resource_library.service import ResourceService

        configured_resource_db = current_app.config.get("FLOWINONE_RESOURCE_DB_PATH")
        resource_service = ResourceService(
            get_resource_database(
                Path(configured_resource_db),
                migrate=bool(
                    current_app.config.get(
                        "FLOWINONE_AUTO_MIGRATE", current_app.testing
                    )
                ),
            )
            if configured_resource_db
            else None
        )
        resource_page = resource_service.repository.list(page=1, per_page=48)
        resource_cards = [
            content_card_from_resource(item, lane="resource")
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
        context["source_summary"].append(
            {
                "label": "Resources",
                "value": resource_page.total,
                "hint": "已匯入資源",
                "tone": "ready",
                "url": url_for("resource_library.resource_index"),
            }
        )
    except Exception:
        current_app.logger.exception("Failed to build Resource Library homepage context")

    shelves = []
    if all_cards:
        shelves.append(
            build_shelf(
                "最近來源項目",
                "最近可用的媒體、書籤與網頁資源",
                [clone_card(card, "最近更新", "recent") for card in all_cards[:10]],
                action_label="開啟 Navigator",
                action_url=url_for("catalog.navigator_page", scope="gallery"),
            )
        )
    if image_cards:
        sample = random.sample(image_cards, min(10, len(image_cards)))
        shelves.append(
            build_shelf(
                "隨機視覺瀏覽",
                "小批次隨機探索；由你決定下一個",
                [clone_card(card, "隨機探索", "random") for card in sample],
            )
        )
    if video_cards:
        shelves.append(
            build_shelf(
                "影片",
                "不自動播放；開啟你要看的來源",
                [clone_card(card, "影片", "video") for card in video_cards[:8]],
            )
        )
    if folder_cards:
        shelves.append(
            build_shelf(
                "來源資料夾",
                "從來源自己的資料夾與集合頁進入",
                [clone_card(card, "來源入口", "source") for card in folder_cards[:10]],
                layout="compact",
                action_label="Eagle Folders" if flags.get("eagle") else None,
                action_url=url_for("eagle.list_all_eagle_folder")
                if flags.get("eagle")
                else None,
            )
        )
    if all_cards:
        context["hero_item"] = clone_card(all_cards[0], "開啟來源", "featured")

    actions = [
        {
            "label": "瀏覽 Web Resources",
            "description": "查看已擷取的網頁 metadata、摘要與全文。",
            "url": url_for("resource_library.resource_index"),
            "enabled": True,
            "tone": "neutral",
        }
    ]
    if context["stale_db_count"]:
        actions.append(
            {
                "label": "同步本機索引",
                "description": f"{context['stale_db_count']} 筆來源已移動或刪除，先清掉失效入口。",
                "url": url_for("local.update_item_db_route"),
                "method": "post",
                "enabled": True,
                "tone": "warning",
            }
        )
    elif flags.get("db"):
        actions.append(
            {
                "label": "瀏覽本機索引",
                "description": "查看本機資料庫已索引的媒體與檔案。",
                "url": url_for("local.view_item_db"),
                "enabled": True,
                "tone": "neutral",
            }
        )
    if flags.get("eagle"):
        actions.append(
            {
                "label": "沿 tag 探索",
                "description": "從主題線索開出下一條內容路徑。",
                "url": url_for("eagle.list_eagle_tags"),
                "enabled": True,
                "tone": "neutral",
            }
        )
    if flags.get("chrome"):
        actions.append(
            {
                "label": "瀏覽書籤",
                "description": "直接瀏覽 Chrome 書籤來源。",
                "url": url_for("chrome.view_chrome_root"),
                "enabled": True,
                "tone": "neutral",
            }
        )
    context["home_shelves"] = shelves
    context["action_queue"] = actions[:3]
    context["has_content"] = bool(all_cards or folder_cards or context["tag_cloud"])
    return context


@bp.get("/")
def index():
    return redirect(url_for("catalog.navigator_page", scope="gallery"))


@bp.get("/library/")
def content_library():
    return render_template("index.html", **_build_index_context(get_feature_flags()))


@bp.post("/open_path/")
def open_filesystem_path():
    raw_path = request.form.get("path")
    if not raw_path:
        abort(400)
    decoded_path = os.path.abspath(unquote(raw_path))
    if not os.path.exists(decoded_path):
        abort(404)
    if not path_is_within_roots(decoded_path, [DB_route_external, DB_route_internal]):
        abort(403)
    target_directory = decoded_path if os.path.isdir(decoded_path) else os.path.dirname(decoded_path)
    if not target_directory:
        abort(404)
    try:
        _open_in_file_manager(target_directory)
    except Exception as exc:
        abort(500, description=f"Failed to open path: {exc}")
    return redirect(url_for("local.content_library"))


@bp.get("/both/<path:folder_path>/")
def view_both(folder_path: str):
    return _render_media_view(
        "view_both.html", folder_path, request.args.get("src", "external")
    )


@bp.get("/grid/<path:folder_path>/")
def view_grid(folder_path: str):
    return _render_media_view("view_grid.html", folder_path)


@bp.get("/slide/<path:folder_path>/")
def view_slide(folder_path: str):
    return _render_media_view("view_slide.html", folder_path)


@bp.get("/folders/")
@require_feature("db")
def view_source_folders():
    try:
        metadata, data = get_all_folders_info(request.args.get("src", "external"))
    except AccessDenied:
        abort(403)
    except (FolderNotFound, MediaNotFound, BookmarkNotFound):
        abort(404)
    except ExternalServiceError as exc:
        abort(500, description=str(exc))
    metadata_dict, data_list = serialize_payload(metadata, data)
    return render_template("view_both.html", metadata=metadata_dict, data=data_list)


@bp.post("/update_db")
@require_feature("db")
def update_item_db_route():
    try:
        result = update_item_database(request.form.get("base") or DB_route_external)
    except FileNotFoundError:
        abort(404, description="指定的資料夾不存在，請確認 DB_route_external。")
    except Exception as exc:
        abort(500, description=f"更新 item DB 失敗: {exc}")
    if _wants_json():
        return jsonify(result)
    return render_template("update_db_result.html", title="Update Item DB", result=result)


@bp.post("/update_thumbnails")
@require_feature("db")
def update_thumbnails_route():
    force = request.form.get("force", "").lower() in {"1", "true", "yes", "y"}
    try:
        result = update_missing_thumbnails(
            request.form.get("base") or DB_route_external, force=force
        )
    except FileNotFoundError:
        abort(404, description="指定的資料夾不存在，請確認 DB_route_external。")
    except Exception as exc:
        abort(500, description=f"更新 thumbnails 失敗: {exc}")
    if _wants_json():
        return jsonify(result)
    return render_template(
        "update_thumbnails_result.html",
        title="Update Thumbnails",
        result=result,
        forced=force,
    )


@bp.post("/clear_thumbnails")
@require_feature("db")
def clear_thumbnails_route():
    try:
        result = clear_thumbnails(request.form.get("base"))
    except Exception as exc:
        abort(500, description=f"清除 thumbnails 失敗: {exc}")
    if _wants_json():
        return jsonify(result)
    return render_template(
        "clear_thumbnails_result.html", title="Clear Thumbnails", result=result
    )


@bp.get("/item_db")
@require_feature("db")
def view_item_db():
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
            item
            for item in payload.get("items", [])
            if selected_tag in (item.get("tags") or [])
        ]
    item_cards = [
        content_card_from_db_item(item, why="本機資料庫", lane="local")
        for item in payload.get("items", [])
    ]
    if _wants_json():
        return jsonify(payload)
    db_rows = [
        {"item": item, "card": card}
        for item, card in zip(payload.get("items", []), item_cards)
    ]
    available_count = sum(card.get("is_available") for card in item_cards)
    return render_template(
        "item_db_view.html",
        title="Item DB",
        payload=payload,
        items=payload.get("items", []),
        item_cards=item_cards,
        db_rows=db_rows,
        available_count=available_count,
        stale_count=len(item_cards) - available_count,
        selected_tag=selected_tag,
    )


def _wants_json() -> bool:
    return (
        request.args.get("format") == "json"
        or request.accept_mimetypes.best == "application/json"
    )


def register_commands(app) -> None:
    @app.cli.command("sidecars-audit")
    @click.argument("root", type=click.Path(path_type=Path, exists=True, file_okay=False))
    def sidecars_audit(root: Path):
        click.echo(json.dumps(SidecarService().audit(root), ensure_ascii=False, indent=2))

    @app.cli.command("sidecars-export")
    @click.argument("root", type=click.Path(path_type=Path, exists=True, file_okay=False))
    @click.option("--apply", is_flag=True, help="Write manifests; default is dry-run.")
    @click.option("--overwrite", is_flag=True, help="Replace a differing existing manifest.")
    def sidecars_export(root: Path, apply: bool, overwrite: bool):
        click.echo(
            json.dumps(
                SidecarService().export(root, dry_run=not apply, overwrite=overwrite),
                ensure_ascii=False,
                indent=2,
            )
        )

    @app.cli.command("sidecars-import")
    @click.argument("root", type=click.Path(path_type=Path, exists=True, file_okay=False))
    @click.option("--apply", is_flag=True, help="Update the local index; default is dry-run.")
    def sidecars_import(root: Path, apply: bool):
        click.echo(
            json.dumps(
                SidecarService().import_(root, dry_run=not apply),
                ensure_ascii=False,
                indent=2,
            )
        )
