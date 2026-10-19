"""Persistent browser-wide source selection for browsing, separate from maintenance."""

from dataclasses import replace
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from flask import Blueprint, abort, redirect, request, url_for

from src.flowinone.catalog.query import CATALOG_SOURCES, CatalogQuery

COOKIE_NAME = "flowinone_browse_sources"
SOURCE_OPTIONS = (
    ("bookmarks", "書籤"), ("eagle", "Eagle"),
    ("local", "本機"), ("resources", "新增網址"),
)
bp = Blueprint("browse_sources", __name__)


def source_preference() -> tuple[str, ...]:
    raw = request.cookies.get(COOKIE_NAME, "").split(",")
    return tuple(source for source in CATALOG_SOURCES if source in raw)


def current_sources() -> tuple[str, ...]:
    preferred = source_preference()
    if preferred:
        return preferred
    if request.endpoint == "catalog.navigator_page":
        if request.args.get("type") == "bookmark":
            return ("bookmarks",)
        allowed = CATALOG_SOURCES[:-1] if request.args.get("scope") == "gallery" else CATALOG_SOURCES
        return tuple(source for source in allowed if source in request.args.getlist("source")) or allowed
    return CATALOG_SOURCES


def apply_source_preference(query: CatalogQuery) -> CatalogQuery:
    preferred = source_preference()
    if not preferred:
        return query
    changed = query.sources != preferred or query.scope != "all"
    return replace(query, sources=preferred, scope="all", cursor="" if changed else query.cursor)


def source_return_url(value: str, sources: tuple[str, ...]) -> str:
    """Retain only registered local HTML destinations; invalidate old paging."""
    from .ui_variants import is_ui_url

    if not is_ui_url(value):
        value = url_for("catalog.navigator_page")
    target = urlsplit(value)
    if target.path.rstrip("/") == "/navigator":
        pairs = [(key, item) for key, item in parse_qsl(target.query)
                 if key not in {"source", "scope", "cursor", "resume_scroll", "context"}
                 and not (key == "type" and item == "bookmark")]
        pairs.extend([("scope", "all"), *(("source", source) for source in sources)])
        target = target._replace(query=urlencode(pairs), fragment="")
    return urlunsplit(target._replace(scheme="", netloc=""))


@bp.post("/browse-sources/")
def update_sources():
    requested = request.form.getlist("source")
    action = request.form.get("source_action")
    if action == "all":
        requested = CATALOG_SOURCES
    elif action == "add_bookmarks":
        requested = [*requested, "bookmarks"]
    if any(source not in CATALOG_SOURCES for source in requested):
        abort(400, description="不支援的瀏覽來源。")
    sources = tuple(source for source in CATALOG_SOURCES if source in requested)
    if not sources:
        abort(400, description="至少保留一個瀏覽來源。")
    response = redirect(source_return_url(request.form.get("return_to", ""), sources), code=303)
    response.set_cookie(COOKIE_NAME, ",".join(sources), max_age=365 * 24 * 60 * 60,
                        httponly=True, samesite="Lax", secure=request.is_secure)
    return response


def register_browse_sources(app):
    app.register_blueprint(bp)

    @app.context_processor
    def browse_source_context():
        return {"browse_source_options": SOURCE_OPTIONS, "browse_selected_sources": current_sources()}
