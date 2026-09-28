"""Shared Resource Blueprint, request services, and response decoration."""

from __future__ import annotations

from flask import Blueprint, current_app, redirect, request, url_for

from src.file_handler.thumbnails.store import get_thumbnail_store

from src.flowinone.web.database import request_database
from .service import ResourceService

bp = Blueprint("resource_library", __name__)


_database = request_database


def _service() -> ResourceService:
    return ResourceService(
        _database(),
        link_thumbnail_cache=bool(
            current_app.config.get("FLOWINONE_RESOURCE_LINK_THUMBNAILS", True)
        ),
    )


def _list_params(payload=None) -> dict:
    values = payload or request.args

    def multi(name: str) -> tuple[str, ...]:
        if hasattr(values, "getlist"):
            result = values.getlist(name)
        else:
            raw = values.get(name, []) if isinstance(values, dict) else []
            result = raw if isinstance(raw, list) else str(raw).split(",")
        return tuple(value.strip() for value in result if str(value).strip())

    try:
        page = int(values.get("page", 1))
        per_page = int(values.get("per_page", 30))
    except (TypeError, ValueError):
        page, per_page = 1, 30
    return {
        "query": str(values.get("q") or values.get("query") or "").strip(),
        "source_types": multi("source_type"),
        "tag": str(values.get("tag") or "").strip(),
        "domain": str(values.get("domain") or "").strip(),
        "page": page,
        "per_page": per_page,
    }


def _thumbnail_url(resource: dict) -> str:
    if resource.get("thumbnail_path"):
        return url_for(
            "resource_library.resource_asset",
            resource_id=resource["id"],
            kind="thumbnail",
        )
    media_id = resource.get("thumbnail_media_id")
    if media_id and get_thumbnail_store().get_thumbnail_path(media_id):
        return url_for("chrome.serve_bookmark_thumbnail", media_id=media_id)
    return url_for("static", filename="default_thumbnail.svg")


def decorate_resource(resource: dict) -> dict:
    decorated = dict(resource)
    decorated["thumbnail_url"] = _thumbnail_url(resource)
    decorated["detail_url"] = url_for(
        "resource_library.resource_detail", resource_id=resource["id"]
    )
    return decorated


def _form_error(endpoint: str, error: Exception, **values):
    return redirect(url_for(endpoint, error=str(error), **values))
