"""Local filesystem, index, and source-overview Flask routes."""

from __future__ import annotations

import json
import os
import platform
import subprocess
from pathlib import Path
from urllib.parse import unquote

import click
from flask import Blueprint, abort, current_app, jsonify, redirect, render_template, request, url_for

from src.flowinone.config import DB_route_external, DB_route_internal
from src.file_handler import (
    AccessDenied,
    BookmarkNotFound,
    ExternalServiceError,
    FolderNotFound,
    MediaNotFound,
    clear_thumbnails,
    fetch_items,
    get_all_folders_info,
    get_folder_images,
    update_item_database,
    update_missing_thumbnails,
)
from src.file_handler.sidecars import SidecarService
from src.flowinone.catalog.browse import CatalogService
from src.flowinone.catalog.query import CatalogQuery
from src.flowinone.web.database import request_database

from .common import (
    content_card_from_db_item,
    get_feature_flags,
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


@bp.get("/")
def index():
    return redirect(url_for("catalog.navigator_page", scope="gallery"))


@bp.get("/library/")
def content_library():
    facets = CatalogService(request_database()).facets(CatalogQuery.create(scope="all"))
    flags = get_feature_flags()
    sources = [
        {
            "key": key,
            "label": label,
            "count": facets["sources"].get(key, 0),
            "status": facets["sync"].get(key, {}).get("status", "idle"),
            "catalog_url": url_for("catalog.navigator_page", scope="all", source=key),
            "detail_url": url_for(endpoint) if flag is None or flags.get(flag) else None,
        }
        for key, label, flag, endpoint in (
            ("local", "本機", "db", "local.view_item_db"),
            ("eagle", "Eagle", "eagle", "eagle.eagle_stream"),
            ("bookmarks", "Chrome 書籤", "chrome", "chrome.view_chrome_root"),
            ("resources", "Resources", None, "resource_library.resource_index"),
        )
    ]
    return render_template("source_overview.html", title="來源總覽 · Flowinone", sources=sources)


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
