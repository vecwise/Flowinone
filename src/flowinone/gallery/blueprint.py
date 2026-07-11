"""Flask HTML and JSON surfaces for the independent Gallery domain."""

from __future__ import annotations

import secrets
from urllib.parse import urlencode

from flask import Blueprint, Flask, abort, current_app, jsonify, redirect, render_template, request, url_for

from .models import GALLERY_SOURCES, GalleryItem, GalleryQuery
from .service import GalleryService, InvalidGalleryCursor


bp = Blueprint("gallery", __name__)
_DEFAULT_SERVICE = GalleryService()


def _service() -> GalleryService:
    factory = current_app.config.get("FLOWINONE_GALLERY_SERVICE_FACTORY")
    return factory() if callable(factory) else _DEFAULT_SERVICE


def _query() -> GalleryQuery:
    raw_sources = request.args.getlist("source")
    return GalleryQuery.create(
        q=request.args.get("q"),
        sources=raw_sources,
        media_type=request.args.get("type"),
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
    pairs.extend((("sort", query.sort), ("view", query.view), ("limit", str(query.limit))))
    if query.seed:
        pairs.append(("seed", str(query.seed)))
    if cursor:
        pairs.append(("cursor", cursor))
    return pairs


def _detail_url(item: GalleryItem, return_to: str) -> str:
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
