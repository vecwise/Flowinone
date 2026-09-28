"""HTML, JSON, and CLI surfaces for the cross-source Catalog."""

from __future__ import annotations

import json
import os
import platform
import secrets
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

import click
from flask import Blueprint, Flask, abort, current_app, redirect, render_template, request, url_for

from config import DB_route_external, DB_route_internal
from src.flowinone.resource_library.database import get_resource_database
from src.flowinone.resource_library.jobs import JobQueue
from src.flowinone.resource_library.canonical import hash_text
from src.file_handler.eagle_integration import is_eagle_available

from .service import CATALOG_SOURCES, CatalogQuery, CatalogService, CatalogSyncService
from .watch import CatalogSourceWatcher
from .discovery import DiscoveryService
from .artifacts import CatalogArtifactService, PersonService
from .similarity import CatalogSimilarityService
from src.flowinone.web.api import api_error, parse_json, parse_query, validated_json
from src.flowinone.web.schemas import (
    CatalogEventRequest,
    CatalogFacetsOutput,
    CatalogItemOutput,
    CatalogItemsOutput,
    CatalogListOutput,
    CatalogListQuery,
    CatalogSessionOutput,
    CatalogSessionRequest,
    CatalogSessionsOutput,
    SavedCatalogSearchDeleteOutput,
    SavedCatalogSearchesOutput,
    SavedCatalogSearchOutput,
    SavedCatalogSearchRequest,
    SavedSearchLimitQuery,
    CatalogSyncOutput,
    CatalogSyncJobEnvelope,
    CatalogSyncRequest,
    CatalogSyncStatusOutput,
    CatalogSimilarityJobEnvelope,
    CatalogSimilarityRebuildRequest,
    CatalogSimilarityStatusOutput,
    DuplicateCanonicalSelectionOutput,
    DuplicateCanonicalSelectionRequest,
    DuplicateRevealOutput,
    DuplicateReviewOutput,
    CatalogWatchSettingsRequest,
    CatalogWatchStatusOutput,
    PersonLinkRequest,
    PersonNameRequest,
    PersonOutput,
    RelationsRebuildOutput,
    RelatedLimitQuery,
    SimilarImageQuery,
    SimilarImagesOutput,
    SessionLimitQuery,
)
from src.flowinone.web.common import path_is_within_roots


bp = Blueprint("catalog", __name__)

NAVIGATOR_SCOPE_SOURCES = {
    "gallery": tuple(source for source in CATALOG_SOURCES if source != "resources"),
    "all": CATALOG_SOURCES,
}
SOURCE_LABELS = {
    "local": "本機",
    "eagle": "EAGLE",
    "bookmarks": "書籤",
    "resources": "Resources",
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


def _database():
    configured = current_app.config.get("FLOWINONE_RESOURCE_DB_PATH")
    return get_resource_database(
        Path(configured) if configured else None,
        migrate=bool(
            current_app.config.get("FLOWINONE_AUTO_MIGRATE", current_app.testing)
        ),
    )


def _duplicate_review_roots() -> list[str]:
    """Return the same configured Local roots used by media serving."""
    configured = current_app.config.get("FLOWINONE_MEDIA_ROOTS", ())
    if isinstance(configured, str):
        configured = configured.split(os.pathsep)
    return [
        str(Path(value).expanduser().resolve())
        for value in (DB_route_external, DB_route_internal, *configured)
        if value
    ]


def _reveal_duplicate_path(path: Path) -> None:
    """Ask the operating system to reveal one validated Local source file."""
    if platform.system() == "Darwin":
        subprocess.Popen(["open", "-R", str(path)])
    elif platform.system() == "Windows":
        subprocess.Popen(["explorer", "/select,", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])


def _query(scope: str | None = None) -> CatalogQuery:
    return CatalogQuery.create(
        q=request.args.get("q"), scope=scope or request.args.get("scope"),
        sources=request.args.getlist("source"), item_type=request.args.get("type"),
        tags=request.args.getlist("tags") or request.args.get("tags"), tag_mode=request.args.get("tag_mode"),
        favorite=request.args.get("favorite"), unviewed=request.args.get("unviewed"),
        duration_min=request.args.get("duration_min"), duration_max=request.args.get("duration_max"),
        added_from=request.args.get("added_from"), added_to=request.args.get("added_to"),
        sort=request.args.get("sort"), seed=request.args.get("seed"), limit=request.args.get("limit"), cursor=request.args.get("cursor"),
    )


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


def _visible_items(service: CatalogService, payload: dict, query: CatalogQuery) -> list[dict]:
    """Decorate canonical rows with a source-specific, usable launch target."""
    visible_items = []
    origins_by_item = service.get_origins_for_items(
        item["id"] for item in payload["items"]
    )
    for item in payload["items"]:
        available = origins_by_item.get(item["id"], {})
        origins = []
        for source in query.sources:
            origin = available.get(source)
            if origin:
                origins.append(origin)
        if not origins:
            for source in item.get("sources") or []:
                origin = available.get(source)
                if origin:
                    origins.append(origin)
        chosen = origins[0] if origins else None
        if not chosen:
            continue
        metadata = chosen.get("metadata") or {}
        source = chosen["source_kind"]
        item["launch_source"] = source
        item["launch_source_label"] = SOURCE_LABELS[source]
        item["launch_uri"] = (
            chosen.get("original_url") or chosen.get("detail_uri")
            if source == "bookmarks"
            else chosen.get("detail_uri")
        )
        item["thumbnail_ref"] = metadata.get("thumbnail_ref") or item.get("thumbnail_ref")
        item["target_blank"] = source == "bookmarks"
        visible_items.append(item)
    return visible_items


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
    payload["items"] = _visible_items(service, payload, effective_query)
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


@bp.get("/api/catalog/items")
def api_items():
    try:
        return validated_json(
            CatalogService(_database()).list(_validated_api_query()),
            CatalogListOutput,
        )
    except ValueError as exc:
        return api_error(str(exc), 400)


@bp.get("/api/catalog/items/<item_id>")
def api_item(item_id: str):
    try:
        return validated_json(
            CatalogService(_database()).get(item_id), CatalogItemOutput
        )
    except LookupError:
        return api_error("Catalog item not found", 404)


@bp.get("/api/catalog/facets")
def api_facets():
    return validated_json(
        CatalogService(_database()).facets(_validated_api_query()),
        CatalogFacetsOutput,
    )


def _validated_api_query() -> CatalogQuery:
    query = parse_query(CatalogListQuery, list_fields=("source", "tags"))
    values = query.model_dump()
    values["sources"] = values.pop("source")
    values["item_type"] = values.pop("type")
    return CatalogQuery.create(**values)


@bp.post("/api/catalog/items/<item_id>/events")
def api_event(item_id: str):
    payload = parse_json(CatalogEventRequest)
    try:
        return validated_json(
            CatalogService(_database()).record_event(
                item_id,
                payload.event_type,
                value=payload.event_value,
                session_id=payload.session_id,
                metadata=payload.metadata,
            ),
            CatalogItemOutput,
        )
    except LookupError:
        return api_error("Catalog item not found", 404)
    except ValueError as exc:
        return api_error(str(exc), 400)


@bp.get("/api/catalog/items/<item_id>/related")
def api_related(item_id: str):
    query = parse_query(RelatedLimitQuery)
    return validated_json(
        {
            "items": DiscoveryService(_database()).related_items(
                item_id, limit=query.limit
            )
        },
        CatalogItemsOutput,
    )


@bp.get("/api/catalog/items/<item_id>/similar-images")
def api_similar_images(item_id: str):
    query = parse_query(SimilarImageQuery)
    service = CatalogService(_database())
    try:
        payload = CatalogSimilarityService(_database()).similar_images(
            item_id, limit=query.limit, max_distance=query.max_distance
        )
    except LookupError:
        return api_error("Catalog item not found", 404)
    payload["items"] = _visible_items(
        service,
        payload,
        CatalogQuery.create(scope="all", sources=CATALOG_SOURCES),
    )
    return validated_json(payload, SimilarImagesOutput)


@bp.get("/api/catalog/duplicate-review")
def api_duplicate_review():
    return validated_json(
        CatalogSimilarityService(_database()).duplicate_review(),
        DuplicateReviewOutput,
    )


@bp.post("/api/catalog/duplicate-review/canonical")
def api_choose_duplicate_canonical():
    payload = parse_json(DuplicateCanonicalSelectionRequest)
    try:
        selected = CatalogSimilarityService(_database()).choose_duplicate_canonical(
            payload.content_hash, payload.canonical_item_id
        )
    except LookupError:
        return api_error("Duplicate group not found", 404)
    except ValueError as exc:
        return api_error(str(exc), 400)
    return validated_json(selected, DuplicateCanonicalSelectionOutput)


@bp.post("/api/catalog/duplicate-review/items/<item_id>/reveal")
def api_reveal_duplicate_item(item_id: str):
    try:
        path = CatalogSimilarityService(_database()).duplicate_review_path(item_id)
    except LookupError:
        return api_error("Duplicate review item not found", 404)
    except FileNotFoundError:
        return api_error("Local source file not found", 404)
    try:
        path = path.resolve(strict=True)
    except OSError:
        return api_error("Local source file not found", 404)
    if not path.is_file() or not path_is_within_roots(
        str(path), _duplicate_review_roots()
    ):
        return api_error("Local source path is outside configured media roots", 403)
    try:
        _reveal_duplicate_path(path)
    except OSError as exc:
        current_app.logger.warning("Could not reveal duplicate item %s: %s", item_id, exc)
        return api_error("Could not reveal Local source file", 500)
    return validated_json(
        {"item_id": item_id, "revealed": True}, DuplicateRevealOutput
    )


@bp.post("/api/catalog/relations/rebuild")
def api_rebuild_relations():
    return validated_json(
        DiscoveryService(_database()).rebuild_item_relations(),
        RelationsRebuildOutput,
    )


@bp.get("/api/catalog/sessions")
def api_sessions():
    query = parse_query(SessionLimitQuery)
    return validated_json(
        {
            "items": CatalogService(_database()).recent_sessions(
                query.limit
            )
        },
        CatalogSessionsOutput,
    )


@bp.post("/api/catalog/sessions")
def api_session_create():
    payload = _session_payload(parse_json(CatalogSessionRequest))
    return validated_json(
        CatalogService(_database()).save_session(payload), CatalogSessionOutput, 201
    )


@bp.patch("/api/catalog/sessions/<session_id>")
def api_session_update(session_id: str):
    payload = _session_payload(parse_json(CatalogSessionRequest))
    try:
        return validated_json(
            CatalogService(_database()).save_session(payload, session_id),
            CatalogSessionOutput,
        )
    except LookupError:
        return api_error("session_not_found", 404)


def _session_payload(payload: CatalogSessionRequest) -> dict:
    values = payload.model_dump(exclude_none=True)
    query = values["query"]
    if query.get("type") and not query.get("item_type"):
        query["item_type"] = query["type"]
    query.pop("type", None)
    query.pop("view", None)
    return values


def _saved_search_payload(payload: SavedCatalogSearchRequest) -> dict:
    query = payload.query.model_dump()
    if query.get("type") and not query.get("item_type"):
        query["item_type"] = query["type"]
    query.pop("type", None)
    query.pop("view", None)
    return {
        "label": payload.label,
        "query": query,
        "pinned": payload.pinned,
    }


@bp.get("/api/catalog/saved-searches")
def api_saved_searches():
    query = parse_query(SavedSearchLimitQuery)
    return validated_json(
        {"items": CatalogService(_database()).list_saved_searches(query.limit)},
        SavedCatalogSearchesOutput,
    )


@bp.post("/api/catalog/saved-searches")
def api_saved_search_create():
    payload = _saved_search_payload(parse_json(SavedCatalogSearchRequest))
    try:
        saved = CatalogService(_database()).save_search(**payload)
    except ValueError as exc:
        return api_error(str(exc), 400)
    return validated_json(saved, SavedCatalogSearchOutput, 201)


@bp.delete("/api/catalog/saved-searches/<saved_search_id>")
def api_saved_search_delete(saved_search_id: str):
    try:
        CatalogService(_database()).delete_saved_search(saved_search_id)
    except LookupError:
        return api_error("saved_search_not_found", 404)
    return validated_json({"id": saved_search_id}, SavedCatalogSearchDeleteOutput)


@bp.post("/api/people")
def api_person_create():
    payload = parse_json(PersonNameRequest)
    return validated_json(
        PersonService(_database()).create(payload.display_name), PersonOutput, 201
    )


@bp.patch("/api/people/<person_id>")
def api_person_rename(person_id: str):
    payload = parse_json(PersonNameRequest)
    try:
        return validated_json(
            PersonService(_database()).rename(person_id, payload.display_name),
            PersonOutput,
        )
    except LookupError:
        return api_error("person_not_found", 404)
    except ValueError as exc:
        return api_error(str(exc), 400)


@bp.post("/api/people/<person_id>/items/<item_id>")
def api_person_link(person_id: str, item_id: str):
    payload = parse_json(PersonLinkRequest)
    try:
        PersonService(_database()).link(
            person_id, item_id, confidence=payload.confidence, source="user"
        )
        return "", 204
    except Exception as exc:
        return api_error(str(exc), 400)


@bp.post("/api/catalog/sync")
def api_sync():
    payload = parse_json(CatalogSyncRequest)
    if not current_app.config.get("FLOWINONE_CATALOG_SYNC_INLINE", False):
        selected_sources = [
            source for source in CATALOG_SOURCES if source in payload.sources
        ]
        job_payload = {
            "sources": selected_sources,
            "full_rescan": payload.full_rescan,
        }
        job = JobQueue(_database()).queue(
            "catalog_sync",
            resource_id=None,
            payload=job_payload,
            input_hash=hash_text(
                json.dumps(job_payload, ensure_ascii=False, sort_keys=True)
            )[:16],
            priority=5,
            force=True,
        )
        return validated_json({"job": job}, CatalogSyncJobEnvelope, 202)
    return validated_json(
        CatalogSyncService(_database()).sync(
            payload.sources, full_rescan=payload.full_rescan
        ),
        CatalogSyncOutput,
    )


@bp.get("/api/catalog/sync/jobs/<job_id>")
def api_sync_job(job_id: str):
    job = JobQueue(_database()).get(job_id)
    if job is None or job.get("job_type") != "catalog_sync":
        return api_error("catalog_sync_job_not_found", 404)
    return validated_json({"job": job}, CatalogSyncJobEnvelope)


@bp.get("/api/catalog/sync/status")
def api_sync_status():
    """Expose per-source live retry progress to Navigator polling."""
    return validated_json(
        {"sources": CatalogService(_database()).sync_status()},
        CatalogSyncStatusOutput,
    )


@bp.get("/api/catalog/watch")
def api_catalog_watch_status():
    return validated_json(
        CatalogSourceWatcher(_database()).status(),
        CatalogWatchStatusOutput,
    )


@bp.post("/api/catalog/watch")
def api_catalog_watch_update():
    payload = parse_json(CatalogWatchSettingsRequest)
    return validated_json(
        CatalogSourceWatcher(_database()).set_enabled(payload.enabled),
        CatalogWatchStatusOutput,
    )


@bp.get("/api/catalog/similarity/status")
def api_catalog_similarity_status():
    return validated_json(
        CatalogSimilarityService(_database()).status(),
        CatalogSimilarityStatusOutput,
    )


@bp.post("/api/catalog/similarity/rebuild")
def api_catalog_similarity_rebuild():
    payload = parse_json(CatalogSimilarityRebuildRequest)
    job_payload = {"limit": payload.limit, "force": payload.force}
    job = JobQueue(_database()).queue(
        "catalog_similarity",
        resource_id=None,
        payload=job_payload,
        input_hash=hash_text(
            json.dumps(job_payload, ensure_ascii=False, sort_keys=True)
        )[:16],
        priority=25,
        force=True,
    )
    return validated_json({"job": job}, CatalogSimilarityJobEnvelope, 202)


def register_catalog(app: Flask) -> None:
    app.config.setdefault(
        "FLOWINONE_CATALOG_SYNC_INLINE", bool(app.config.get("TESTING"))
    )
    app.register_blueprint(bp)

    @app.cli.command("catalog-sync")
    @click.option("--source", "sources", multiple=True, type=click.Choice((*CATALOG_SOURCES, "all")), default=("all",))
    @click.option(
        "--full-rescan",
        is_flag=True,
        help="Discard any Eagle checkpoint and rebuild every Eagle projection.",
    )
    def catalog_sync(sources: tuple[str, ...], full_rescan: bool) -> None:
        selected = CATALOG_SOURCES if "all" in sources else sources
        click.echo(
            CatalogSyncService(_database()).sync(
                selected, full_rescan=full_rescan
            )
        )

    @app.cli.command("catalog-relations-rebuild")
    def catalog_relations_rebuild() -> None:
        click.echo(DiscoveryService(_database()).rebuild_item_relations())

    @app.cli.command("catalog-ocr")
    @click.argument("item_id")
    @click.argument("path", type=click.Path(path_type=Path, exists=True, dir_okay=False))
    def catalog_ocr(item_id: str, path: Path) -> None:
        click.echo(CatalogArtifactService(_database()).run_ocr(item_id, path))

    @app.cli.command("catalog-similarity-rebuild")
    @click.option("--force", is_flag=True, help="重新計算所有本機圖片，而不只變更過的檔案。")
    @click.option("--limit", default=20_000, type=click.IntRange(1, 50_000), show_default=True)
    def catalog_similarity_rebuild(force: bool, limit: int) -> None:
        click.echo(
            CatalogSimilarityService(_database()).analyze_local_images(
                limit=limit, force=force
            )
        )


__all__ = ["bp", "register_catalog"]
