"""Flask UI, JSON API, and CLI integration for the Resource Library."""

from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path
from typing import Optional

import click
from flask import (
    Blueprint,
    Flask,
    abort,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from config import CHROME_BOOKMARK_PATH
from src.file_handler.thumbnails.store import get_thumbnail_store
from src.flowinone.entry_system.models import ENTRY_MODES
from src.flowinone.entry_system.service import EntryService
from src.flowinone.knowledge_os.service import KnowledgeOSService

from .canonical import ensure_within
from .curation import (
    CollectionNotFound,
    CollectionService,
    DraftNoteNotFound,
    DraftNoteService,
)
from .database import get_resource_database, upgrade_database
from .enrichment import EnrichmentService
from .jobs import JobQueue
from .maintenance import export_resource_mirrors, rebuild_fts, retry_failed_jobs
from .obsidian import ObsidianConflict, ObsidianExporter, ObsidianNotConfigured
from .repository import ResourceNotFound, ResourceRepository
from .service import ResourceService
from .settings import get_resource_settings
from .worker import ResourceWorker


bp = Blueprint("resource_library", __name__)


def _database():
    configured = current_app.config.get("FLOWINONE_RESOURCE_DB_PATH")
    return get_resource_database(Path(configured) if configured else None)


def _services():
    database = _database()
    return (
        ResourceService(
            database,
            link_thumbnail_cache=bool(
                current_app.config.get("FLOWINONE_RESOURCE_LINK_THUMBNAILS", True)
            ),
        ),
        CollectionService(database),
        DraftNoteService(database),
        ObsidianExporter(database),
    )


def _knowledge() -> KnowledgeOSService:
    return KnowledgeOSService(_database())


def _list_params(payload=None) -> dict:
    values = payload or request.args

    def multi(name: str) -> tuple[str, ...]:
        if hasattr(values, "getlist"):
            result = values.getlist(name)
        else:
            raw = values.get(name, []) if isinstance(values, dict) else []
            result = raw if isinstance(raw, list) else str(raw).split(",")
        return tuple(value.strip() for value in result if str(value).strip() and value != "all")

    disposition_values = multi("disposition")
    if (values.get("disposition") if hasattr(values, "get") else None) == "all":
        disposition_values = ("active", "archived", "rejected")
    min_priority = values.get("min_priority") if hasattr(values, "get") else None
    try:
        min_priority_value = int(min_priority) if str(min_priority or "").strip() else None
    except (TypeError, ValueError):
        min_priority_value = None
    try:
        page = int(values.get("page", 1))
        per_page = int(values.get("per_page", 30))
    except (TypeError, ValueError):
        page, per_page = 1, 30
    return {
        "query": str(values.get("q") or values.get("query") or "").strip(),
        "reading_states": multi("reading_state"),
        "dispositions": disposition_values,
        "source_types": multi("source_type"),
        "tag": str(values.get("tag") or "").strip(),
        "domain": str(values.get("domain") or "").strip(),
        "min_priority": min_priority_value,
        "page": page,
        "per_page": per_page,
    }


def _thumbnail_url(resource: dict) -> str:
    if resource.get("thumbnail_path"):
        return url_for("resource_library.resource_asset", resource_id=resource["id"], kind="thumbnail")
    if resource.get("thumbnail_media_id"):
        media_id = resource["thumbnail_media_id"]
        if get_thumbnail_store().get_thumbnail_path(media_id):
            return url_for("serve_bookmark_thumbnail", media_id=media_id)
    return url_for("static", filename="default_thumbnail.svg")


def decorate_resource(resource: dict) -> dict:
    decorated = dict(resource)
    decorated["thumbnail_url"] = _thumbnail_url(resource)
    decorated["detail_url"] = url_for(
        "resource_library.resource_detail", resource_id=resource["id"]
    )
    return decorated


def decorate_collection(collection: dict, resources: ResourceRepository) -> dict:
    """Resolve typed Resource refs for display while leaving stored snapshots portable."""
    decorated = dict(collection)
    decorated_items = []
    for item in collection.get("items") or []:
        rendered = dict(item)
        if item.get("source_kind") == "resource":
            try:
                resource = decorate_resource(resources.get(item["source_id"]))
                rendered["title"] = resource["title"]
                rendered["url"] = resource["detail_url"]
                rendered["thumbnail"] = resource["thumbnail_url"]
            except ResourceNotFound:
                pass
        decorated_items.append(rendered)
    decorated["items"] = decorated_items
    return decorated


def _form_error(endpoint: str, error: Exception, **values):
    return redirect(url_for(endpoint, error=str(error), **values))


@bp.get("/resources/")
def resource_index():
    service, collections, notes, _ = _services()
    params = _list_params()
    page = service.repository.list(**params)
    items = [decorate_resource(item) for item in page.items]
    snapshot_filters = []
    for field, value in (
        ("reading_state", next(iter(params["reading_states"]), "")),
        ("disposition", next(iter(params["dispositions"]), "active")),
        ("source_type", next(iter(params["source_types"]), "")),
        ("tag", params["tag"]),
        ("domain", params["domain"]),
        ("min_priority", params["min_priority"]),
    ):
        if value not in (None, "", "active"):
            snapshot_filters.append({"field": field, "op": "eq", "value": value})
    snapshot_query = {
        "q": params["query"],
        "reading_state": next(iter(params["reading_states"]), ""),
        "source_type": next(iter(params["source_types"]), ""),
        "disposition": next(iter(params["dispositions"]), "active"),
        "tag": params["tag"],
        "domain": params["domain"],
    }
    snapshot_target = url_for(
        "resource_library.resource_index",
        **{key: value for key, value in snapshot_query.items() if value not in (None, "")},
    )
    snapshot_state = {
        "scope": {"sources": ["resource_library"]},
        "query": {"text": params["query"]},
        "filters": snapshot_filters,
        "sort": [{"field": "priority", "order": "desc"}, {"field": "captured_at", "order": "desc"}],
        "layout": {"mode": "grid", "density": "comfortable", "inspector_open": False},
        "focus": {"item_id": None},
    }
    return render_template(
        "resource_library.html",
        title="Resource Flow · Flowinone",
        resources=items,
        page=page,
        page_count=max(1, math.ceil(page.total / page.per_page)),
        stats=service.repository.stats(),
        filters=params,
        snapshot_target_uri=snapshot_target,
        snapshot_state_json=json.dumps(snapshot_state, ensure_ascii=False, separators=(",", ":")),
        snapshot_context_json=json.dumps(
            {"source": {"kind": "resource_view", "id": snapshot_target}},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        notice=request.args.get("notice"),
        error=request.args.get("error"),
    )


@bp.post("/resources/")
def resource_create():
    service, _, _, _ = _services()
    try:
        result = service.create_url(
            request.form.get("url", ""),
            title=request.form.get("title", ""),
            saved_reason=request.form.get("saved_reason", ""),
            enqueue=request.form.get("enqueue", "1") != "0",
        )
    except Exception as exc:
        return _form_error("resource_library.resource_index", exc)
    return redirect(
        url_for(
            "resource_library.resource_detail",
            resource_id=result["resource"]["id"],
            notice="資源已加入 Resource Flow",
        )
    )


@bp.post("/resources/import/chrome")
def resource_import_chrome():
    service, _, _, _ = _services()
    path = Path(current_app.config.get("CHROME_BOOKMARK_PATH", CHROME_BOOKMARK_PATH))
    try:
        result = service.import_file_if_changed(
            path,
            format_hint="json",
            enqueue=request.form.get("enqueue", "1") != "0",
        )
        if not result.get("changed"):
            message = "Chrome 書籤沒有變更"
        else:
            message = (
                f"同步完成：新增 {result.get('created', 0)}、"
                f"既有 {result.get('duplicates', 0)}、失敗 {result.get('failed', 0)}"
            )
    except Exception as exc:
        return _form_error("resource_library.resource_index", exc)
    return redirect(url_for("resource_library.resource_index", notice=message))


@bp.post("/resources/import/upload")
def resource_import_upload():
    service, _, _, _ = _services()
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return _form_error("resource_library.resource_index", ValueError("請選擇書籤檔案"))
    format_hint = request.form.get("format") or Path(upload.filename).suffix.lstrip(".")
    suffix = ".html" if format_hint.lower() in {"html", "htm"} else ".json"
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(prefix="flowinone-bookmarks-", suffix=suffix, delete=False) as tmp:
            temporary_path = Path(tmp.name)
            upload.save(tmp)
        summary = service.import_file(
            temporary_path,
            format_hint=format_hint,
            enqueue=request.form.get("enqueue", "1") != "0",
        )
        message = (
            f"匯入完成：新增 {summary.created}、既有 {summary.duplicates}、失敗 {summary.failed}"
        )
    except Exception as exc:
        return _form_error("resource_library.resource_index", exc)
    finally:
        if temporary_path:
            temporary_path.unlink(missing_ok=True)
    return redirect(url_for("resource_library.resource_index", notice=message))


@bp.get("/resources/<resource_id>/")
def resource_detail(resource_id: str):
    service, collections, _, _ = _services()
    try:
        resource = decorate_resource(service.repository.get(resource_id))
    except ResourceNotFound:
        abort(404)
    resource["extracted_text"] = service.repository.latest_content_text(resource_id)[:100_000]
    similar = [
        decorate_resource(item)
        for item in service.repository.find_similar(resource_id, limit=6)
    ]
    return render_template(
        "resource_detail.html",
        title=f"{resource['title']} · Flowinone",
        resource=resource,
        jobs=service.jobs.list_for_resource(resource_id),
        collections=collections.list(),
        similar_resources=similar,
        resource_context=_knowledge().resource_context(resource_id),
        context_modes=ENTRY_MODES,
        projects=EntryService(_database()).repository.list_projects(statuses=("active",)),
        ai_available=EnrichmentService(_database()).ai.available,
        notice=request.args.get("notice"),
        error=request.args.get("error"),
    )


@bp.post("/resources/<resource_id>/workflow")
def resource_workflow(resource_id: str):
    service, _, _, _ = _services()
    changes = {
        key: request.form[key]
        for key in ("reading_state", "disposition", "availability", "priority", "user_note", "saved_reason")
        if key in request.form
    }
    try:
        service.update_resource(resource_id, changes)
    except Exception as exc:
        return _form_error("resource_library.resource_detail", exc, resource_id=resource_id)
    return redirect(
        url_for("resource_library.resource_detail", resource_id=resource_id, notice="狀態已更新")
    )


@bp.post("/resources/<resource_id>/context")
def resource_context_update(resource_id: str):
    try:
        _knowledge().link_resource(
            resource_id,
            project_id=(request.form.get("project_id") or "").strip() or None,
            modes=request.form.getlist("mode"),
            relevance=request.form.get("relevance", 1.0),
            source="user",
        )
    except Exception as exc:
        return _form_error("resource_library.resource_detail", exc, resource_id=resource_id)
    return redirect(
        url_for(
            "resource_library.resource_detail",
            resource_id=resource_id,
            notice="Project / Mode 提取脈絡已更新",
        )
    )


@bp.post("/resources/<resource_id>/tags")
def resource_tags(resource_id: str):
    service, _, _, _ = _services()
    raw = request.form.get("tags", "")
    tags = [value.strip() for value in raw.replace("，", ",").split(",") if value.strip()]
    try:
        service.replace_tags(resource_id, tags)
    except Exception as exc:
        return _form_error("resource_library.resource_detail", exc, resource_id=resource_id)
    return redirect(
        url_for("resource_library.resource_detail", resource_id=resource_id, notice="Tags 已更新")
    )


@bp.post("/resources/<resource_id>/enrich")
def resource_enrich(resource_id: str):
    service, _, _, _ = _services()
    try:
        service.enqueue_enrichment(
            resource_id,
            include_ai=request.form.get("include_ai") == "1",
            force=request.form.get("force") == "1",
        )
    except Exception as exc:
        return _form_error("resource_library.resource_detail", exc, resource_id=resource_id)
    return redirect(
        url_for("resource_library.resource_detail", resource_id=resource_id, notice="已加入處理佇列")
    )


@bp.post("/resources/<resource_id>/collection")
def resource_add_collection(resource_id: str):
    _, collections, _, _ = _services()
    try:
        collections.add_item(
            request.form.get("collection_id", ""),
            source_kind="resource",
            source_id=resource_id,
            annotation=request.form.get("annotation", ""),
        )
    except Exception as exc:
        return _form_error("resource_library.resource_detail", exc, resource_id=resource_id)
    return redirect(
        url_for("resource_library.resource_detail", resource_id=resource_id, notice="已加入靈感集合")
    )


@bp.post("/resources/<resource_id>/promote")
def resource_promote(resource_id: str):
    _, _, notes, exporter = _services()
    try:
        note = notes.create_literature_note(resource_id)
        if request.form.get("export") == "1":
            exporter.export_note(note["id"])
    except Exception as exc:
        return _form_error("resource_library.resource_detail", exc, resource_id=resource_id)
    return redirect(
        url_for("resource_library.note_detail", note_id=note["id"], notice="Literature Note 已建立")
    )


@bp.post("/resources/<resource_id>/ask")
def resource_ask(resource_id: str):
    try:
        EnrichmentService(_database()).ask_question(
            resource_id,
            request.form.get("question", ""),
        )
    except Exception as exc:
        return _form_error("resource_library.resource_detail", exc, resource_id=resource_id)
    return redirect(
        url_for("resource_library.resource_detail", resource_id=resource_id, notice="Ask AI 已回答")
    )


@bp.get("/resources/<resource_id>/asset/<kind>")
def resource_asset(resource_id: str, kind: str):
    service, _, _, _ = _services()
    try:
        resource = service.repository.get(resource_id)
    except ResourceNotFound:
        abort(404)
    path_value = resource.get("thumbnail_path") if kind == "thumbnail" else resource.get("favicon_path")
    if not path_value:
        abort(404)
    try:
        path = ensure_within(get_resource_settings().data_dir, Path(path_value))
    except ValueError:
        abort(403)
    if not path.is_file():
        abort(404)
    return send_file(path, conditional=True, max_age=3600)


@bp.get("/inspiration/")
def collection_index():
    _, collections, _, _ = _services()
    return render_template(
        "resource_collections.html",
        title="靈感 Collections · Flowinone",
        collections=collections.list(),
        notice=request.args.get("notice"),
        error=request.args.get("error"),
    )


@bp.post("/inspiration/")
def collection_create():
    _, collections, _, _ = _services()
    try:
        collection = collections.create(
            request.form.get("title", ""),
            request.form.get("description", ""),
            request.form.get("kind", "inspiration"),
        )
    except Exception as exc:
        return _form_error("resource_library.collection_index", exc)
    return redirect(url_for("resource_library.collection_detail", collection_id=collection["id"]))


@bp.get("/inspiration/<collection_id>/")
def collection_detail(collection_id: str):
    service, collections, _, _ = _services()
    try:
        collection = decorate_collection(
            collections.get(collection_id),
            service.repository,
        )
    except CollectionNotFound:
        abort(404)
    return render_template(
        "resource_collection_detail.html",
        title=f"{collection['title']} · Flowinone",
        collection=collection,
        notice=request.args.get("notice"),
        error=request.args.get("error"),
    )


@bp.post("/inspiration/<collection_id>/items")
def collection_add_item(collection_id: str):
    _, collections, _, _ = _services()
    try:
        collections.add_item(
            collection_id,
            source_kind=request.form.get("source_kind", "resource"),
            source_id=request.form.get("source_id", ""),
            title=request.form.get("title", ""),
            url=request.form.get("url", ""),
            thumbnail=request.form.get("thumbnail", ""),
            annotation=request.form.get("annotation", ""),
        )
    except Exception as exc:
        return _form_error("resource_library.collection_detail", exc, collection_id=collection_id)
    return redirect(
        url_for("resource_library.collection_detail", collection_id=collection_id, notice="項目已加入")
    )


@bp.post("/inspiration/add-item")
def collection_add_item_from_source():
    """Accept a typed snapshot directly from Eagle/local media detail pages."""
    _, collections, _, _ = _services()
    collection_id = request.form.get("collection_id", "")
    try:
        collections.add_item(
            collection_id,
            source_kind=request.form.get("source_kind", "filesystem"),
            source_id=request.form.get("source_id", ""),
            title=request.form.get("title", ""),
            url=request.form.get("url", ""),
            thumbnail=request.form.get("thumbnail", ""),
            annotation=request.form.get("annotation", ""),
        )
    except Exception as exc:
        return _form_error("resource_library.collection_index", exc)
    return_to = request.form.get("return_to", "")
    if return_to.startswith("/") and not return_to.startswith("//"):
        separator = "&" if "?" in return_to else "?"
        return redirect(f"{return_to}{separator}notice=已加入靈感+Collection")
    return redirect(
        url_for(
            "resource_library.collection_detail",
            collection_id=collection_id,
            notice="項目已加入",
        )
    )


@bp.post("/inspiration/<collection_id>/synthesize")
def collection_synthesize(collection_id: str):
    _, collections, notes, _ = _services()
    try:
        note = notes.create_from_collection(
            collections.get(collection_id),
            request.form.get("title", ""),
        )
    except Exception as exc:
        return _form_error("resource_library.collection_detail", exc, collection_id=collection_id)
    return redirect(url_for("resource_library.note_detail", note_id=note["id"]))


@bp.get("/notes/")
def note_index():
    _, _, notes, _ = _services()
    return render_template(
        "resource_notes.html",
        title="整併筆記 · Flowinone",
        notes=notes.list(),
        notice=request.args.get("notice"),
        error=request.args.get("error"),
    )


@bp.post("/notes/")
def note_create():
    _, _, notes, _ = _services()
    try:
        note = notes.create(
            request.form.get("title", ""),
            request.form.get("body", ""),
            request.form.get("note_type", "synthesis"),
        )
    except Exception as exc:
        return _form_error("resource_library.note_index", exc)
    return redirect(url_for("resource_library.note_detail", note_id=note["id"]))


@bp.get("/notes/<note_id>/")
def note_detail(note_id: str):
    _, _, notes, _ = _services()
    try:
        note = notes.get(note_id)
    except DraftNoteNotFound:
        abort(404)
    return render_template(
        "resource_note_detail.html",
        title=f"{note['title']} · Flowinone",
        note=note,
        notice=request.args.get("notice"),
        error=request.args.get("error"),
    )


@bp.post("/notes/<note_id>/")
def note_update(note_id: str):
    _, _, notes, _ = _services()
    try:
        notes.update(
            note_id,
            {"title": request.form.get("title", ""), "body": request.form.get("body", "")},
        )
    except Exception as exc:
        return _form_error("resource_library.note_detail", exc, note_id=note_id)
    return redirect(url_for("resource_library.note_detail", note_id=note_id, notice="草稿已儲存"))


@bp.post("/notes/<note_id>/sources")
def note_add_source(note_id: str):
    _, _, notes, _ = _services()
    try:
        notes.add_source(
            note_id,
            source_kind=request.form.get("source_kind", "resource"),
            source_id=request.form.get("source_id", ""),
            citation_label=request.form.get("citation_label", ""),
            url=request.form.get("url", ""),
            annotation=request.form.get("annotation", ""),
        )
    except Exception as exc:
        return _form_error("resource_library.note_detail", exc, note_id=note_id)
    return redirect(url_for("resource_library.note_detail", note_id=note_id, notice="來源已加入"))


@bp.post("/notes/<note_id>/export")
def note_export(note_id: str):
    _, _, _, exporter = _services()
    try:
        result = exporter.export_note(
            note_id,
            overwrite=request.form.get("overwrite") == "1",
        )
    except (ObsidianNotConfigured, ObsidianConflict, ValueError, OSError) as exc:
        return _form_error("resource_library.note_detail", exc, note_id=note_id)
    return redirect(
        url_for(
            "resource_library.note_detail",
            note_id=note_id,
            notice=f"已輸出到 {result['path']}",
        )
    )


# JSON API
@bp.get("/api/resources")
def api_resources_list():
    service, _, _, _ = _services()
    page = service.repository.list(**_list_params())
    return jsonify(
        {
            "items": [decorate_resource(item) for item in page.items],
            "total": page.total,
            "page": page.page,
            "per_page": page.per_page,
        }
    )


@bp.post("/api/resources")
def api_resources_create():
    service, _, _, _ = _services()
    payload = request.get_json(silent=True) or {}
    try:
        result = service.create_url(
            str(payload.get("url") or ""),
            title=str(payload.get("title") or ""),
            saved_reason=str(payload.get("saved_reason") or ""),
            enqueue=bool(payload.get("enqueue", True)),
        )
    except (ValueError, RuntimeError) as exc:
        return jsonify({"error": str(exc)}), 400
    result["resource"] = decorate_resource(result["resource"])
    return jsonify(result), 201 if result["import"]["created"] else 200


@bp.get("/api/resources/<resource_id>")
def api_resource_get(resource_id: str):
    service, _, _, _ = _services()
    try:
        return jsonify(decorate_resource(service.repository.get(resource_id)))
    except ResourceNotFound:
        return jsonify({"error": "not_found"}), 404


@bp.patch("/api/resources/<resource_id>")
def api_resource_patch(resource_id: str):
    service, _, _, _ = _services()
    try:
        resource = service.update_resource(resource_id, request.get_json(silent=True) or {})
    except ResourceNotFound:
        return jsonify({"error": "not_found"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(decorate_resource(resource))


@bp.post("/api/resources/<resource_id>/status")
def api_resource_status(resource_id: str):
    """Compatibility endpoint that maps the original single-state API to split dimensions."""
    service, _, _, _ = _services()
    payload = request.get_json(silent=True) or {}
    changes = {
        key: payload[key]
        for key in ("reading_state", "disposition", "availability")
        if key in payload
    }
    legacy = str(payload.get("status") or "")
    if legacy in {"inbox", "unread", "skimmed", "reading", "digested"}:
        changes["reading_state"] = legacy
    elif legacy in {"archived", "rejected"}:
        changes["disposition"] = legacy
    elif legacy == "dead_link":
        changes["availability"] = "dead"
    if not changes:
        return jsonify({"error": "status payload is empty or unsupported"}), 400
    try:
        return jsonify(service.update_resource(resource_id, changes))
    except ResourceNotFound:
        return jsonify({"error": "not_found"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@bp.delete("/api/resources/<resource_id>")
def api_resource_delete(resource_id: str):
    service, _, _, _ = _services()
    try:
        service.repository.delete(resource_id)
    except ResourceNotFound:
        return jsonify({"error": "not_found"}), 404
    return "", 204


@bp.post("/api/resources/<resource_id>/tags")
def api_resource_tags(resource_id: str):
    service, _, _, _ = _services()
    payload = request.get_json(silent=True) or {}
    tags = payload.get("tags") or []
    if not isinstance(tags, list):
        return jsonify({"error": "tags must be a list"}), 400
    try:
        return jsonify(service.replace_tags(resource_id, [str(tag) for tag in tags]))
    except ResourceNotFound:
        return jsonify({"error": "not_found"}), 404


@bp.delete("/api/resources/<resource_id>/tags/<tag_id>")
def api_resource_tag_delete(resource_id: str, tag_id: str):
    service, _, _, _ = _services()
    try:
        resource = service.repository.remove_tag(
            resource_id,
            tag_id,
            source=request.args.get("source", "user"),
        )
    except ResourceNotFound:
        return jsonify({"error": "not_found"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(resource)


@bp.post("/api/resources/<resource_id>/enrich")
def api_resource_enrich(resource_id: str):
    service, _, _, _ = _services()
    payload = request.get_json(silent=True) or {}
    try:
        jobs = service.enqueue_enrichment(
            resource_id,
            include_ai=bool(payload.get("include_ai")),
            force=bool(payload.get("force")),
        )
    except ResourceNotFound:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"jobs": jobs}), 202


@bp.post("/api/resources/<resource_id>/retry")
def api_resource_retry(resource_id: str):
    service, _, _, _ = _services()
    payload = request.get_json(silent=True) or {}
    try:
        jobs = service.enqueue_enrichment(
            resource_id,
            include_ai=bool(payload.get("include_ai")),
            force=True,
        )
    except ResourceNotFound:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"jobs": jobs}), 202


@bp.post("/api/resources/<resource_id>/promote")
def api_resource_promote(resource_id: str):
    _, _, notes, exporter = _services()
    payload = request.get_json(silent=True) or {}
    try:
        note = notes.create_literature_note(resource_id)
        export = exporter.export_note(note["id"]) if payload.get("export") else None
    except ResourceNotFound:
        return jsonify({"error": "not_found"}), 404
    except (ObsidianNotConfigured, ObsidianConflict, ValueError, OSError) as exc:
        return jsonify({"error": str(exc), "note": note if "note" in locals() else None}), 409
    return jsonify({"note": note, "export": export})


@bp.get("/api/resources/<resource_id>/similar")
def api_resource_similar(resource_id: str):
    service, _, _, _ = _services()
    try:
        items = service.repository.find_similar(resource_id, limit=request.args.get("limit", 8, type=int))
    except ResourceNotFound:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"items": [decorate_resource(item) for item in items]})


@bp.post("/api/resources/<resource_id>/ask")
def api_resource_ask(resource_id: str):
    payload = request.get_json(silent=True) or {}
    try:
        answer = EnrichmentService(_database()).ask_question(
            resource_id,
            str(payload.get("question") or ""),
        )
    except ResourceNotFound:
        return jsonify({"error": "not_found"}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(answer)


@bp.get("/api/resources/<resource_id>/context")
def api_resource_context_get(resource_id: str):
    try:
        return jsonify(_knowledge().resource_context(resource_id))
    except ResourceNotFound:
        return jsonify({"error": "not_found"}), 404


@bp.post("/api/resources/<resource_id>/context")
def api_resource_context_update(resource_id: str):
    payload = request.get_json(silent=True) or {}
    try:
        context = _knowledge().link_resource(
            resource_id,
            project_id=str(payload.get("project_id") or "").strip() or None,
            modes=payload.get("modes") or [],
            relevance=payload.get("relevance", 1.0),
            source=str(payload.get("source") or "user"),
        )
    except ResourceNotFound:
        return jsonify({"error": "not_found"}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(context)


@bp.post("/api/search")
def api_search():
    service, _, _, _ = _services()
    payload = request.get_json(silent=True) or {}
    page = service.repository.list(**_list_params(payload))
    return jsonify(
        {"items": [decorate_resource(item) for item in page.items], "total": page.total}
    )


@bp.post("/api/imports/chrome")
def api_import_chrome():
    service, _, _, _ = _services()
    upload = request.files.get("file")
    if upload is None:
        path = Path(current_app.config.get("CHROME_BOOKMARK_PATH", CHROME_BOOKMARK_PATH))
        try:
            summary = service.import_file(path, format_hint="json", enqueue=True)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(summary.to_dict())
    format_hint = request.form.get("format") or Path(upload.filename or "").suffix.lstrip(".")
    suffix = ".html" if format_hint.lower() in {"html", "htm"} else ".json"
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(prefix="flowinone-api-bookmarks-", suffix=suffix, delete=False) as tmp:
            temporary_path = Path(tmp.name)
            upload.save(tmp)
        summary = service.import_file(temporary_path, format_hint=format_hint, enqueue=True)
        return jsonify(summary.to_dict())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        if temporary_path:
            temporary_path.unlink(missing_ok=True)


def register_resource_library(app: Flask) -> None:
    """Register the blueprint, config defaults, and maintenance CLI commands."""
    settings = get_resource_settings()
    app.config.setdefault("FLOWINONE_RESOURCE_DB_PATH", str(settings.database_path))
    app.config.setdefault("CHROME_BOOKMARK_PATH", CHROME_BOOKMARK_PATH)
    app.config.setdefault("FLOWINONE_RESOURCE_LINK_THUMBNAILS", True)
    app.config.setdefault("MAX_CONTENT_LENGTH", settings.max_download_bytes)
    app.register_blueprint(bp)

    @app.cli.command("resources-sync")
    @click.option("--path", "path_value", type=click.Path(path_type=Path), default=None)
    @click.option("--format", "format_hint", type=click.Choice(["json", "html"]), default=None)
    @click.option("--no-enqueue", is_flag=True, help="Import without creating enrichment jobs.")
    def resources_sync(path_value, format_hint, no_enqueue):
        """Import a Chrome profile/export into the Resource Library."""
        service = ResourceService(_database())
        target = path_value or Path(current_app.config["CHROME_BOOKMARK_PATH"])
        summary = service.import_file(target, format_hint=format_hint, enqueue=not no_enqueue)
        click.echo(summary.to_dict())

    @app.cli.command("resources-worker")
    @click.option("--limit", type=click.IntRange(min=1), default=None)
    def resources_worker(limit):
        """Process ready Resource Library jobs and exit when idle."""
        completed = ResourceWorker(_database()).run_until_idle(max_jobs=limit)
        click.echo(f"processed={completed}")

    @app.cli.command("resources-db-upgrade")
    def resources_db_upgrade():
        """Apply all Resource Library database migrations."""
        upgrade_database(Path(current_app.config["FLOWINONE_RESOURCE_DB_PATH"]))
        click.echo("resource database is at head")

    @app.cli.command("resources-rebuild-fts")
    def resources_rebuild_fts():
        """Rebuild every denormalized FTS row from current data and content files."""
        click.echo(rebuild_fts(_database()))

    @app.cli.command("resources-retry-failed")
    def resources_retry_failed():
        """Reset failed resource jobs so the worker can retry them."""
        click.echo(retry_failed_jobs(_database()))

    @app.cli.command("resources-export-mirrors")
    def resources_export_mirrors():
        """Export read-only Markdown mirrors for backup and tool interoperability."""
        click.echo(export_resource_mirrors(_database()))


__all__ = ["bp", "decorate_resource", "register_resource_library"]
