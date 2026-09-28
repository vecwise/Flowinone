"""Resource JSON APIs with their existing request and response schemas."""

from __future__ import annotations

import tempfile
from pathlib import Path

from flask import current_app, request

from config import CHROME_BOOKMARK_PATH
from src.flowinone.web.api import api_error, parse_json, parse_payload, parse_query, validated_json
from src.flowinone.web.schemas import (
    EnrichmentRequest, ChromeImportFormRequest, ImportSummaryOutput, JobsOutput,
    ResourceCreateOutput, ResourceCreateRequest, ResourceItemsOutput, ResourceListQuery,
    ResourceListOutput, ResourceOutput, ResourcePatchRequest, ResourceSearchRequest,
    ResourceTagDeleteQuery, ResourceTagsRequest, ResourceVersionCompareQuery,
    ResourceVersionDiffOutput, ResourceVersionLimitQuery, ResourceVersionsOutput, LimitQuery,
)

from .http import bp, _database, _list_params, _service, decorate_resource
from .repository import ResourceNotFound
from .versions import ResourceVersionService


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


@bp.get("/api/resources/<resource_id>/versions")
def api_resource_versions(resource_id: str):
    query = parse_query(ResourceVersionLimitQuery)
    try:
        versions = ResourceVersionService(_database()).list_versions(
            resource_id, limit=query.limit
        )
    except ResourceNotFound:
        return api_error("not_found", 404)
    return validated_json({"items": versions}, ResourceVersionsOutput)


@bp.get("/api/resources/<resource_id>/versions/compare")
def api_resource_versions_compare(resource_id: str):
    query = parse_query(ResourceVersionCompareQuery)
    try:
        result = ResourceVersionService(_database()).compare(
            resource_id,
            from_version_id=query.from_version,
            to_version_id=query.to_version,
        )
    except ResourceNotFound:
        return api_error("not_found", 404)
    except (LookupError, ValueError) as exc:
        return api_error(str(exc), 400)
    return validated_json(result, ResourceVersionDiffOutput)


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
