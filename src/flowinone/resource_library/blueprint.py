"""Resource HTML pages and Blueprint registration."""

from __future__ import annotations

import math
from pathlib import Path

from flask import Flask, abort, current_app, redirect, render_template, request, send_file, url_for

from config import CHROME_BOOKMARK_PATH

from . import api as _api  # Register JSON routes on the shared Blueprint.
from .canonical import ensure_within
from .commands import register_resource_commands
from .enrichment import EnrichmentService
from .http import bp, _database, _form_error, _list_params, _service, decorate_resource, import_uploaded_bookmarks
from .repository import ResourceNotFound
from .settings import get_resource_settings
from .versions import ResourceVersionService

RESOURCE_TYPE_LABELS = {
    "article": "文章",
    "web_page": "網頁",
    "video": "影片",
    "pdf": "PDF",
    "github": "GitHub",
    "image": "圖片",
    "social_post": "社群貼文",
    "unknown": "其他",
}


@bp.get("/resources/")
def resource_index():
    service = _service()
    params = _list_params()
    page = service.repository.list(**params)
    return render_template(
        "resource_library.html",
        title="Resources · Flowinone",
        resources=[decorate_resource(item) for item in page.items],
        page=page,
        page_count=max(1, math.ceil(page.total / page.per_page)),
        stats=service.repository.stats(),
        filters=params,
        notice=request.args.get("notice"),
        error=request.args.get("error"),
        resource_type_labels=RESOURCE_TYPE_LABELS,
    )


@bp.post("/resources/")
def resource_create():
    try:
        result = _service().create_url(
            request.form.get("url", ""),
            title=request.form.get("title", ""),
            enqueue=request.form.get("enqueue", "1") != "0",
        )
    except Exception as exc:
        return _form_error("resource_library.resource_index", exc)
    return redirect(
        url_for(
            "resource_library.resource_detail",
            resource_id=result["resource"]["id"],
            notice="資源已加入",
        )
    )


@bp.post("/resources/import/chrome")
def resource_import_chrome():
    service = _service()
    path = Path(current_app.config.get("CHROME_BOOKMARK_PATH", CHROME_BOOKMARK_PATH))
    try:
        result = service.import_file_if_changed(
            path,
            format_hint="json",
            enqueue=request.form.get("enqueue", "1") != "0",
        )
        message = (
            "Chrome 書籤沒有變更"
            if not result.get("changed")
            else (
                f"同步完成：新增 {result.get('created', 0)}、"
                f"既有 {result.get('duplicates', 0)}、失敗 {result.get('failed', 0)}"
            )
        )
    except Exception as exc:
        return _form_error("resource_library.resource_index", exc)
    return redirect(url_for("resource_library.resource_index", notice=message))


@bp.post("/resources/import/upload")
def resource_import_upload():
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return _form_error("resource_library.resource_index", ValueError("請選擇書籤檔案"))
    try:
        summary = import_uploaded_bookmarks(
            _service(),
            upload,
            format_hint=request.form.get("format"),
            enqueue=request.form.get("enqueue", "1") != "0",
        )
        message = (
            f"匯入完成：新增 {summary.created}、"
            f"既有 {summary.duplicates}、失敗 {summary.failed}"
        )
    except Exception as exc:
        return _form_error("resource_library.resource_index", exc)
    return redirect(url_for("resource_library.resource_index", notice=message))


@bp.get("/resources/<resource_id>/")
def resource_detail(resource_id: str):
    service = _service()
    try:
        resource = decorate_resource(service.repository.get(resource_id))
    except ResourceNotFound:
        abort(404)
    resource["extracted_text"] = service.repository.latest_content_text(resource_id)[:100_000]
    content_versions = ResourceVersionService(_database()).list_versions(
        resource_id, limit=5
    )
    return render_template(
        "resource_detail.html",
        title=f"{resource['title']} · Flowinone",
        resource=resource,
        content_versions=content_versions,
        jobs=service.jobs.list_for_resource(resource_id),
        similar_resources=[
            decorate_resource(item)
            for item in service.repository.find_similar(resource_id, limit=6)
        ],
        ai_available=EnrichmentService(_database()).ai.available,
        notice=request.args.get("notice"),
        error=request.args.get("error"),
    )


@bp.get("/resources/<resource_id>/versions/")
def resource_versions(resource_id: str):
    service = _service()
    version_service = ResourceVersionService(_database())
    try:
        resource = decorate_resource(service.repository.get(resource_id))
        versions = version_service.list_versions(resource_id)
    except ResourceNotFound:
        abort(404)
    from_version = request.args.get("from", "")
    to_version = request.args.get("to", "")
    comparison = None
    comparison_error = None
    if not from_version and not to_version and len(versions) >= 2:
        from_version, to_version = versions[1]["id"], versions[0]["id"]
    if from_version or to_version:
        try:
            comparison = version_service.compare(
                resource_id,
                from_version_id=from_version,
                to_version_id=to_version,
            )
        except (LookupError, ValueError) as exc:
            comparison_error = str(exc)
    return render_template(
        "resource_versions.html",
        title=f"內容版本 · {resource['title']} · Flowinone",
        resource=resource,
        versions=versions,
        selected_from=from_version,
        selected_to=to_version,
        comparison=comparison,
        comparison_error=comparison_error,
    )


@bp.post("/resources/<resource_id>/tags")
def resource_tags(resource_id: str):
    raw = request.form.get("tags", "")
    tags = [value.strip() for value in raw.replace("，", ",").split(",") if value.strip()]
    try:
        _service().replace_tags(resource_id, tags)
    except Exception as exc:
        return _form_error("resource_library.resource_detail", exc, resource_id=resource_id)
    return redirect(
        url_for(
            "resource_library.resource_detail",
            resource_id=resource_id,
            notice="Tags 已更新",
        )
    )


@bp.post("/resources/<resource_id>/enrich")
def resource_enrich(resource_id: str):
    try:
        _service().enqueue_enrichment(
            resource_id,
            include_ai=request.form.get("include_ai") == "1",
            force=request.form.get("force") == "1",
        )
    except Exception as exc:
        return _form_error("resource_library.resource_detail", exc, resource_id=resource_id)
    return redirect(
        url_for(
            "resource_library.resource_detail",
            resource_id=resource_id,
            notice="已加入處理佇列",
        )
    )


@bp.get("/resources/<resource_id>/asset/<kind>")
def resource_asset(resource_id: str, kind: str):
    try:
        resource = _service().repository.get(resource_id)
    except ResourceNotFound:
        abort(404)
    path_value = (
        resource.get("thumbnail_path")
        if kind == "thumbnail"
        else resource.get("favicon_path")
    )
    if not path_value:
        abort(404)
    try:
        path = ensure_within(get_resource_settings().data_dir, Path(path_value))
    except ValueError:
        abort(403)
    if not path.is_file():
        abort(404)
    return send_file(path, conditional=True, max_age=3600)


def register_resource_library(app: Flask) -> None:
    """Register renderer routes, config defaults, and maintenance commands."""
    settings = get_resource_settings()
    app.config.setdefault("FLOWINONE_RESOURCE_DB_PATH", str(settings.database_path))
    app.config.setdefault("CHROME_BOOKMARK_PATH", CHROME_BOOKMARK_PATH)
    app.config.setdefault("FLOWINONE_RESOURCE_LINK_THUMBNAILS", True)
    app.config.setdefault("MAX_CONTENT_LENGTH", settings.max_download_bytes)
    app.register_blueprint(bp)
    register_resource_commands(app)


__all__ = ["bp", "decorate_resource", "register_resource_library"]
