"""HTML, JSON, and CLI surfaces for the cross-source Catalog."""

from __future__ import annotations

import secrets
from pathlib import Path
from urllib.parse import urlencode

import click
from flask import Blueprint, Flask, abort, current_app, jsonify, redirect, render_template, request, url_for

from src.flowinone.resource_library.database import get_resource_database
from src.file_handler.eagle_integration import is_eagle_available

from .service import CATALOG_SOURCES, CatalogQuery, CatalogService, CatalogSyncService
from .discovery import DiscoveryService
from .artifacts import CatalogArtifactService, PersonService


bp = Blueprint("catalog", __name__)


def _database():
    configured = current_app.config.get("FLOWINONE_RESOURCE_DB_PATH")
    return get_resource_database(Path(configured) if configured else None)


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


@bp.get("/search/")
def search_page():
    service = CatalogService(_database())
    query = _query()
    if query.sort == "random" and not query.seed:
        args = request.args.to_dict(flat=False)
        args["seed"] = [str(secrets.randbelow(2_147_483_646) + 1)]
        return redirect(f"{url_for('catalog.search_page')}?{urlencode(args, doseq=True)}")
    if service.count() == 0:
        CatalogSyncService(_database()).sync(("resources", "bookmarks", "local"))
    try:
        payload = service.list(query)
    except ValueError as exc:
        abort(400, description=str(exc))
    eagle_available = is_eagle_available() if "eagle" in query.sources else True
    visible_items = []
    for item in payload["items"]:
        origins = []
        for source in query.sources:
            origin = service.get_origin(item["id"], source)
            if origin:
                origins.append(origin)
        if not origins:
            for source in item.get("sources") or []:
                origin = service.get_origin(item["id"], source)
                if origin:
                    origins.append(origin)
        chosen = next((origin for origin in origins if origin["source_kind"] != "eagle" or eagle_available), None)
        if chosen:
            metadata = chosen.get("metadata") or {}
            item["launch_source"] = chosen["source_kind"]
            item["launch_uri"] = (chosen.get("original_url") or chosen.get("detail_uri")) if chosen["source_kind"] == "bookmarks" else chosen.get("detail_uri")
            item["thumbnail_ref"] = metadata.get("thumbnail_ref") or item.get("thumbnail_ref")
        else:
            # Do not render a dead link for an Eagle-only item while Eagle is
            # offline. Mixed-origin items remain visible through their other
            # origin.
            continue
        visible_items.append(item)
    payload["items"] = visible_items
    if not payload["next_cursor"]:
        payload["total_estimate"] = len(visible_items)
    next_url = None
    if payload["next_cursor"]:
        args = request.args.to_dict(flat=False)
        args["cursor"] = [payload["next_cursor"]]
        next_url = f"{url_for('catalog.search_page')}?{urlencode(args, doseq=True)}"
    return render_template("catalog_search.html", title="Search · Flowinone", payload=payload, query=query, sources=CATALOG_SOURCES, next_url=next_url, eagle_available=eagle_available)


@bp.get("/api/catalog/items")
def api_items():
    try:
        return jsonify(CatalogService(_database()).list(_query()))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@bp.get("/api/catalog/items/<item_id>")
def api_item(item_id: str):
    try:
        return jsonify(CatalogService(_database()).get(item_id))
    except LookupError:
        return jsonify({"error": "Catalog item not found"}), 404


@bp.get("/api/catalog/facets")
def api_facets():
    return jsonify(CatalogService(_database()).facets(_query()))


@bp.post("/api/catalog/items/<item_id>/events")
def api_event(item_id: str):
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify(CatalogService(_database()).record_event(item_id, str(payload.get("event_type") or ""), value=payload.get("event_value"), session_id=payload.get("session_id"), metadata=payload.get("metadata")))
    except LookupError:
        return jsonify({"error": "Catalog item not found"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@bp.get("/api/catalog/items/<item_id>/related")
def api_related(item_id: str):
    return jsonify({"items": DiscoveryService(_database()).related_items(item_id, limit=request.args.get("limit", 18, type=int))})


@bp.post("/api/catalog/relations/rebuild")
def api_rebuild_relations():
    return jsonify(DiscoveryService(_database()).rebuild_item_relations())


@bp.post("/api/catalog/collections/generate")
def api_generate_collections():
    payload = request.get_json(silent=True) or {}
    service = DiscoveryService(_database())
    if payload.get("rebuild", True):
        service.rebuild_item_relations()
    collections = service.generate_collections(limit=max(1, min(int(payload.get("limit") or 20), 100)))
    return jsonify({"collections": collections}), 201


@bp.get("/api/catalog/sessions")
def api_sessions():
    return jsonify({"items": CatalogService(_database()).recent_sessions(request.args.get("limit", 3, type=int))})


@bp.post("/api/catalog/sessions")
def api_session_create():
    return jsonify(CatalogService(_database()).save_session(request.get_json(silent=True) or {})), 201


@bp.patch("/api/catalog/sessions/<session_id>")
def api_session_update(session_id: str):
    try:
        return jsonify(CatalogService(_database()).save_session(request.get_json(silent=True) or {}, session_id))
    except LookupError:
        return jsonify({"error": "session_not_found"}), 404


@bp.post("/api/people")
def api_person_create():
    payload = request.get_json(silent=True) or {}
    return jsonify(PersonService(_database()).create(str(payload.get("display_name") or ""))), 201


@bp.patch("/api/people/<person_id>")
def api_person_rename(person_id: str):
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify(PersonService(_database()).rename(person_id, str(payload.get("display_name") or "")))
    except LookupError:
        return jsonify({"error": "person_not_found"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@bp.post("/api/people/<person_id>/items/<item_id>")
def api_person_link(person_id: str, item_id: str):
    payload = request.get_json(silent=True) or {}
    try:
        PersonService(_database()).link(person_id, item_id, confidence=payload.get("confidence"), source="user")
        return "", 204
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@bp.post("/api/catalog/sync")
def api_sync():
    payload = request.get_json(silent=True) or {}
    sources = payload.get("sources") or CATALOG_SOURCES
    return jsonify(CatalogSyncService(_database()).sync(sources))


def register_catalog(app: Flask) -> None:
    app.register_blueprint(bp)

    @app.cli.command("catalog-sync")
    @click.option("--source", "sources", multiple=True, type=click.Choice((*CATALOG_SOURCES, "all")), default=("all",))
    def catalog_sync(sources: tuple[str, ...]) -> None:
        selected = CATALOG_SOURCES if "all" in sources else sources
        click.echo(CatalogSyncService(_database()).sync(selected))

    @app.cli.command("catalog-relations-rebuild")
    def catalog_relations_rebuild() -> None:
        click.echo(DiscoveryService(_database()).rebuild_item_relations())

    @app.cli.command("catalog-collections-generate")
    @click.option("--limit", type=click.IntRange(1, 100), default=20)
    def catalog_collections_generate(limit: int) -> None:
        service = DiscoveryService(_database())
        service.rebuild_item_relations()
        click.echo({"collections": len(service.generate_collections(limit=limit))})

    @app.cli.command("catalog-ocr")
    @click.argument("item_id")
    @click.argument("path", type=click.Path(path_type=Path, exists=True, dir_okay=False))
    def catalog_ocr(item_id: str, path: Path) -> None:
        click.echo(CatalogArtifactService(_database()).run_ocr(item_id, path))


__all__ = ["bp", "register_catalog"]
