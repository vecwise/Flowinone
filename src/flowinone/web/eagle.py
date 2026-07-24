"""Eagle source browsing, search, stream, and detail routes."""

from __future__ import annotations

from flask import Blueprint, abort, redirect, render_template, request, url_for

from src.file_handler import (
    ExternalServiceError,
    MediaNotFound,
    get_eagle_folders,
    get_eagle_image_details,
    get_eagle_images_by_folderid,
    get_eagle_images_by_smart_folder_id,
    get_eagle_images_by_tag,
    get_eagle_smart_folders,
    get_eagle_stream_items,
    get_eagle_tags,
    get_eagle_video_details,
    get_subfolders_info,
    search_eagle_items,
)

from .common import (
    build_detail_actions,
    catalog_related_for_origin,
    decorate_related_items,
    merge_related,
    normalize_current_url,
    require_feature,
    serialize_detail,
    serialize_payload,
    to_dict,
)
from .api import parse_query, validated_json
from .schemas import EagleStreamOutput, EagleStreamQuery


bp = Blueprint("eagle", __name__)


def page_args() -> tuple[int, int]:
    try:
        offset = int(request.args.get("offset", 0))
        limit = int(request.args.get("limit", 120))
    except ValueError:
        abort(400, description="Invalid Eagle pagination")
    return max(0, offset), max(1, min(limit, 200))


def attach_pagination_urls(metadata: dict) -> None:
    pagination = metadata.get("pagination")
    if not pagination:
        return

    def page_url(offset):
        if offset is None:
            return None
        params = request.args.to_dict(flat=True)
        params.update({"offset": offset, "limit": pagination["limit"]})
        return url_for(request.endpoint, **(request.view_args or {}), **params)

    pagination["previous_url"] = page_url(pagination.get("previous_offset"))
    pagination["next_url"] = page_url(pagination.get("next_offset"))


def _attach_detail_urls(items: list[dict], current_url: str) -> list[dict]:
    for item in items:
        item_id = item.get("id")
        if not item_id:
            continue
        if item.get("media_type") == "video":
            item["url"] = url_for(
                "eagle.view_eagle_video", item_id=item_id, return_to=current_url
            )
        elif item.get("media_type") == "image":
            item["url"] = url_for(
                "eagle.view_eagle_image", item_id=item_id, return_to=current_url
            )
    return items


def _render_paginated(loader, *args):
    offset, limit = page_args()
    try:
        metadata, data = loader(*args, offset=offset, limit=limit)
    except MediaNotFound:
        abort(404)
    except ExternalServiceError as exc:
        abort(500, description=str(exc))
    metadata_dict, data_list = serialize_payload(metadata, data)
    attach_pagination_urls(metadata_dict)
    _attach_detail_urls(data_list, normalize_current_url())
    return metadata_dict, data_list


@bp.get("/EAGLE_folders/")
@require_feature("eagle")
def list_all_eagle_folder():
    try:
        metadata, data = get_eagle_folders()
    except ExternalServiceError as exc:
        abort(500, description=str(exc))
    metadata_dict, data_list = serialize_payload(metadata, data)
    return render_template("view_both.html", metadata=metadata_dict, data=data_list)


@bp.get("/EAGLE_tags/")
@require_feature("eagle")
def list_eagle_tags():
    try:
        metadata, tags = get_eagle_tags()
    except ExternalServiceError as exc:
        abort(500, description=str(exc))
    return render_template("eagle_tags.html", metadata=to_dict(metadata), tags=tags)


@bp.get("/EAGLE_smart_folders/")
@require_feature("eagle")
def list_eagle_smart_folders():
    try:
        metadata, data = get_eagle_smart_folders()
    except ExternalServiceError as exc:
        abort(500, description=str(exc))
    metadata_dict, data_list = serialize_payload(metadata, data)
    return render_template("view_both.html", metadata=metadata_dict, data=data_list)


@bp.get("/EAGLE_smart_folder/<smart_folder_id>/")
@require_feature("eagle")
def view_eagle_smart_folder(smart_folder_id: str):
    metadata, data = _render_paginated(
        get_eagle_images_by_smart_folder_id, smart_folder_id
    )
    return render_template("view_both.html", metadata=metadata, data=data)


@bp.get("/EAGLE_folder/<eagle_folder_id>/")
@require_feature("eagle")
def view_eagle_folder(eagle_folder_id: str):
    offset, limit = page_args()
    try:
        metadata, data = get_eagle_images_by_folderid(
            eagle_folder_id, offset=offset, limit=limit
        )
        if offset == 0:
            data = get_subfolders_info(eagle_folder_id) + data
    except MediaNotFound:
        abort(404)
    except ExternalServiceError as exc:
        abort(500, description=str(exc))
    metadata_dict, data_list = serialize_payload(metadata, data)
    attach_pagination_urls(metadata_dict)
    _attach_detail_urls(data_list, normalize_current_url())
    return render_template("view_both.html", metadata=metadata_dict, data=data_list)


@bp.get("/EAGLE_tag/<target_tag>/")
@require_feature("eagle")
def view_images_by_tag(target_tag: str):
    metadata, data = _render_paginated(get_eagle_images_by_tag, target_tag)
    return render_template("view_both.html", metadata=metadata, data=data)


@bp.get("/search")
@require_feature("eagle")
def search_eagle():
    keyword = request.args.get("query", "").strip()
    if not keyword:
        return redirect(request.referrer or url_for("local.index"))
    metadata, data = _render_paginated(search_eagle_items, keyword)
    return render_template("view_both.html", metadata=metadata, data=data)


@bp.get("/EAGLE_stream/")
@require_feature("eagle")
def eagle_stream():
    return render_template("eagle_stream.html")


@bp.get("/api/EAGLE_stream/")
@require_feature("eagle")
def eagle_stream_data():
    query = parse_query(EagleStreamQuery)
    offset = query.offset
    limit = query.limit
    try:
        data = get_eagle_stream_items(offset=offset, limit=limit)
    except ExternalServiceError as exc:
        abort(500, description=str(exc))
    items = []
    for item in (to_dict(value) for value in data):
        item_id = item.get("id")
        if not item_id:
            continue
        endpoint = (
            "eagle.view_eagle_video"
            if item.get("media_type") == "video"
            else "eagle.view_eagle_image"
        )
        items.append(
            {
                "id": item_id,
                "name": item.get("name"),
                "thumbnail_route": item.get("thumbnail_route"),
                "detail_url": url_for(endpoint, item_id=item_id),
                "media_type": item.get("media_type"),
                "ext": item.get("ext"),
            }
        )
    return validated_json(
        {"items": items, "nextOffset": offset + len(items)}, EagleStreamOutput
    )


def _render_eagle_detail(item_id: str, media_kind: str):
    loader = get_eagle_video_details if media_kind == "video" else get_eagle_image_details
    try:
        metadata, detail = loader(item_id)
    except MediaNotFound:
        abort(404)
    except ExternalServiceError as exc:
        abort(500, description=str(exc))
    metadata_dict = to_dict(metadata)
    detail_dict = serialize_detail(detail)
    detail_dict["parent_url"] = (
        request.args.get("return_to") or request.referrer or url_for("local.index")
    )
    return render_template(
        "video_player.html" if media_kind == "video" else "image_viewer.html",
        metadata=metadata_dict,
        **{media_kind: detail_dict},
        related_items=merge_related(
            decorate_related_items(metadata_dict.get("similar"), metadata_dict),
            catalog_related_for_origin("eagle", item_id),
        ),
        recommended_actions=build_detail_actions(media_kind, metadata_dict),
    )


@bp.get("/EAGLE_video/<item_id>/")
@require_feature("eagle")
def view_eagle_video(item_id: str):
    return _render_eagle_detail(item_id, "video")


@bp.get("/EAGLE_image/<item_id>/")
@require_feature("eagle")
def view_eagle_image(item_id: str):
    return _render_eagle_detail(item_id, "image")
