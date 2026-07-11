"""Flask HTML and JSON surfaces for the independent Gallery domain."""

from __future__ import annotations

import secrets
from urllib.parse import urlencode

from flask import Blueprint, Flask, abort, current_app, jsonify, redirect, render_template, request, url_for

from .models import GALLERY_SOURCES, GalleryItem, GalleryQuery
from .service import CatalogGalleryService, GalleryService, InvalidGalleryCursor
from src.flowinone.resource_library.database import get_resource_database
from pathlib import Path
from src.flowinone.catalog.service import CatalogService


bp = Blueprint("gallery", __name__)
_DEFAULT_SERVICE = GalleryService()


def _service() -> GalleryService:
    factory = current_app.config.get("FLOWINONE_GALLERY_SERVICE_FACTORY")
    if callable(factory):
        return factory()
    configured = current_app.config.get("FLOWINONE_RESOURCE_DB_PATH")
    return CatalogGalleryService(get_resource_database(Path(configured) if configured else None))


def _query() -> GalleryQuery:
    raw_sources = request.args.getlist("source")
    return GalleryQuery.create(
        q=request.args.get("q"),
        sources=raw_sources,
        media_type=request.args.get("type"),
        tags=request.args.getlist("tags") or request.args.get("tags", "").split(","),
        tag_mode=request.args.get("tag_mode"),
        favorite=request.args.get("favorite"),
        unviewed=request.args.get("unviewed"),
        duration_min=request.args.get("duration_min"),
        duration_max=request.args.get("duration_max"),
        added_from=request.args.get("added_from"),
        added_to=request.args.get("added_to"),
        sort=request.args.get("sort"),
        seed=request.args.get("seed"),
        view=request.args.get("view"),
        limit=request.args.get("limit"),
        cursor=request.args.get("cursor"),
    )


def _query_pairs(query: GalleryQuery, *, cursor: str | None = None) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    if query.q:
        pairs.append(("q", query.q))
    pairs.extend(("source", source) for source in query.sources)
    if query.media_type:
        pairs.append(("type", query.media_type))
    if query.tags:
        pairs.append(("tags", ",".join(query.tags)))
        pairs.append(("tag_mode", query.tag_mode))
    if query.favorite:
        pairs.append(("favorite", "1"))
    if query.unviewed:
        pairs.append(("unviewed", "1"))
    if query.duration_min is not None:
        pairs.append(("duration_min", str(query.duration_min)))
    if query.duration_max is not None:
        pairs.append(("duration_max", str(query.duration_max)))
    if query.added_from:
        pairs.append(("added_from", query.added_from))
    if query.added_to:
        pairs.append(("added_to", query.added_to))
    pairs.extend((("sort", query.sort), ("view", query.view), ("limit", str(query.limit))))
    if query.seed:
        pairs.append(("seed", str(query.seed)))
    if cursor:
        pairs.append(("cursor", cursor))
    return pairs


def _detail_url(item: GalleryItem, return_to: str) -> str:
    if item.detail_uri:
        return item.detail_uri
    if item.source == "eagle":
        item_id = item.id.split(":", 1)[1]
        endpoint = "view_eagle_video" if item.media_type == "video" else "view_eagle_image"
        return url_for(endpoint, item_id=item_id, return_to=return_to)
    if item.source == "local" and item.relative_path:
        endpoint = "view_video" if item.media_type == "video" else "view_image"
        return url_for(endpoint, **{f"{item.media_type}_path": item.relative_path, "src": "external"})
    return item.original_url or "#"


def _decorate(item: GalleryItem, return_to: str) -> dict:
    payload = item.public_dict()
    payload["detail_url"] = _detail_url(item, return_to)
    payload["target_blank"] = item.source == "bookmarks"
    return payload


def _page_payload(page, return_to: str) -> dict:
    return {
        "items": [_decorate(item, return_to) for item in page.items],
        "next_cursor": page.next_cursor,
        "total_estimate": page.total,
        "query": page.query.public_dict(),
        "facets": {"sources": page.source_counts},
        "source_errors": page.source_errors,
    }


@bp.get("/gallery/", strict_slashes=False)
def gallery_index():
    query = _query()
    if query.sort == "random" and not query.seed:
        pairs = _query_pairs(query)
        pairs.append(("seed", str(secrets.randbelow(2_147_483_646) + 1)))
        return redirect(f"{url_for('gallery.gallery_index')}?{urlencode(pairs)}")
    try:
        page = _service().list_items(query)
    except InvalidGalleryCursor as exc:
        abort(400, description=str(exc))
    canonical_query = urlencode(_query_pairs(query))
    return_to = f"{url_for('gallery.gallery_index')}?{canonical_query}"
    payload = _page_payload(page, return_to)
    next_url = None
    if page.next_cursor:
        next_url = f"{url_for('gallery.gallery_index')}?{urlencode(_query_pairs(query, cursor=page.next_cursor))}"
    source_labels = {"local": "本機", "eagle": "Eagle", "bookmarks": "書籤"}
    catalog_database = get_resource_database(Path(current_app.config.get("FLOWINONE_RESOURCE_DB_PATH")) if current_app.config.get("FLOWINONE_RESOURCE_DB_PATH") else None)
    recent_sessions = CatalogService(catalog_database).recent_sessions(3)
    for session in recent_sessions:
        pairs = []
        for key, value in session["query"].items():
            if value in (None, "", False, []):
                continue
            if key == "sources":
                pairs.extend(("source", source) for source in value)
            elif key == "tags":
                pairs.append(("tags", ",".join(value)))
            else:
                pairs.append((key, str(int(value)) if isinstance(value, bool) else str(value)))
        session["url"] = f"{url_for('gallery.gallery_index')}?{urlencode(pairs)}"
    return render_template(
        "gallery.html",
        title="Gallery · Flowinone",
        items=payload["items"],
        total=page.total,
        query=query,
        source_options=[
            {
                "key": source,
                "label": source_labels[source],
                "count": page.source_counts.get(source, 0),
                "error": page.source_errors.get(source),
            }
            for source in GALLERY_SOURCES
        ],
        next_url=next_url,
        random_url=url_for("gallery.gallery_index", source=list(query.sources), sort="random"),
        reset_url=url_for("gallery.gallery_index"),
        canonical_query=canonical_query,
        recent_sessions=recent_sessions,
    )


@bp.get("/api/gallery/items")
def api_gallery_items():
    query = _query()
    try:
        page = _service().list_items(query)
    except InvalidGalleryCursor as exc:
        return jsonify({"error": str(exc)}), 400
    return_to = f"{url_for('gallery.gallery_index')}?{urlencode(_query_pairs(query))}"
    return jsonify(_page_payload(page, return_to))


@bp.get("/api/gallery/sources")
def api_gallery_sources():
    query = GalleryQuery.create(sources=GALLERY_SOURCES, limit=1)
    page = _service().list_items(query)
    labels = {"local": "本機", "eagle": "Eagle", "bookmarks": "書籤"}
    return jsonify(
        {
            "items": [
                {
                    "key": source,
                    "name": labels[source],
                    "count": page.source_counts[source],
                    "available": source not in page.source_errors,
                    "error": page.source_errors.get(source),
                }
                for source in GALLERY_SOURCES
            ]
        }
    )


def register_gallery(app: Flask) -> None:
    app.register_blueprint(bp)


__all__ = ["bp", "register_gallery"]
