"""Flask UI, JSON API, and CLI integration for rendered web resources."""

from __future__ import annotations

import math
import tempfile
from pathlib import Path

import click
from flask import (
    Blueprint,
    Flask,
    abort,
    current_app,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from config import CHROME_BOOKMARK_PATH
from src.file_handler.thumbnails.store import get_thumbnail_store

from .canonical import ensure_within
from .database import backup_database, get_resource_database, upgrade_database
from .enrichment import EnrichmentService
from .maintenance import rebuild_fts, retry_failed_jobs
from .repository import ResourceNotFound
from .service import ResourceService
from .settings import get_resource_settings
from .worker import ResourceWorker
from src.flowinone.web.api import (
    api_error,
    parse_json,
    parse_payload,
    parse_query,
    validated_json,
)
from src.flowinone.web.schemas import (
    EnrichmentRequest,
    ChromeImportFormRequest,
    ImportSummaryOutput,
    JobsOutput,
    ResourceCreateOutput,
    ResourceCreateRequest,
    ResourceItemsOutput,
    ResourceListQuery,
    ResourceListOutput,
    ResourceOutput,
    ResourcePatchRequest,
    ResourceSearchRequest,
    ResourceTagDeleteQuery,
    ResourceTagsRequest,
    LimitQuery,
)


bp = Blueprint("resource_library", __name__)
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


def _database():
    configured = current_app.config.get("FLOWINONE_RESOURCE_DB_PATH")
    return get_resource_database(
        Path(configured) if configured else None,
        migrate=bool(
            current_app.config.get("FLOWINONE_AUTO_MIGRATE", current_app.testing)
        ),
    )


def _service() -> ResourceService:
    return ResourceService(
        _database(),
        link_thumbnail_cache=bool(
            current_app.config.get("FLOWINONE_RESOURCE_LINK_THUMBNAILS", True)
        ),
    )


def _list_params(payload=None) -> dict:
    values = payload or request.args

    def multi(name: str) -> tuple[str, ...]:
        if hasattr(values, "getlist"):
            result = values.getlist(name)
        else:
            raw = values.get(name, []) if isinstance(values, dict) else []
            result = raw if isinstance(raw, list) else str(raw).split(",")
        return tuple(value.strip() for value in result if str(value).strip())

    try:
        page = int(values.get("page", 1))
        per_page = int(values.get("per_page", 30))
    except (TypeError, ValueError):
        page, per_page = 1, 30
    return {
        "query": str(values.get("q") or values.get("query") or "").strip(),
        "source_types": multi("source_type"),
        "tag": str(values.get("tag") or "").strip(),
        "domain": str(values.get("domain") or "").strip(),
        "page": page,
        "per_page": per_page,
    }


def _thumbnail_url(resource: dict) -> str:
    if resource.get("thumbnail_path"):
        return url_for(
            "resource_library.resource_asset",
            resource_id=resource["id"],
            kind="thumbnail",
        )
    media_id = resource.get("thumbnail_media_id")
    if media_id and get_thumbnail_store().get_thumbnail_path(media_id):
        return url_for("chrome.serve_bookmark_thumbnail", media_id=media_id)
    return url_for("static", filename="default_thumbnail.svg")


def decorate_resource(resource: dict) -> dict:
    decorated = dict(resource)
    decorated["thumbnail_url"] = _thumbnail_url(resource)
    decorated["detail_url"] = url_for(
        "resource_library.resource_detail", resource_id=resource["id"]
    )
    return decorated


def _form_error(endpoint: str, error: Exception, **values):
    return redirect(url_for(endpoint, error=str(error), **values))


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
    format_hint = request.form.get("format") or Path(upload.filename).suffix.lstrip(".")
    suffix = ".html" if format_hint.lower() in {"html", "htm"} else ".json"
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="flowinone-bookmarks-", suffix=suffix, delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            upload.save(temporary)
        summary = _service().import_file(
            temporary_path,
            format_hint=format_hint,
            enqueue=request.form.get("enqueue", "1") != "0",
        )
        message = (
            f"匯入完成：新增 {summary.created}、"
            f"既有 {summary.duplicates}、失敗 {summary.failed}"
        )
    except Exception as exc:
        return _form_error("resource_library.resource_index", exc)
    finally:
        if temporary_path:
            temporary_path.unlink(missing_ok=True)
    return redirect(url_for("resource_library.resource_index", notice=message))


@bp.get("/resources/<resource_id>/")
def resource_detail(resource_id: str):
    service = _service()
    try:
        resource = decorate_resource(service.repository.get(resource_id))
    except ResourceNotFound:
        abort(404)
    resource["extracted_text"] = service.repository.latest_content_text(resource_id)[:100_000]
    return render_template(
        "resource_detail.html",
        title=f"{resource['title']} · Flowinone",
        resource=resource,
        jobs=service.jobs.list_for_resource(resource_id),
        similar_resources=[
            decorate_resource(item)
            for item in service.repository.find_similar(resource_id, limit=6)
        ],
        ai_available=EnrichmentService(_database()).ai.available,
        notice=request.args.get("notice"),
        error=request.args.get("error"),
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


@bp.get("/api/resources")
def api_resources_list():
    query = parse_query(ResourceListQuery, list_fields=("source_type",))
    page = _service().repository.list(**_list_params(query.model_dump()))
    return validated_json(
        {
            "items": [decorate_resource(item) for item in page.items],
            "total": page.total,
            "page": page.page,
            "per_page": page.per_page,
        },
        ResourceListOutput,
    )


@bp.post("/api/resources")
def api_resources_create():
    payload = parse_json(ResourceCreateRequest)
    try:
        result = _service().create_url(
            payload.url,
            title=payload.title,
            enqueue=payload.enqueue,
        )
    except (ValueError, RuntimeError) as exc:
        return api_error(str(exc), 400)
    result["resource"] = decorate_resource(result["resource"])
    return validated_json(
        result,
        ResourceCreateOutput,
        201 if result["import"]["created"] else 200,
    )


@bp.get("/api/resources/<resource_id>")
def api_resource_get(resource_id: str):
    try:
        return validated_json(
            decorate_resource(_service().repository.get(resource_id)), ResourceOutput
        )
    except ResourceNotFound:
        return api_error("not_found", 404)


@bp.patch("/api/resources/<resource_id>")
def api_resource_patch(resource_id: str):
    payload = parse_json(ResourcePatchRequest)
    changes = payload.model_dump(exclude_none=True)
    if not changes:
        return api_error("Only title and availability are editable", 400)
    try:
        resource = _service().update_resource(resource_id, changes)
    except ResourceNotFound:
        return api_error("not_found", 404)
    except ValueError as exc:
        return api_error(str(exc), 400)
    return validated_json(decorate_resource(resource), ResourceOutput)


@bp.delete("/api/resources/<resource_id>")
def api_resource_delete(resource_id: str):
    try:
        _service().repository.delete(resource_id)
    except ResourceNotFound:
        return api_error("not_found", 404)
    return "", 204


@bp.post("/api/resources/<resource_id>/tags")
def api_resource_tags(resource_id: str):
    payload = parse_json(ResourceTagsRequest)
    try:
        return validated_json(
            _service().replace_tags(resource_id, payload.tags), ResourceOutput
        )
    except ResourceNotFound:
        return api_error("not_found", 404)


@bp.delete("/api/resources/<resource_id>/tags/<tag_id>")
def api_resource_tag_delete(resource_id: str, tag_id: str):
    query = parse_query(ResourceTagDeleteQuery)
    try:
        resource = _service().repository.remove_tag(
            resource_id,
            tag_id,
            source=query.source,
        )
    except ResourceNotFound:
        return api_error("not_found", 404)
    except ValueError as exc:
        return api_error(str(exc), 400)
    return validated_json(resource, ResourceOutput)


@bp.post("/api/resources/<resource_id>/enrich")
def api_resource_enrich(resource_id: str):
    payload = parse_json(EnrichmentRequest)
    try:
        jobs = _service().enqueue_enrichment(
            resource_id,
            include_ai=payload.include_ai,
            force=payload.force,
        )
    except ResourceNotFound:
        return api_error("not_found", 404)
    return validated_json({"jobs": jobs}, JobsOutput, 202)


@bp.post("/api/resources/<resource_id>/retry")
def api_resource_retry(resource_id: str):
    payload = parse_json(EnrichmentRequest)
    try:
        jobs = _service().enqueue_enrichment(
            resource_id,
            include_ai=payload.include_ai,
            force=True,
        )
    except ResourceNotFound:
        return api_error("not_found", 404)
    return validated_json({"jobs": jobs}, JobsOutput, 202)


@bp.get("/api/resources/<resource_id>/similar")
def api_resource_similar(resource_id: str):
    query = parse_query(LimitQuery)
    try:
        items = _service().repository.find_similar(
            resource_id,
            limit=query.limit,
        )
    except ResourceNotFound:
        return api_error("not_found", 404)
    return validated_json(
        {"items": [decorate_resource(item) for item in items]}, ResourceItemsOutput
    )


@bp.post("/api/search")
def api_search():
    payload = parse_json(ResourceSearchRequest)
    page = _service().repository.list(**_list_params(payload.model_dump()))
    return validated_json(
        {"items": [decorate_resource(item) for item in page.items], "total": page.total},
        ResourceListOutput,
    )


@bp.post("/api/imports/chrome")
def api_import_chrome():
    service = _service()
    options = parse_payload(ChromeImportFormRequest, request.form.to_dict(flat=True))
    upload = request.files.get("file")
    if upload is None:
        path = Path(current_app.config.get("CHROME_BOOKMARK_PATH", CHROME_BOOKMARK_PATH))
        try:
            summary = service.import_file(path, format_hint="json", enqueue=True)
        except Exception as exc:
            return api_error(str(exc), 400)
        return validated_json(summary.to_dict(), ImportSummaryOutput)
    format_hint = options.format or Path(upload.filename or "").suffix.lstrip(".")
    suffix = ".html" if format_hint.lower() in {"html", "htm"} else ".json"
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="flowinone-api-bookmarks-", suffix=suffix, delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            upload.save(temporary)
        summary = service.import_file(
            temporary_path,
            format_hint=format_hint,
            enqueue=True,
        )
        return validated_json(summary.to_dict(), ImportSummaryOutput)
    except Exception as exc:
        return api_error(str(exc), 400)
    finally:
        if temporary_path:
            temporary_path.unlink(missing_ok=True)


def register_resource_library(app: Flask) -> None:
    """Register renderer routes, config defaults, and maintenance commands."""
    settings = get_resource_settings()
    app.config.setdefault("FLOWINONE_RESOURCE_DB_PATH", str(settings.database_path))
    app.config.setdefault("CHROME_BOOKMARK_PATH", CHROME_BOOKMARK_PATH)
    app.config.setdefault("FLOWINONE_RESOURCE_LINK_THUMBNAILS", True)
    app.config.setdefault("MAX_CONTENT_LENGTH", settings.max_download_bytes)
    app.register_blueprint(bp)

    @app.cli.command("resources-sync")
    @click.option("--path", "path_value", type=click.Path(path_type=Path), default=None)
    @click.option("--format", "format_hint", type=click.Choice(["json", "html"]), default=None)
    @click.option("--no-enqueue", is_flag=True, help="Import without enrichment jobs.")
    def resources_sync(path_value, format_hint, no_enqueue):
        """Import a Chrome profile/export into rendered Resources."""
        target = path_value or Path(current_app.config["CHROME_BOOKMARK_PATH"])
        summary = ResourceService(_database()).import_file(
            target,
            format_hint=format_hint,
            enqueue=not no_enqueue,
        )
        click.echo(summary.to_dict())

    @app.cli.command("resources-worker")
    @click.option("--limit", type=click.IntRange(min=1), default=None)
    def resources_worker(limit):
        """Process ready Resource enrichment jobs and exit when idle."""
        completed = ResourceWorker(_database()).run_until_idle(max_jobs=limit)
        click.echo(f"processed={completed}")

    @app.cli.command("resources-db-upgrade")
    def resources_db_upgrade():
        """Apply all database migrations."""
        database_path = Path(current_app.config["FLOWINONE_RESOURCE_DB_PATH"])
        backup_path = backup_database(database_path)
        if backup_path:
            click.echo(f"backup={backup_path}")
        upgrade_database(database_path)
        click.echo("resource database is at head")

    @app.cli.command("resources-rebuild-fts")
    def resources_rebuild_fts():
        """Rebuild Resource FTS rows from rendered metadata and extracted content."""
        click.echo(rebuild_fts(_database()))

    @app.cli.command("resources-retry-failed")
    def resources_retry_failed():
        """Reset failed enrichment jobs so the worker can retry them."""
        click.echo(retry_failed_jobs(_database()))


__all__ = ["bp", "decorate_resource", "register_resource_library"]
