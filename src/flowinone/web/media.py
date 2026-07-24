"""Local media serving and detail-view routes."""

from __future__ import annotations

import os
import platform
from urllib.parse import unquote

from flask import Blueprint, abort, render_template, request, send_file, send_from_directory

from src.file_handler import (
    AccessDenied,
    FolderNotFound,
    MediaNotFound,
    get_image_details,
    get_video_details,
)

from .common import (
    build_detail_actions,
    catalog_related_for_origin,
    decorate_related_items,
    merge_related,
    serialize_detail,
    to_dict,
)


bp = Blueprint("media", __name__)
IS_MACOS = platform.system() == "Darwin"
IS_WINDOWS = platform.system() == "Windows"


@bp.get("/serve_image/<path:image_path>")
def serve_image_by_full_path(image_path: str):
    decoded = unquote(image_path)
    if IS_WINDOWS:
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


def _render_local_detail(path: str, media_kind: str):
    source = request.args.get("src", "external")
    loader = get_video_details if media_kind == "video" else get_image_details
    try:
        metadata, detail = loader(path, source)
    except AccessDenied:
        abort(403)
    except (FolderNotFound, MediaNotFound):
        abort(404)
    metadata_dict = to_dict(metadata)
    detail_dict = serialize_detail(detail)
    return render_template(
        "video_player.html" if media_kind == "video" else "image_viewer.html",
        metadata=metadata_dict,
        **{media_kind: detail_dict},
        related_items=merge_related(
            decorate_related_items(metadata_dict.get("similar"), metadata_dict),
            catalog_related_for_origin(
                "local", detail_dict.get("relative_path") or path
            ),
        ),
        recommended_actions=build_detail_actions(media_kind, metadata_dict),
    )


@bp.get("/video/<path:video_path>")
def view_video(video_path: str):
    return _render_local_detail(video_path, "video")


@bp.get("/image/<path:image_path>")
def view_image(image_path: str):
    return _render_local_detail(image_path, "image")
