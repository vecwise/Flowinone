"""Navigator and duplicate review pages plus Catalog registration."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

from flask import Flask, abort, current_app, g, redirect, render_template, request, url_for

from src.file_handler.eagle_integration import is_eagle_available
from src.flowinone.config import CHROME_BOOKMARK_PATH

from src.flowinone.web.ui import browse_view, render_catalog_browse
from src.flowinone.web.browse_sources import SOURCE_OPTIONS, apply_source_preference, source_preference

from . import api as _api  # Register JSON routes on the shared Blueprint.
from .browse import CatalogService
from .commands import register_catalog_commands
from .http import bp, _database
from .presentation import SOURCE_LABELS, bookmark_source_health as _bookmark_source_health, visible_items
from .query import CATALOG_SOURCES, CatalogQuery
from .media import MEDIA_LABELS
from .similarity import CatalogSimilarityService
from .sync import CatalogSyncService
from .eagle_library import EagleLibraryService
from .targets import BrowseTargetService

NAVIGATOR_SCOPE_SOURCES = {
    "gallery": tuple(source for source in CATALOG_SOURCES if source != "resources"),
    "all": CATALOG_SOURCES,
}
ITEM_TYPE_LABELS = {
    "image": "圖片",
    "video": "影片",
    "bookmark": "網頁",
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
    scope = request.args.get("scope", "all").strip().lower()
    if scope not in NAVIGATOR_SCOPE_SOURCES:
        scope = "all"
    allowed_sources = NAVIGATOR_SCOPE_SOURCES[scope]
    requested_sources = request.args.getlist("source")
    sources = tuple(source for source in requested_sources if source in allowed_sources) or allowed_sources
    item_type = request.args.get("type")
    if item_type == "bookmark":
        # Older links used bookmark as a type, although it denotes an origin.
        sources = ("bookmarks",)
        item_type = None
    return apply_source_preference(CatalogQuery.create(
        q=request.args.get("q"), scope=scope, sources=sources,
        item_type=item_type,
        media=request.args.get("media"),
        folder=request.args.get("folder"),
        target=request.args.get("target"),
        tags=request.args.getlist("tags") or request.args.get("tags"),
        tag_mode=request.args.get("tag_mode"), favorite=request.args.get("favorite"),
        unviewed=request.args.get("unviewed"),
        duration_min=request.args.get("duration_min"), duration_max=request.args.get("duration_max"),
        added_from=request.args.get("added_from"), added_to=request.args.get("added_to"),
        sort=request.args.get("sort"), seed=request.args.get("seed"),
        limit=request.args.get("limit"), cursor=request.args.get("cursor"),
    ))


def _navigator_query_pairs(query: CatalogQuery, *, cursor: str | None = None) -> list[tuple[str, str]]:
    pairs = [("scope", query.scope)]
    if query.q:
        pairs.append(("q", query.q))
    pairs.extend(("source", source) for source in query.sources)
    if query.item_type:
        pairs.append(("type", query.item_type))
    if query.media:
        pairs.append(("media", query.media))
    if query.folder:
        pairs.append(("folder", query.folder))
    if query.target:
        pairs.append(("target", query.target))
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


def _navigator_view() -> str:
    """Keep old knowledge links readable through the remaining list layout."""
    return browse_view()


def _navigator_url(query: CatalogQuery, *, cursor: str | None = None) -> str:
    pairs = _navigator_query_pairs(query, cursor=cursor)
    if _navigator_view() == "list":
        pairs.append(("view", "list"))
    if request.args.get("shape") in {"landscape", "portrait", "square"}:
        pairs.append(("shape", request.args["shape"]))
    context = getattr(g, "navigator_context", None)
    if context and apply_source_preference(CatalogQuery.create(**context["query"])).signature() == query.signature():
        pairs.append(("context", context["id"]))
    return f"{url_for('catalog.navigator_page')}?{urlencode(pairs)}"


def _quick_filter_url(query: CatalogQuery, **changes) -> str:
    values = query.public_dict()
    values.update(changes)
    values["cursor"] = ""
    return _navigator_url(CatalogQuery.create(**values))


def _with_scope(query: CatalogQuery, scope: str) -> CatalogQuery:
    values = query.public_dict()
    values.update({"scope": scope, "sources": NAVIGATOR_SCOPE_SOURCES[scope], "cursor": ""})
    return apply_source_preference(CatalogQuery.create(**values))


def _gallery_home(query: CatalogQuery) -> bool:
    return not any((query.q, query.item_type, query.media, query.folder, query.target, query.tags,
                    query.favorite, query.unviewed, query.duration_min is not None,
                    query.duration_max is not None, query.added_from, query.added_to,
                    query.cursor, query.sort != "recently_added"))


def _gallery_filter_chips(query: CatalogQuery) -> list[dict]:
    chips = []

    def add(label, **changes):
        chips.append({"label": label, "url": _quick_filter_url(query, **changes)})

    if query.q:
        add(f"搜尋：{query.q}", q="")
    if query.target:
        target = g.navigator_target
        add(f"{target['prefix']}：{target['label']}", target="")
    for tag in query.tags:
        add(tag, tags=tuple(value for value in query.tags if value != tag))
    if query.media:
        add(MEDIA_LABELS[query.media], media="")
    if query.item_type:
        add(ITEM_TYPE_LABELS.get(query.item_type, query.item_type), type="", item_type="")
    if query.folder:
        add(f"資料夾：{query.folder}", folder="")
    if query.favorite:
        add("只看最愛", favorite=False)
    if query.unviewed:
        add("尚未瀏覽", unviewed=False)
    if query.duration_min is not None or query.duration_max is not None:
        minimum = query.duration_min if query.duration_min is not None else 0
        maximum = query.duration_max if query.duration_max is not None else "不限"
        add(f"長度：{minimum}–{maximum} 秒", duration_min=None, duration_max=None)
    if query.added_from or query.added_to:
        add(f"加入日期：{query.added_from or '不限'}–{query.added_to or '不限'}", added_from="", added_to="")
    return chips


def _navigator_topics(
    service: CatalogService, query: CatalogQuery, facets: dict, *, active_sources: tuple[str, ...],
) -> list[dict]:
    """Use a bounded facet pool and existing browse filters for topic discovery."""
    item_count = sum(facets["types"].values())
    candidates = [tag for tag in facets["tags"] if tag["count"] < item_count * 0.9]
    # A small library may have only one tag shared by every item.
    candidates = candidates or facets["tags"]
    show_previews = _gallery_home(query)
    topics = []
    for tag in candidates[:6]:
        preview = show_previews and len(topics) < 3
        values = query.public_dict()
        values.update({
            "sources": active_sources,
            "tags": (tag["name"],), "tag_mode": "any", "cursor": "", "target": "",
            "limit": 3 if preview else 1,
        })
        topic_query = CatalogQuery.create(**values)
        page = service.list(topic_query, include_facets=False, include_items=preview)
        if not page["total_estimate"]:
            continue
        items = visible_items(service, page, topic_query) if preview else []
        # Topics replace a typed destination while retaining other browse filters.
        target = _quick_filter_url(query, tags=topic_query.tags, tag_mode="any", target="")
        preview_target = _quick_filter_url(
            query, tags=topic_query.tags, tag_mode="any", target="", limit=max(query.limit, len(items)),
        )
        topics.append({
            "name": tag["name"], "count": page["total_estimate"], "url": target,
            "selected": topic_query.tags[0] in query.tags,
            "items": [
                {**item, "browse_url": f"{preview_target}#item-{item['id']}"}
                for item in items
            ],
        })
    return topics


def _navigator_recent_sessions(service: CatalogService) -> list[dict]:
    sessions = []
    for session in service.recent_sessions(12):
        saved_query = dict(session.get("query") or {})
        scope = saved_query.get("scope") or "all"
        if scope not in NAVIGATOR_SCOPE_SOURCES:
            scope = "all"
        saved_query["scope"] = scope
        saved_query["sources"] = tuple(
            source for source in saved_query.get("sources") or ()
            if source in NAVIGATOR_SCOPE_SOURCES[scope]
        ) or NAVIGATOR_SCOPE_SOURCES[scope]
        original_query = CatalogQuery.create(**saved_query)
        session_query = apply_source_preference(original_query)
        if session_query != original_query:
            session = {**session, "cursor": "", "scroll_position": 0, "focused_item_id": None}
        meaningful = bool(
            session_query.q
            or session_query.item_type
            or session_query.media
            or session_query.tags
            or session_query.target
            or session_query.favorite
            or session_query.unviewed
            or session_query.duration_min is not None
            or session_query.duration_max is not None
            or session_query.sources != NAVIGATOR_SCOPE_SOURCES[scope]
            or session_query.sort != "recently_added"
            or session.get("focused_item_id")
            or session_query.cursor
            or session.get("scroll_position")
        )
        if not meaningful:
            continue
        session["url"] = (
            _navigator_url(session_query, cursor=session.get("cursor") or session_query.cursor)
            + f"&resume_scroll={max(0, int(session.get('scroll_position') or 0))}"
        )
        if session.get("focused_item_id"):
            session["url"] += f"#item-{session['focused_item_id']}"
            try:
                session["focused_title"] = service.get(session["focused_item_id"])["title"]
            except LookupError:
                session["focused_title"] = None
        session["summary"] = _saved_search_summary(session_query)
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
    if query.target:
        try:
            target = BrowseTargetService(_database()).describe(query.target)
            details.append(f"{target['prefix']}：{target['label']}")
        except LookupError:
            details.append("已失效的瀏覽入口")
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
    if query.media:
        details.append(MEDIA_LABELS[query.media])
    if query.folder:
        details.append(f"資料夾：{query.folder}")
    return " · ".join(details) or "篩選瀏覽"


def _navigator_saved_searches(service: CatalogService) -> list[dict]:
    saved_searches = []
    for saved in service.list_saved_searches():
        query = CatalogQuery.create(**(saved.get("query") or {}))
        saved["url"] = url_for(
            "catalog.navigator_saved_view", saved_search_id=saved["id"],
            view="list" if _navigator_view() == "list" else None,
        )
        saved["scope_label"] = "素材" if query.scope == "gallery" else "全部內容"
        saved["summary"] = _saved_search_summary(query)
        saved_searches.append(saved)
    return saved_searches


@bp.get("/navigator/views/<saved_search_id>/")
def navigator_saved_view(saved_search_id: str):
    """Open a named view through its stable, shareable local URL."""
    try:
        saved = CatalogService(_database()).get_saved_search(saved_search_id)
    except LookupError:
        abort(404)
    query = apply_source_preference(CatalogQuery.create(**saved["query"]))
    target = _navigator_url(query)
    return redirect(f"{target}&context={saved_search_id}")


@bp.get("/navigator/", strict_slashes=False)
def navigator_page():
    if request.args.get("manage") == "1":
        pairs = [(key, value) for key, value in request.args.items(multi=True) if key != "manage"]
        return_url = url_for("catalog.navigator_page") + ("?" + urlencode(pairs) if pairs else "")
        return redirect(url_for("local.content_library", return_to=return_url) + "#collection-maintenance")
    service = CatalogService(_database())
    try:
        query = _navigator_query()
        g.navigator_target = BrowseTargetService(_database()).describe(query.target) if query.target else None
    except ValueError as exc:
        abort(400, description=str(exc))
    except LookupError:
        abort(404, description="找不到這個瀏覽入口。")
    preferred = source_preference()
    if preferred and (tuple(request.args.getlist("source")) != preferred
                      or request.args.get("scope", "all") != "all"):
        return redirect(_navigator_url(query, cursor=query.cursor))
    view = _navigator_view()
    context_id = request.args.get("context", "").strip()
    if context_id:
        try:
            candidate = service.get_saved_search(context_id)
            if apply_source_preference(CatalogQuery.create(**candidate["query"])).signature() == query.signature():
                g.navigator_context = candidate
        except LookupError:
            pass
        if not getattr(g, "navigator_context", None):
            pairs = [(key, value) for key, value in request.args.items(multi=True) if key != "context"]
            return redirect(f"{url_for('catalog.navigator_page')}?{urlencode(pairs)}")
    if query.sort == "random" and not query.seed:
        values = query.public_dict()
        values["seed"] = secrets.randbelow(2_147_483_646) + 1
        target = _navigator_url(CatalogQuery.create(**values))
        if request.args.get("resume_scroll") == "0":
            target += "&resume_scroll=0"
        return redirect(target)

    eagle_available = is_eagle_available() if "eagle" in query.sources else True
    if "eagle" in query.sources:
        eagle_available = eagle_available and EagleLibraryService(_database()).visibility()["allowed"]
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
    return_url = _navigator_url(query, cursor=query.cursor)
    for item in payload["items"]:
        detail_uri = item.get("resource_detail_uri")
        if detail_uri and detail_uri.startswith("/resources/"):
            contextual_uri = f"{detail_uri}?{urlencode({'return_to': return_url})}"
            item["resource_detail_uri"] = contextual_uri
            if item.get("launch_uri") == detail_uri:
                item["launch_uri"] = contextual_uri
    excerpts = service.text_excerpts_for_items([
        item["id"] for item in payload["items"] if not item.get("card_description")
    ])
    for item in payload["items"]:
        item["content_excerpt"] = excerpts.get(item["id"], "")
    bookmark_source_health = _bookmark_source_health(
        Path(CHROME_BOOKMARK_PATH),
        payload["facets"]["sync"].get("bookmarks", {}),
        payload["facets"]["sources"].get("bookmarks", 0),
    ) if "bookmarks" in query.sources else None
    next_url = None
    if payload["next_cursor"]:
        next_url = _navigator_url(query, cursor=payload["next_cursor"])
    folder_children = service.folder_children(query.folder)
    folder_crumbs = []
    folder_parts = query.folder.split(" / ") if query.folder else []
    for depth in range(1, len(folder_parts) + 1):
        path = " / ".join(folder_parts[:depth])
        folder_crumbs.append({"name": path.rsplit(" / ", 1)[-1], "url": _quick_filter_url(query, folder=path)})
    topics = _navigator_topics(
        service, query, payload["facets"], active_sources=active_sources,
    ) if active_sources else []
    reset_query = CatalogQuery.create(scope=query.scope, sources=query.sources)
    random_values = query.public_dict()
    random_values.update({"sort": "random", "seed": 0, "cursor": ""})
    active_context = getattr(g, "navigator_context", None)
    share_url = (
        url_for(
            "catalog.navigator_saved_view", saved_search_id=active_context["id"],
            view="list" if view == "list" else None, _external=True,
        )
        if active_context else request.url_root.rstrip("/") + _navigator_url(
            CatalogQuery.create(**{**query.public_dict(), "cursor": ""})
        )
    )
    filter_chips = _gallery_filter_chips(query)
    gallery_heading = (
        active_context["label"] if active_context else
        g.navigator_target["label"] if query.target else
        "、".join(query.tags) if query.tags else
        f"「{query.q}」的搜尋結果" if query.q else
        query.folder.rsplit(" / ", 1)[-1] if query.folder else
        f"{MEDIA_LABELS[query.media]}收藏" if query.media else "全部收藏"
    )
    source_summary = "全部來源" if set(query.sources) == set(CATALOG_SOURCES) else "＋".join(
        label for key, label in SOURCE_OPTIONS if key in query.sources
    )
    show_context = bool(
        active_context or query.q or query.item_type or query.media or query.folder or query.target or query.tags
        or query.favorite or query.unviewed or query.duration_min is not None
        or query.duration_max is not None or query.added_from or query.added_to
    )
    return render_catalog_browse(
        title=f"{gallery_heading} · Flowinone",
        gallery_heading=gallery_heading,
        gallery_home=_gallery_home(query),
        filter_chips=filter_chips,
        filter_count=len(filter_chips) + int(set(query.sources) != set(CATALOG_SOURCES)),
        source_summary=source_summary,
        current_context={
            "label": active_context["label"] if active_context else "目前條件",
            "named": bool(active_context),
            "summary": _saved_search_summary(query),
            "share_url": share_url,
            "clear_url": _navigator_url(reset_query),
            "return_url": None,
        } if show_context else None,
        view=view,
        resume_scroll=request.args.get("resume_scroll", ""),
        payload=payload,
        query=query,
        browse_target=g.navigator_target,
        catalog_sources=[
            {"key": source, "label": SOURCE_LABELS[source]}
            for source in CATALOG_SOURCES
        ],
        next_url=next_url,
        reset_url=_navigator_url(reset_query),
        random_url=_navigator_url(CatalogQuery.create(**random_values)),
        all_sources_url=_navigator_url(_with_scope(query, "all")),
        sort_fields=[(key, value) for key, value in _navigator_query_pairs(query)
                     if key not in {"sort", "cursor"}]
                    + ([("view", "list")] if view == "list" else [])
                    + ([("shape", request.args["shape"])] if request.args.get("shape") else []),
        recent_sessions=_navigator_recent_sessions(service),
        saved_searches=_navigator_saved_searches(service),
        quick_filter_urls={
            "favorites": _navigator_url(
                CatalogQuery.create(
                    scope=query.scope,
                    sources=query.sources,
                    favorite=True,
                )
            ),
            "unviewed": _navigator_url(
                CatalogQuery.create(
                    scope=query.scope,
                    sources=query.sources,
                    unviewed=True,
                )
            ),
        },
        folder_children=[
            {**folder, "url": _quick_filter_url(query, folder=folder["path"], cursor="")}
            for folder in folder_children
        ],
        folder_crumbs=folder_crumbs,
        folder_clear_url=_quick_filter_url(query, folder=""),
        topics=topics,
        topic_clear_url=_quick_filter_url(query, tags=()),
        eagle_available=eagle_available,
        bookmark_source_health=bookmark_source_health,
        item_type_labels=ITEM_TYPE_LABELS,
        media_labels=MEDIA_LABELS,
    )


@bp.get("/catalog/duplicate-review/")
def duplicate_review_page():
    """Review every current exact duplicate group in the Local image library."""
    review = CatalogSimilarityService(_database()).duplicate_review()
    return render_template(
        "manage.html", management="duplicates",
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
