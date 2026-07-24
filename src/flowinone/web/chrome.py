"""Chrome bookmark pages and thumbnail HTTP adapter."""

from __future__ import annotations

import json

import click
from flask import Blueprint, abort, redirect, render_template, request, send_file, url_for

from src.file_handler import (
    BookmarkError,
    BookmarkNotFound,
    ExternalServiceError,
    get_chrome_bookmarks,
    get_chrome_youtube_bookmarks,
)
from src.file_handler.chrome_bookmarks import iter_chrome_bookmark_records
from src.file_handler.thumbnails.store import (
    PRIORITY_MISSING,
    PRIORITY_VISIBLE,
    get_thumbnail_store,
)
from src.file_handler.thumbnails.worker import ThumbnailWorker

from .common import require_feature, serialize_payload
from .api import parse_json, parse_query, validated_json
from .schemas import (
    BookmarkThumbnailStatusQuery,
    BookmarkThumbnailEnqueueRequest,
    ThumbnailEnqueueOutput,
    ThumbnailStatusesOutput,
)


bp = Blueprint("chrome", __name__)


@bp.get("/api/bookmark-thumbnails/status")
def bookmark_thumbnail_status():
    query = parse_query(BookmarkThumbnailStatusQuery)
    media_ids = [
        value.strip() for value in query.ids.split(",") if value.strip()
    ]
    if not media_ids:
        return validated_json({"items": []}, ThumbnailStatusesOutput)
    if len(media_ids) > 200:
        abort(400, description="At most 200 thumbnail IDs can be checked at once")
    return validated_json(
        {"items": get_thumbnail_store().get_statuses(media_ids)},
        ThumbnailStatusesOutput,
    )


@bp.post("/api/bookmark-thumbnails/enqueue")
def enqueue_bookmark_thumbnails():
    payload = parse_json(BookmarkThumbnailEnqueueRequest)
    media_ids = payload.ids
    force = payload.force
    priority = PRIORITY_VISIBLE
    if payload.priority is not None:
        priority = max(PRIORITY_VISIBLE, min(PRIORITY_MISSING, payload.priority))
    store = get_thumbnail_store()
    queued = [
        str(media_id)
        for media_id in media_ids
        if store.enqueue_job(str(media_id), priority=priority, force=force)
    ]
    return validated_json(
        {"queued": queued, "items": store.get_statuses(media_ids)},
        ThumbnailEnqueueOutput,
    )


@bp.get("/api/bookmark-thumbnails/<media_id>/image")
def serve_bookmark_thumbnail(media_id: str):
    if len(media_id) != 40 or any(
        char not in "0123456789abcdef" for char in media_id.lower()
    ):
        abort(404)
    path = get_thumbnail_store().get_thumbnail_path(media_id)
    if not path:
        abort(404)
    return send_file(path, conditional=True, max_age=3600)


@bp.get("/chrome/")
@require_feature("chrome")
def view_chrome_root():
    focus_mode = request.args.get("mode")
    if focus_mode:
        return redirect(
            url_for(
                "chrome.view_chrome_folder",
                folder_path="bookmark_bar",
                mode=focus_mode,
            )
        )
    return redirect(
        url_for("chrome.view_chrome_folder", folder_path="bookmark_bar")
    )


@bp.get("/chrome/<path:folder_path>/")
@require_feature("chrome")
def view_chrome_folder(folder_path: str):
    try:
        metadata, data = get_chrome_bookmarks(folder_path, request.args.get("mode"))
    except BookmarkNotFound:
        abort(404)
    except (BookmarkError, ExternalServiceError) as exc:
        abort(500, description=str(exc))
    metadata_dict, data_list = serialize_payload(metadata, data)
    return render_template("view_both.html", metadata=metadata_dict, data=data_list)


@bp.get("/chrome_youtube/")
@require_feature("youtube")
def view_chrome_youtube():
    try:
        metadata, data = get_chrome_youtube_bookmarks()
    except BookmarkNotFound:
        abort(404)
    except (BookmarkError, ExternalServiceError) as exc:
        abort(500, description=str(exc))
    metadata_dict, data_list = serialize_payload(metadata, data)
    return render_template("view_both.html", metadata=metadata_dict, data=data_list)


def register_commands(app) -> None:
    @app.cli.command("thumbnails-sync")
    @click.option("--missing", is_flag=True, help="Queue bookmarks without a local thumbnail.")
    @click.option("--force", is_flag=True, help="Refresh thumbnails even when a cache entry exists.")
    @click.option("--domain", help="Only process one hostname or parent domain.")
    @click.option("--limit", type=click.IntRange(min=1), help="Maximum bookmarks to process.")
    def thumbnails_sync(missing, force, domain, limit):
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
        processed = ThumbnailWorker(
            store=store, domain_filter=domain
        ).run_until_idle(max_jobs=limit)
        click.echo(
            json.dumps(
                {
                    "registered": registered,
                    "missing_only": bool(missing or not force),
                    "processed": processed,
                    **queue_result,
                },
                ensure_ascii=False,
            )
        )
