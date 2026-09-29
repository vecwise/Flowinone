"""Catalog JSON endpoints; route names and response contracts stay stable."""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

from flask import abort, current_app, redirect, request, url_for

from src.file_handler import AccessDenied, ExternalServiceError, MediaNotFound, get_eagle_video_details, get_video_details

from src.flowinone.resource_library.canonical import hash_text
from src.flowinone.resource_library.jobs import JobQueue
from src.flowinone.web.api import api_error, parse_json, parse_query, validated_json
from src.flowinone.web.common import path_is_within_roots
from src.flowinone.web.schemas import (
    CatalogEventRequest, CatalogFacetsOutput, CatalogItemOutput, CatalogItemsOutput,
    CatalogListOutput, CatalogListQuery, CatalogSessionOutput, CatalogSessionRequest,
    CatalogSessionsOutput, SavedCatalogSearchDeleteOutput, SavedCatalogSearchesOutput,
    SavedCatalogSearchOutput, SavedCatalogSearchRequest, SavedSearchLimitQuery,
    CatalogSyncOutput, CatalogSyncJobEnvelope, CatalogSyncRequest, CatalogSyncStatusOutput,
    CatalogSimilarityJobEnvelope, CatalogSimilarityRebuildRequest, CatalogSimilarityStatusOutput,
    DuplicateCanonicalSelectionOutput, DuplicateCanonicalSelectionRequest, DuplicateRevealOutput,
    DuplicateReviewOutput, CatalogWatchSettingsRequest, CatalogWatchStatusOutput,
    PersonLinkRequest, PersonNameRequest, PersonOutput, RelationsRebuildOutput,
    RelatedLimitQuery, SimilarImageQuery, SimilarImagesOutput, SessionLimitQuery,
)

from . import http
from .artifacts import PersonService
from .browse import CatalogService
from .discovery import DiscoveryService
from .http import bp, _database
from .query import CATALOG_SOURCES, CatalogQuery
from .presentation import visible_items
from .similarity import CatalogSimilarityService
from .sync import CatalogSyncService
from .watch import CatalogSourceWatcher


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
        service = CatalogService(_database())
        item = service.get(item_id)
        visible = visible_items(service, {"items": [item]}, _preview_query())
        if visible and visible[0]["item_type"] == "video" and visible[0].get("launch_source") in {"local", "eagle"}:
            visible[0]["playback_uri"] = url_for("catalog.catalog_video_playback", item_id=item_id, source=visible[0]["launch_source"])
        return validated_json(
            visible[0] if visible else item, CatalogItemOutput
        )
    except LookupError:
        return api_error("Catalog item not found", 404)


@bp.get("/catalog/items/<item_id>/video")
def catalog_video_playback(item_id: str):
    """Resolve one Catalog video to its source-owned, range-capable stream."""
    source = request.args.get("source")
    if source not in {"local", "eagle"}:
        abort(404)
    service = CatalogService(_database())
    try:
        item = service.get(item_id)
    except LookupError:
        abort(404)
    if item["item_type"] != "video":
        abort(404)
    origin = service.get_origin(item_id, source)
    if not origin:
        abort(404)
    try:
        if source == "eagle":
            _, detail = get_eagle_video_details(origin["source_key"])
        else:
            path = origin.get("source_path") or ""
            selected = parse_qs(urlsplit(origin.get("detail_uri") or "").query).get("src", ["external"])[0]
            if selected not in {"internal", "external"}:
                abort(404)
            try:
                _, detail = get_video_details(path, selected)
            except MediaNotFound:
                _, detail = get_video_details(path, "external" if selected == "internal" else "internal")
    except (AccessDenied, MediaNotFound, ExternalServiceError):
        abort(404)
    return redirect(detail.source_url)


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
    query = parse_query(RelatedLimitQuery, list_fields=("source",))
    service = CatalogService(_database())
    try:
        related = DiscoveryService(_database()).related_items(item_id, limit=query.limit)
    except LookupError:
        return api_error("Catalog item not found", 404)
    return validated_json(
        {
            "items": visible_items(
                service,
                {"items": related},
                _preview_query(),
            )
        },
        CatalogItemsOutput,
    )


def _preview_query() -> CatalogQuery:
    requested = tuple(source for source in request.args.getlist("source") if source in CATALOG_SOURCES)
    return CatalogQuery.create(scope="all", sources=requested or CATALOG_SOURCES, folder=request.args.get("folder"))


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
    payload["items"] = visible_items(
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
        str(path), http._duplicate_review_roots()
    ):
        return api_error("Local source path is outside configured media roots", 403)
    try:
        http._reveal_duplicate_path(path)
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
