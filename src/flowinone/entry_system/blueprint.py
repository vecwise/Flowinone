"""Flask UI and JSON API for Flowinone's state-based Entry system."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click
from flask import Blueprint, Flask, abort, current_app, jsonify, redirect, render_template, request, url_for

from src.flowinone.knowledge_os.service import KnowledgeOSService
from src.flowinone.resource_library.database import get_resource_database, upgrade_database

from .models import ENTRY_MODES, ENTRY_STATUSES, PROJECT_STATUSES
from .repository import EntryNotFound, ProjectNotFound
from .service import EntryService, EntrySystemError


bp = Blueprint("entry_system", __name__)


MODE_COPY = {
    "build": {
        "eyebrow": "01 / BUILD",
        "title": "回到下一個有意義的行動",
        "description": "只顯示可繼續的工作、明確下一步與阻塞項目；不放內容 feed。",
        "create_label": "建立 BUILD Entry",
    },
    "think": {
        "eyebrow": "02 / THINK",
        "title": "把模糊問題變成可執行結構",
        "description": "先寫下問題、假設與下一步，再轉成 BUILD。這裡不推薦內容。",
        "create_label": "儲存 Think Entry",
    },
    "learn": {
        "eyebrow": "03 / LEARN",
        "title": "為明確問題而學習",
        "description": "每個入口都應有學習目標、來源範圍與可產生的輸出。",
        "create_label": "建立 LEARN Entry",
    },
    "scan": {
        "eyebrow": "04 / SCAN",
        "title": "有邊界地探索",
        "description": "先決定時間、數量或退出條件，再開始掃描。",
        "create_label": "建立 SCAN Entry",
    },
    "recover": {
        "eyebrow": "05 / RECOVER",
        "title": "清楚地退出工作狀態",
        "description": "這裡只保留恢復用入口，不混入未完成工作或提醒。",
        "create_label": "建立 RECOVER Entry",
    },
    "write": {
        "eyebrow": "06 / WRITE",
        "title": "把已理解的內容變成可交付資產",
        "description": "從 Brief、Wiki、Decision 或 Project 建立可追溯來源的 Output Asset。",
        "create_label": "建立 WRITE Entry",
    },
}


def _database():
    configured = current_app.config.get("FLOWINONE_RESOURCE_DB_PATH")
    return get_resource_database(Path(configured) if configured else None)


def _service() -> EntryService:
    return EntryService(_database())


def _knowledge() -> KnowledgeOSService:
    return KnowledgeOSService(_database())


def _form_data() -> dict[str, Any]:
    return {key: value for key, value in request.form.items()}


def _entry_form_data() -> dict[str, Any]:
    """Translate the small SCAN form into portable Entry context JSON."""
    data = _form_data()
    if str(data.get("mode") or "").strip().lower() == "scan":
        data["context"] = {
            "scan": {
                "time_budget_minutes": data.get("scan_minutes"),
                "item_limit": data.get("scan_limit"),
                "exit_condition": data.get("scan_exit_condition"),
            }
        }
    return data


def _safe_return_path(value: str | None, fallback_endpoint: str, **values: Any) -> str:
    candidate = (value or "").strip()
    if candidate.startswith("/") and not candidate.startswith("//") and "\\" not in candidate:
        return candidate
    return url_for(fallback_endpoint, **values)


def _form_error(endpoint: str, error: Exception, **values: Any):
    return redirect(url_for(endpoint, error=str(error), **values))


def _api_error(error: Exception):
    status = 404 if isinstance(error, (EntryNotFound, ProjectNotFound)) else 400
    return jsonify({"error": str(error)}), status


def decorate_entry(entry: dict[str, Any], service: EntryService) -> dict[str, Any]:
    decorated = dict(entry)
    decorated["detail_url"] = url_for("entry_system.entry_detail", entry_id=entry["id"])
    decorated["enter_url"] = url_for("entry_system.entry_enter", entry_id=entry["id"])
    decorated["destination"] = service.resolve_destination(entry)
    return decorated


def decorate_project(project: dict[str, Any]) -> dict[str, Any]:
    decorated = dict(project)
    decorated["detail_url"] = url_for("entry_system.project_detail", project_id=project["id"])
    return decorated


def decorate_retrieved_resource(resource: dict[str, Any]) -> dict[str, Any]:
    decorated = dict(resource)
    decorated["detail_url"] = url_for(
        "resource_library.resource_detail", resource_id=resource["id"]
    )
    return decorated


@bp.get("/build/", strict_slashes=False)
def build_dashboard():
    service = _service()
    dashboard = service.build_dashboard()
    dashboard["continue_entry"] = (
        decorate_entry(dashboard["continue_entry"], service)
        if dashboard["continue_entry"]
        else None
    )
    dashboard["recent_entries"] = [
        decorate_entry(entry, service) for entry in dashboard["recent_entries"]
    ]
    dashboard["blocked_entries"] = [
        decorate_entry(entry, service) for entry in dashboard["blocked_entries"]
    ]
    current_project_id = (dashboard.get("current_project") or {}).get("id")
    dashboard["related_resources"] = (
        [
            decorate_retrieved_resource(resource)
            for resource in _knowledge().retrieve(project_id=current_project_id, mode="build", limit=8)
        ]
        if current_project_id
        else []
    )
    dashboard["related_decisions"] = (
        _knowledge().list_decisions(current_project_id)[:6] if current_project_id else []
    )
    if dashboard.get("current_project"):
        dashboard["current_project"] = decorate_project(dashboard["current_project"])
    return render_template(
        "entry_build_dashboard.html",
        title="BUILD · Flowinone",
        dashboard=dashboard,
        projects=[decorate_project(project) for project in service.repository.list_projects(statuses=("active",))],
        notice=request.args.get("notice"),
        error=request.args.get("error"),
    )


def _render_mode(mode: str):
    service = _service()
    entries = [decorate_entry(entry, service) for entry in service.repository.list_entries(mode=mode)]
    return render_template(
        "entry_mode.html",
        title=f"{mode.upper()} · Flowinone",
        mode=mode,
        mode_copy=MODE_COPY[mode],
        entries=entries,
        projects=[decorate_project(project) for project in service.repository.list_projects(statuses=("active",))],
        notice=request.args.get("notice"),
        error=request.args.get("error"),
    )


@bp.get("/think/", strict_slashes=False)
def think_page():
    return _render_mode("think")


@bp.get("/learn/", strict_slashes=False)
def learn_page():
    return _render_mode("learn")


@bp.get("/scan/", strict_slashes=False)
def scan_page():
    return _render_mode("scan")


@bp.get("/recover/", strict_slashes=False)
def recover_page():
    return _render_mode("recover")


@bp.get("/write/", strict_slashes=False)
def write_page():
    service = _service()
    return render_template(
        "entry_write.html",
        title="WRITE · Flowinone",
        mode="write",
        mode_copy=MODE_COPY["write"],
        entries=[
            decorate_entry(entry, service)
            for entry in service.repository.list_entries(mode="write")
        ],
        assets=_knowledge().list_assets(),
        projects=[
            decorate_project(project)
            for project in service.repository.list_projects(statuses=("active",))
        ],
        notice=request.args.get("notice"),
        error=request.args.get("error"),
    )


@bp.post("/output-assets/")
def output_asset_create():
    data = _form_data()
    source_type = str(data.pop("source_type", "") or "").strip()
    source_id = str(data.pop("source_id", "") or "").strip()
    if source_type and source_id:
        data["sources"] = [{"source_type": source_type, "source_id": source_id}]
    try:
        _knowledge().create_asset(data)
    except Exception as exc:
        return _form_error("entry_system.write_page", exc)
    return redirect(url_for("entry_system.write_page", notice="Output Asset 已建立"))


@bp.post("/entries/new")
def entry_new_build():
    service = _service()
    try:
        entry = service.create_build_from_template(_form_data())
    except Exception as exc:
        return _form_error("entry_system.build_dashboard", exc)
    return redirect(service.enter(entry["id"])["destination"], code=303)


@bp.post("/entries/think-to-build")
def entry_think_to_build():
    service = _service()
    try:
        result = service.think_to_build(_form_data())
    except Exception as exc:
        return _form_error("entry_system.think_page", exc)
    return redirect(
        url_for(
            "entry_system.entry_detail",
            entry_id=result["build_entry"]["id"],
            notice="已從 THINK 建立 BUILD Entry",
        )
    )


@bp.post("/entries/from-source")
def entry_create_from_source():
    service = _service()
    data = _form_data()
    try:
        entry = service.create_source_entry(data)
    except Exception as exc:
        return redirect(
            _safe_return_path(
                data.get("return_to"), "entry_system.build_dashboard", error=str(exc)
            )
        )
    return redirect(
        url_for(
            "entry_system.entry_detail",
            entry_id=entry["id"],
            notice=f"已建立 {entry['mode'].upper()} Entry",
        )
    )


@bp.post("/entries/snapshot")
def entry_create_snapshot():
    service = _service()
    data = _form_data()
    try:
        entry = service.create_snapshot(data)
    except Exception as exc:
        return redirect(
            _safe_return_path(
                data.get("return_to"), "resource_library.resource_index", error=str(exc)
            )
        )
    return redirect(
        url_for(
            "entry_system.entry_detail",
            entry_id=entry["id"],
            notice="目前檢視已儲存為 Entry",
        )
    )


@bp.get("/entries/", strict_slashes=False)
def entry_index():
    service = _service()
    mode = (request.args.get("mode") or "").strip().lower()
    if mode and mode not in ENTRY_MODES:
        abort(400, description="無效的 mode")
    statuses = tuple(value for value in request.args.getlist("status") if value in ENTRY_STATUSES)
    project_id = (request.args.get("project_id") or "").strip() or None
    entries = service.repository.list_entries(mode=mode or None, statuses=statuses, project_id=project_id)
    return render_template(
        "entry_index.html",
        title="Entries · Flowinone",
        entries=[decorate_entry(entry, service) for entry in entries],
        modes=ENTRY_MODES,
        statuses=ENTRY_STATUSES,
        filters={"mode": mode, "statuses": statuses, "project_id": project_id or ""},
        projects=[decorate_project(project) for project in service.repository.list_projects()],
        notice=request.args.get("notice"),
        error=request.args.get("error"),
    )


@bp.post("/entries/")
def entry_create():
    service = _service()
    data = _entry_form_data()
    try:
        entry = service.create_entry(data)
    except Exception as exc:
        return _form_error("entry_system.entry_index", exc)
    return redirect(url_for("entry_system.entry_detail", entry_id=entry["id"], notice="Entry 已建立"))


@bp.get("/entries/<entry_id>/", strict_slashes=False)
def entry_detail(entry_id: str):
    service = _service()
    try:
        entry = decorate_entry(service.repository.get_entry(entry_id, include_events=True), service)
    except EntryNotFound:
        abort(404)
    return render_template(
        "entry_detail.html",
        title=f"{entry['name']} · Flowinone",
        entry=entry,
        projects=[decorate_project(project) for project in service.repository.list_projects()],
        modes=ENTRY_MODES,
        statuses=ENTRY_STATUSES,
        notice=request.args.get("notice"),
        error=request.args.get("error"),
    )


@bp.post("/entries/<entry_id>/")
def entry_update(entry_id: str):
    service = _service()
    try:
        service.update_entry(entry_id, _form_data())
    except Exception as exc:
        return _form_error("entry_system.entry_detail", exc, entry_id=entry_id)
    return redirect(url_for("entry_system.entry_detail", entry_id=entry_id, notice="Entry 已更新"))


@bp.post("/entries/<entry_id>/enter")
def entry_enter(entry_id: str):
    service = _service()
    try:
        result = service.enter(entry_id)
    except EntryNotFound:
        abort(404)
    except Exception as exc:
        return _form_error("entry_system.entry_detail", exc, entry_id=entry_id)
    return redirect(result["destination"], code=303)


@bp.post("/entries/<entry_id>/delete")
def entry_delete(entry_id: str):
    service = _service()
    try:
        service.repository.delete_entry(entry_id)
    except EntryNotFound:
        abort(404)
    return redirect(url_for("entry_system.entry_index", notice="Entry 已刪除"))


@bp.get("/projects/", strict_slashes=False)
def project_index():
    service = _service()
    return render_template(
        "entry_projects.html",
        title="Projects · Flowinone",
        projects=[decorate_project(project) for project in service.repository.list_projects()],
        project_statuses=PROJECT_STATUSES,
        notice=request.args.get("notice"),
        error=request.args.get("error"),
    )


@bp.post("/projects/")
def project_create():
    service = _service()
    try:
        project = service.create_project(_form_data())
    except Exception as exc:
        return _form_error("entry_system.project_index", exc)
    return redirect(url_for("entry_system.project_detail", project_id=project["id"], notice="Project 已建立"))


@bp.get("/projects/<project_id>/", strict_slashes=False)
def project_detail(project_id: str):
    service = _service()
    try:
        project = decorate_project(service.repository.get_project(project_id))
    except ProjectNotFound:
        abort(404)
    project["entries"] = [decorate_entry(entry, service) for entry in project.get("entries", [])]
    project["decisions"] = _knowledge().list_decisions(project_id)
    project["related_resources"] = [
        decorate_retrieved_resource(resource)
        for resource in _knowledge().retrieve(project_id=project_id, limit=20)
    ]
    project["output_assets"] = _knowledge().list_assets(project_id=project_id)
    return render_template(
        "entry_project_detail.html",
        title=f"{project['name']} · Flowinone",
        project=project,
        project_statuses=PROJECT_STATUSES,
        notice=request.args.get("notice"),
        error=request.args.get("error"),
    )


@bp.post("/projects/<project_id>/")
def project_update(project_id: str):
    service = _service()
    try:
        service.update_project(project_id, _form_data())
    except Exception as exc:
        return _form_error("entry_system.project_detail", exc, project_id=project_id)
    return redirect(url_for("entry_system.project_detail", project_id=project_id, notice="Project 已更新"))


@bp.post("/projects/<project_id>/decisions")
def project_decision_create(project_id: str):
    try:
        _knowledge().create_decision(project_id, _form_data())
    except Exception as exc:
        return _form_error("entry_system.project_detail", exc, project_id=project_id)
    return redirect(
        url_for("entry_system.project_detail", project_id=project_id, notice="Decision 已記錄")
    )


# JSON API
@bp.get("/api/dashboard/build")
def api_build_dashboard():
    return jsonify(_service().build_dashboard())


@bp.get("/api/entries")
def api_entries_list():
    service = _service()
    mode = (request.args.get("mode") or "").strip().lower() or None
    if mode and mode not in ENTRY_MODES:
        return jsonify({"error": "無效的 Entry mode"}), 400
    statuses = tuple(value for value in request.args.getlist("status") if value in ENTRY_STATUSES)
    return jsonify(
        {
            "items": service.repository.list_entries(
                mode=mode,
                statuses=statuses,
                project_id=(request.args.get("project_id") or "").strip() or None,
            )
        }
    )


@bp.post("/api/entries")
def api_entries_create():
    try:
        entry = _service().create_entry(request.get_json(silent=True) or {})
    except Exception as exc:
        return _api_error(exc)
    return jsonify(entry), 201


@bp.post("/api/entries/think-to-build")
def api_entries_think_to_build():
    try:
        result = _service().think_to_build(request.get_json(silent=True) or {})
    except Exception as exc:
        return _api_error(exc)
    return jsonify(result), 201


@bp.get("/api/entries/<entry_id>")
def api_entry_get(entry_id: str):
    try:
        return jsonify(_service().repository.get_entry(entry_id, include_events=True))
    except Exception as exc:
        return _api_error(exc)


@bp.patch("/api/entries/<entry_id>")
def api_entry_patch(entry_id: str):
    try:
        return jsonify(_service().update_entry(entry_id, request.get_json(silent=True) or {}))
    except Exception as exc:
        return _api_error(exc)


@bp.delete("/api/entries/<entry_id>")
def api_entry_delete(entry_id: str):
    try:
        _service().repository.delete_entry(entry_id)
    except Exception as exc:
        return _api_error(exc)
    return "", 204


@bp.post("/api/entries/<entry_id>/enter")
def api_entry_enter(entry_id: str):
    try:
        return jsonify(_service().enter(entry_id))
    except Exception as exc:
        return _api_error(exc)


@bp.get("/api/projects")
def api_projects_list():
    statuses = tuple(value for value in request.args.getlist("status") if value in PROJECT_STATUSES)
    return jsonify({"items": _service().repository.list_projects(statuses=statuses)})


@bp.post("/api/projects")
def api_projects_create():
    try:
        project = _service().create_project(request.get_json(silent=True) or {})
    except Exception as exc:
        return _api_error(exc)
    return jsonify(project), 201


@bp.get("/api/projects/<project_id>")
def api_project_get(project_id: str):
    try:
        return jsonify(_service().repository.get_project(project_id))
    except Exception as exc:
        return _api_error(exc)


@bp.patch("/api/projects/<project_id>")
def api_project_patch(project_id: str):
    try:
        return jsonify(_service().update_project(project_id, request.get_json(silent=True) or {}))
    except Exception as exc:
        return _api_error(exc)


@bp.delete("/api/projects/<project_id>")
def api_project_delete(project_id: str):
    try:
        _service().repository.delete_project(project_id)
    except Exception as exc:
        return _api_error(exc)
    return "", 204


@bp.get("/api/retrieval")
def api_context_retrieval():
    try:
        items = _knowledge().retrieve(
            project_id=(request.args.get("project_id") or "").strip() or None,
            mode=(request.args.get("mode") or "").strip().lower() or None,
            query=(request.args.get("q") or "").strip(),
            limit=request.args.get("limit", 12),
        )
    except Exception as exc:
        return _api_error(exc)
    return jsonify({"items": items})


@bp.get("/api/output-assets")
def api_output_assets_list():
    try:
        assets = _knowledge().list_assets(
            project_id=(request.args.get("project_id") or "").strip() or None,
            status=(request.args.get("status") or "").strip() or None,
        )
    except Exception as exc:
        return _api_error(exc)
    return jsonify({"items": assets})


@bp.post("/api/output-assets")
def api_output_assets_create():
    try:
        asset = _knowledge().create_asset(request.get_json(silent=True) or {})
    except Exception as exc:
        return _api_error(exc)
    return jsonify(asset), 201


@bp.patch("/api/output-assets/<asset_id>")
def api_output_asset_patch(asset_id: str):
    try:
        asset = _knowledge().update_asset(asset_id, request.get_json(silent=True) or {})
    except Exception as exc:
        return _api_error(exc)
    return jsonify(asset)


@bp.get("/api/projects/<project_id>/decisions")
def api_project_decisions(project_id: str):
    return jsonify({"items": _knowledge().list_decisions(project_id)})


@bp.post("/api/projects/<project_id>/decisions")
def api_project_decision_create(project_id: str):
    try:
        decision = _knowledge().create_decision(project_id, request.get_json(silent=True) or {})
    except Exception as exc:
        return _api_error(exc)
    return jsonify(decision), 201


def register_entry_system(app: Flask) -> None:
    """Register routes and a small migration helper on the existing Flask app."""
    app.register_blueprint(bp)

    @app.cli.command("entries-db-upgrade")
    def entries_db_upgrade() -> None:
        """Upgrade the shared local SQLite database through the Entry migration."""
        configured = app.config.get("FLOWINONE_RESOURCE_DB_PATH")
        database = _database() if configured else get_resource_database()
        upgrade_database(database.path)
        click.echo(f"Entry system database ready: {database.path}")


__all__ = ["bp", "decorate_entry", "decorate_project", "register_entry_system"]
