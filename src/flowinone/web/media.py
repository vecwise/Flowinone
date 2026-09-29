"""Local media serving and detail-view routes."""

from __future__ import annotations

import os
import platform
from pathlib import Path
from urllib.parse import unquote

from flask import Blueprint, abort, current_app, render_template, request, send_file

from src.flowinone import config
from src.file_handler.paths import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
from src.flowinone.paths import data_dir

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


def _allowed_media_roots() -> list[Path]:
    roots = [
        Path(value).expanduser()
        for value in (config.DB_route_external, config.DB_route_internal)
        if value
    ]
    roots.append(data_dir())
    configured = current_app.config.get("FLOWINONE_MEDIA_ROOTS", ())
    if isinstance(configured, str):
        configured = configured.split(os.pathsep)
    roots.extend(Path(value).expanduser() for value in configured if value)
    try:
        from src.file_handler.eagle_integration import _get_eagle_library_path

        roots.append(Path(_get_eagle_library_path()).expanduser())
    except Exception:
        # Eagle is optional; its root is only relevant when the local API is live.
        pass
    return [root.resolve() for root in roots]


def _serve_media_by_full_path(media_path: str, extensions: set[str]):
    decoded = unquote(media_path).replace("\\", os.sep)
    if platform.system() != "Windows" and not decoded.startswith("/"):
        decoded = "/" + decoded
    candidate = Path(decoded).expanduser().resolve()
    if candidate.suffix.lower().lstrip(".") not in extensions:
        abort(404)
    if not candidate.is_file():
        abort(404)
    if not any(candidate.is_relative_to(root) for root in _allowed_media_roots()):
        abort(403)
    response = send_file(candidate, conditional=True, max_age=3600)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@bp.get("/serve_image/<path:image_path>")
def serve_image_by_full_path(image_path: str):
    return _serve_media_by_full_path(image_path, IMAGE_EXTENSIONS)


@bp.get("/serve_video/<path:video_path>")
def serve_video_by_full_path(video_path: str):
    return _serve_media_by_full_path(video_path, VIDEO_EXTENSIONS)


def _render_local_detail(path: str, media_kind: str):
    source = request.args.get("src", "external")
    loader = get_video_details if media_kind == "video" else get_image_details
    try:
        metadata, detail = loader(path, source)
    except AccessDenied:
        abort(403)
    except MediaNotFound:
        if media_kind != "video":
            abort(404)
        # Older Catalog origins recorded every Local item as external. Keep
        # those links usable when the file actually lives in the other root.
        alternate = "internal" if source == "external" else "external"
        try:
            metadata, detail = loader(path, alternate)
        except AccessDenied:
            abort(403)
        except (FolderNotFound, MediaNotFound):
            abort(404)
    except FolderNotFound:
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
