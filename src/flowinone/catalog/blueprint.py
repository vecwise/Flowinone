"""Navigator and duplicate review pages plus Catalog registration."""

from __future__ import annotations

import secrets
from datetime import datetime
from urllib.parse import urlencode

from flask import Flask, abort, current_app, redirect, render_template, request, url_for

from src.file_handler.eagle_integration import is_eagle_available

from . import api as _api  # Register JSON routes on the shared Blueprint.
from .browse import CatalogService
from .commands import register_catalog_commands
from .http import bp, _database
from .presentation import SOURCE_LABELS, visible_items
from .query import CATALOG_SOURCES, CatalogQuery
from .similarity import CatalogSimilarityService
from .sync import CatalogSyncService
from .watch import CatalogSourceWatcher

NAVIGATOR_SCOPE_SOURCES = {
    "gallery": tuple(source for source in CATALOG_SOURCES if source != "resources"),
    "all": CATALOG_SOURCES,
}
ITEM_TYPE_LABELS = {
    "image": "圖片",
    "video": "影片",
    "bookmark": "書籤",
    "article": "文章",
    "web_page": "網頁",
    "pdf": "PDF",
    "github": "GitHub",
    "social_post": "社群貼文",
    "file": "檔案",
    "folder": "資料夾",
    "unknown": "其他",
}


def _navigator_query() -> CatalogQuery:
    """Build a query whose source defaults always match the chosen scope."""
    scope = request.args.get("scope", "gallery").strip().lower()
    if scope not in NAVIGATOR_SCOPE_SOURCES:
        scope = "gallery"
    allowed_sources = NAVIGATOR_SCOPE_SOURCES[scope]
    requested_sources = request.args.getlist("source")
    sources = tuple(source for source in requested_sources if source in allowed_sources) or allowed_sources
    return CatalogQuery.create(
        q=request.args.get("q"), scope=scope, sources=sources,
        item_type=request.args.get("type"),
        tags=request.args.getlist("tags") or request.args.get("tags"),
        tag_mode=request.args.get("tag_mode"), favorite=request.args.get("favorite"),
        unviewed=request.args.get("unviewed"),
        duration_min=request.args.get("duration_min"), duration_max=request.args.get("duration_max"),
        added_from=request.args.get("added_from"), added_to=request.args.get("added_to"),
        sort=request.args.get("sort"), seed=request.args.get("seed"),
        limit=request.args.get("limit"), cursor=request.args.get("cursor"),
    )


def _navigator_query_pairs(query: CatalogQuery, *, cursor: str | None = None) -> list[tuple[str, str]]:
    pairs = [("scope", query.scope)]
    if query.q:
        pairs.append(("q", query.q))
    pairs.extend(("source", source) for source in query.sources)
    if query.item_type:
        pairs.append(("type", query.item_type))
    if query.tags:
        pairs.extend((("tags", ",".join(query.tags)), ("tag_mode", query.tag_mode)))
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
    pairs.extend((("sort", query.sort), ("limit", str(query.limit))))
    if query.seed:
        pairs.append(("seed", str(query.seed)))
    if cursor:
        pairs.append(("cursor", cursor))
    return pairs


def _navigator_url(query: CatalogQuery, *, cursor: str | None = None) -> str:
    return f"{url_for('catalog.navigator_page')}?{urlencode(_navigator_query_pairs(query, cursor=cursor))}"


def _with_scope(query: CatalogQuery, scope: str) -> CatalogQuery:
    values = query.public_dict()
    values.update({"scope": scope, "sources": NAVIGATOR_SCOPE_SOURCES[scope], "cursor": ""})
    return CatalogQuery.create(**values)


def _navigator_recent_sessions(service: CatalogService) -> list[dict]:
    sessions = []
    for session in service.recent_sessions(12):
        saved_query = dict(session.get("query") or {})
        scope = saved_query.get("scope") or "gallery"
        if scope not in NAVIGATOR_SCOPE_SOURCES:
            scope = "gallery"
        saved_query["scope"] = scope
        saved_query["sources"] = tuple(
            source for source in saved_query.get("sources") or ()
            if source in NAVIGATOR_SCOPE_SOURCES[scope]
        ) or NAVIGATOR_SCOPE_SOURCES[scope]
        session_query = CatalogQuery.create(**saved_query)
        meaningful = bool(
            session_query.q
            or session_query.item_type
            or session_query.tags
            or session_query.favorite
            or session_query.unviewed
            or session_query.duration_min is not None
            or session_query.duration_max is not None
            or session_query.sources != NAVIGATOR_SCOPE_SOURCES[scope]
            or session_query.sort != "recently_added"
        )
        if not meaningful:
            continue
        session["url"] = _navigator_url(session_query)
        session["scope_label"] = "素材" if scope == "gallery" else "全部內容"
        try:
            session["updated_display"] = datetime.fromisoformat(
                str(session.get("updated_at") or "").replace("Z", "+00:00")
            ).astimezone().strftime("%Y-%m-%d %H:%M")
        except ValueError:
            session["updated_display"] = session.get("updated_at") or ""
        sessions.append(session)
        if len(sessions) == 3:
            break
    return sessions


def _saved_search_summary(query: CatalogQuery) -> str:
    details = []
    if query.q:
        details.append(f"「{query.q}」")
    if query.tags:
        details.append(f"標籤：{', '.join(query.tags)}")
    if query.favorite:
        details.append("我的最愛")
    if query.unviewed:
        details.append("尚未瀏覽")
    if query.item_type:
        details.append(ITEM_TYPE_LABELS.get(query.item_type, query.item_type))
    return " · ".join(details) or "篩選瀏覽"


def _navigator_saved_searches(service: CatalogService) -> list[dict]:
    saved_searches = []
    for saved in service.list_saved_searches():
        query = CatalogQuery.create(**(saved.get("query") or {}))
        saved["url"] = _navigator_url(query)
        saved["scope_label"] = "素材" if query.scope == "gallery" else "全部內容"
        saved["summary"] = _saved_search_summary(query)
        saved_searches.append(saved)
    return saved_searches


@bp.get("/navigator/", strict_slashes=False)
def navigator_page():
    service = CatalogService(_database())
    query = _navigator_query()
    if query.sort == "random" and not query.seed:
        values = query.public_dict()
        values["seed"] = secrets.randbelow(2_147_483_646) + 1
        return redirect(_navigator_url(CatalogQuery.create(**values)))

    eagle_available = is_eagle_available() if "eagle" in query.sources else True
    active_sources = tuple(source for source in query.sources if source != "eagle" or eagle_available)
    values = query.public_dict()
    values["sources"] = active_sources
    effective_query = CatalogQuery.create(**values)

    if service.count() == 0 and current_app.config.get(
        "FLOWINONE_CATALOG_SYNC_INLINE", False
    ):
        CatalogSyncService(_database()).sync_if_empty(effective_query.sources)
    try:
        payload = service.list(effective_query) if active_sources else {
            "items": [], "next_cursor": None, "total_estimate": 0,
            "facets": service.facets(query),
        }
    except ValueError as exc:
        abort(400, description=str(exc))
    payload["items"] = visible_items(service, payload, effective_query)
    if not payload["next_cursor"]:
        payload["total_estimate"] = len(payload["items"])
    next_url = None
    if payload["next_cursor"]:
        next_url = _navigator_url(effective_query, cursor=payload["next_cursor"])
    scope_sources = NAVIGATOR_SCOPE_SOURCES[query.scope]
    reset_query = CatalogQuery.create(scope=query.scope, sources=scope_sources)
    random_values = query.public_dict()
    random_values.update({"sort": "random", "seed": 0, "cursor": ""})
    return render_template(
        "navigator.html",
        title="Navigator · Flowinone",
        payload=payload,
        query=query,
        source_options=[
            {
                "key": source,
                "label": SOURCE_LABELS[source],
                "count": payload["facets"]["sources"].get(source, 0),
                "error": "目前未連線" if source == "eagle" and not eagle_available else None,
            }
            for source in scope_sources
        ],
        catalog_sources=[
            {"key": source, "label": SOURCE_LABELS[source]}
            for source in CATALOG_SOURCES
        ],
        next_url=next_url,
        reset_url=_navigator_url(reset_query),
        random_url=_navigator_url(CatalogQuery.create(**random_values)),
        scope_urls={scope: _navigator_url(_with_scope(query, scope)) for scope in NAVIGATOR_SCOPE_SOURCES},
        recent_sessions=_navigator_recent_sessions(service),
        saved_searches=_navigator_saved_searches(service),
        quick_filter_urls={
            "favorites": _navigator_url(
                CatalogQuery.create(
                    scope=query.scope,
                    sources=NAVIGATOR_SCOPE_SOURCES[query.scope],
                    favorite=True,
                )
            ),
            "unviewed": _navigator_url(
                CatalogQuery.create(
                    scope=query.scope,
                    sources=NAVIGATOR_SCOPE_SOURCES[query.scope],
                    unviewed=True,
                )
            ),
        },
        source_watch_status=CatalogSourceWatcher(_database()).status(),
        similarity_status=CatalogSimilarityService(_database()).status(),
        eagle_available=eagle_available,
        item_type_labels=ITEM_TYPE_LABELS,
    )


@bp.get("/catalog/duplicate-review/")
def duplicate_review_page():
    """Review every current exact duplicate group in the Local image library."""
    review = CatalogSimilarityService(_database()).duplicate_review()
    return render_template(
        "duplicate_review.html",
        title="重複圖片檢閱 · Flowinone",
        review=review,
    )


def register_catalog(app: Flask) -> None:
    app.config.setdefault(
        "FLOWINONE_CATALOG_SYNC_INLINE", bool(app.config.get("TESTING"))
    )
    app.register_blueprint(bp)
    register_catalog_commands(app)


__all__ = ["bp", "register_catalog"]
