"""Flask HTML and JSON surfaces for the independent Gallery domain."""

from __future__ import annotations

from urllib.parse import urlencode

from flask import Blueprint, Flask, abort, current_app, redirect, render_template, request, url_for

from .models import GALLERY_SOURCES, GalleryItem, GalleryQuery
from .service import CatalogGalleryService, GalleryService, InvalidGalleryCursor
from src.flowinone.resource_library.database import get_resource_database
from pathlib import Path
from src.flowinone.web.api import api_error, parse_query, validated_json
from src.flowinone.web.schemas import (
    GalleryItemsOutput,
    GalleryListQuery,
    GallerySourcesOutput,
)


bp = Blueprint("gallery", __name__)
_DEFAULT_SERVICE = GalleryService()

GALLERY_LAB_VARIANTS = (
    {"number": "01", "slug": "gsap-filmstrip", "family": "gsap", "family_label": "GSAP", "name": "動態膠卷", "summary": "橫向敘事與分鏡式進場，強調瀏覽節奏。"},
    {"number": "02", "slug": "gsap-masonry", "family": "gsap", "family_label": "GSAP", "name": "動態瀑布", "summary": "不等高構圖配合批次捲動揭露。"},
    {"number": "07", "slug": "taste-contact", "family": "taste", "family_label": "Taste", "name": "影像接觸表", "summary": "銳利、緊湊、像攝影工作室的素材索引。"},
    {"number": "10", "slug": "uipro-workbench", "family": "uipro", "family_label": "UI/UX Pro Max", "name": "素材工作台", "summary": "桌面高效率側欄，行動版自然回到單欄。"},
    {"number": "12", "slug": "uipro-library", "family": "uipro", "family_label": "UI/UX Pro Max", "name": "學術收藏室", "summary": "知識庫語彙、沉穩排版與可讀性優先。"},
    {"number": "16", "slug": "gsap-rhythm", "family": "gsap", "family_label": "GSAP", "name": "節奏矩陣", "summary": "規整矩陣配合波浪式動態，兼顧效率與趣味。"},
    {"number": "18", "slug": "taste-swiss", "family": "taste", "family_label": "Taste", "name": "瑞士索引", "summary": "紅黑高對比、嚴謹格線與直接的資訊層級。"},
    {"number": "21", "slug": "uipro-console", "family": "uipro", "family_label": "UI/UX Pro Max", "name": "媒體控制台", "summary": "高資訊密度、清楚狀態與鍵盤友善的管理介面。"},
    {"number": "23", "slug": "uipro-board", "family": "uipro", "family_label": "UI/UX Pro Max", "name": "收藏看板", "summary": "穩定卡片欄位、明確操作區與清楚內容狀態。"},
)


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
        endpoint = (
            "eagle.view_eagle_video"
            if item.media_type == "video"
            else "eagle.view_eagle_image"
        )
        return url_for(endpoint, item_id=item_id, return_to=return_to)
    if item.source == "local" and item.relative_path:
        endpoint = "media.view_video" if item.media_type == "video" else "media.view_image"
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
    """Legacy deep link for the former Gallery destination."""
    args = request.args.to_dict(flat=False)
    args["scope"] = ["gallery"]
    return redirect(f"{url_for('catalog.navigator_page')}?{urlencode(args, doseq=True)}")


@bp.get("/gallery/lab", strict_slashes=False)
def gallery_lab_index():
    """Numbered comparison surface for the retained gallery design studies."""
    return render_template(
        "gallery_lab_index.html",
        title="Gallery Lab · Flowinone",
        variants=GALLERY_LAB_VARIANTS,
    )


@bp.get("/gallery/lab/<variant_slug>")
def gallery_lab_variant(variant_slug: str):
    """Render one design study with the production Gallery query and item data."""
    variant = next((item for item in GALLERY_LAB_VARIANTS if item["slug"] == variant_slug), None)
    if variant is None:
        abort(404)
    query = _query()
    try:
        page = _service().list_items(query)
    except InvalidGalleryCursor as exc:
        abort(400, description=str(exc))
    canonical_query = urlencode(_query_pairs(query))
    variant_url = url_for("gallery.gallery_lab_variant", variant_slug=variant_slug)
    return_to = f"{variant_url}?{canonical_query}"
    payload = _page_payload(page, return_to)
    next_url = None
    if page.next_cursor:
        next_url = f"{variant_url}?{urlencode(_query_pairs(query, cursor=page.next_cursor))}"
    source_labels = {"local": "本機", "eagle": "EAGLE", "bookmarks": "書籤"}
    return render_template(
        "gallery_lab.html",
        title=f"{variant['number']} {variant['name']} · Flowinone",
        variant=variant,
        variants=GALLERY_LAB_VARIANTS,
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
        reset_url=variant_url,
        canonical_query=canonical_query,
    )


@bp.get("/api/gallery/items")
def api_gallery_items():
    values = parse_query(
        GalleryListQuery, list_fields=("source", "tags")
    ).model_dump()
    query = GalleryQuery.create(
        q=values["q"],
        sources=values["source"],
        media_type=values["type"],
        tags=values["tags"],
        tag_mode=values["tag_mode"],
        favorite=values["favorite"],
        unviewed=values["unviewed"],
        duration_min=values["duration_min"],
        duration_max=values["duration_max"],
        added_from=values["added_from"],
        added_to=values["added_to"],
        sort=values["sort"],
        seed=values["seed"],
        view=values["view"],
        limit=values["limit"],
        cursor=values["cursor"],
    )
    try:
        page = _service().list_items(query)
    except InvalidGalleryCursor as exc:
        return api_error(str(exc), 400)
    return_to = f"{url_for('gallery.gallery_index')}?{urlencode(_query_pairs(query))}"
    return validated_json(_page_payload(page, return_to), GalleryItemsOutput)


@bp.get("/api/gallery/sources")
def api_gallery_sources():
    query = GalleryQuery.create(sources=GALLERY_SOURCES, limit=1)
    page = _service().list_items(query)
    labels = {"local": "本機", "eagle": "EAGLE", "bookmarks": "書籤"}
    return validated_json(
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
        },
        GallerySourcesOutput,
    )


def register_gallery(app: Flask) -> None:
    app.register_blueprint(bp)


__all__ = ["bp", "register_gallery"]
