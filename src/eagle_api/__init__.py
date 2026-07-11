"""Flowinone-facing compatibility facade for Eagle Web API v2.

New code may import :class:`EagleClient`; the ``EAGLE_*`` names keep existing
Flowinone integrations stable while every request now targets ``/api/v2``.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

import pandas as pd

from .client import EagleClient, default_client


def _error_text(response: Mapping[str, Any]) -> Any:
    return response.get("message") or response.get("data")


def _flatten_page(response: dict[str, Any]) -> dict[str, Any]:
    """Adapt v2's nested pagination to the historical Flowinone list contract."""
    if response.get("status") != "success":
        response.setdefault("data", _error_text(response))
        return response
    envelope = response.get("data")
    if isinstance(envelope, dict) and isinstance(envelope.get("data"), list):
        return {
            "status": "success",
            "data": envelope["data"],
            "pagination": {key: envelope.get(key) for key in ("total", "offset", "limit")},
        }
    return response


def send_request_to_eagle(endpoint: str, method: str = "GET", payload: dict | None = None) -> dict[str, Any]:
    return default_client.request(endpoint, method, payload)


def EAGLE_get_folders() -> dict[str, Any]:
    return _flatten_page(default_client.all_pages("folder/get"))


def EAGLE_get_recent_folders() -> dict[str, Any]:
    return _flatten_page(default_client.get_folders(isRecent=True, limit=1000))


def EAGLE_get_folders_df() -> pd.DataFrame:
    response = EAGLE_get_folders()
    return pd.DataFrame(response.get("data") or []) if response.get("status") == "success" else pd.DataFrame()


def EAGLE_get_folders_df_all(flatten: bool = True) -> pd.DataFrame:
    response = EAGLE_get_folders()
    folders = response.get("data") or [] if response.get("status") == "success" else []
    if not flatten:
        return pd.DataFrame(folders)
    rows: list[dict[str, Any]] = []

    def visit(folder: Mapping[str, Any], parent_name: str = "", parent_id: str | None = None) -> None:
        row = dict(folder)
        children = row.pop("children", []) or []
        row["parentName"] = parent_name
        row["parentId"] = parent_id
        rows.append(row)
        for child in children:
            visit(child, str(folder.get("name") or ""), str(folder.get("id") or "") or None)

    for folder in folders:
        visit(folder)
    return pd.DataFrame(rows)


def EAGLE_create_folder(folderName: str, parent: str | None = None, **details: Any) -> dict[str, Any]:
    return default_client.create_folder(folderName, parent=parent, **details)


def EAGLE_update_folder_name(folderId: str, newName: str) -> dict[str, Any]:
    return default_client.update_folder(folderId, name=newName)


def EAGLE_update_folder_details(
    folderId: str,
    newName: str | None = None,
    newDescription: str | None = None,
    newColor: str | None = None,
    parent: str | None = None,
) -> dict[str, Any]:
    changes = {"name": newName, "description": newDescription, "iconColor": newColor}
    if parent is not None:
        changes["parent"] = parent
    return default_client.update_folder(folderId, **{key: value for key, value in changes.items() if value is not None})


def EAGLE_add_image_from_url(
    url: str,
    folderId: str | None = None,
    name: str | None = None,
    website: str | None = None,
    tags: Sequence[str] | None = None,
) -> dict[str, Any]:
    return default_client.add_item(url=url, folders=[folderId] if folderId else None, name=name, website=website, tags=tags)


def EAGLE_add_img_from_json(payload: Mapping[str, Any]) -> dict[str, Any]:
    item = dict(payload)
    folder_id = item.pop("folderId", None)
    if folder_id and not item.get("folders"):
        item["folders"] = [folder_id]
    return default_client.add_item(**item)


def EAGLE_add_multiple_img_from_json(payload: Mapping[str, Any]) -> dict[str, Any]:
    items = [dict(item) for item in payload.get("items") or []]
    shared_folder = payload.get("folderId")
    for item in items:
        own_folder = item.pop("folderId", None)
        folder_id = own_folder or shared_folder
        if folder_id and not item.get("folders"):
            item["folders"] = [folder_id]
    return default_client.add_items(items)


def EAGLE_add_bookmark(url: str, name: str, tags: Sequence[str] | None = None, folders: Sequence[str] | None = None) -> dict[str, Any]:
    return default_client.add_item(bookmarkURL=url, name=name, tags=tags, folders=folders)


def EAGLE_get_tags() -> dict[str, Any]:
    return _flatten_page(default_client.all_pages("tag/get"))


def EAGLE_get_item_info(item_id: str) -> dict[str, Any]:
    response = default_client.get_items(id=item_id)
    if response.get("status") != "success":
        return response
    envelope = response.get("data") or {}
    rows = envelope.get("data") or [] if isinstance(envelope, dict) else []
    if not rows:
        return {"status": "error", "message": f"Eagle item not found: {item_id}", "data": f"Eagle item not found: {item_id}"}
    return {"status": "success", "data": rows[0]}


def EAGLE_get_application_info() -> dict[str, Any]:
    return default_client.app_info()


def EAGLE_get_library_info() -> dict[str, Any]:
    return default_client.library_info()


def EAGLE_get_current_library_path() -> str:
    response = EAGLE_get_library_info()
    if response.get("status") != "success":
        raise ValueError(f"Failed to fetch library info: {_error_text(response)}")
    path = (response.get("data") or {}).get("path")
    if not path:
        raise ValueError("Library path not found in Eagle v2 response")
    return path


def EAGLE_update_item_tags(itemId: str, tags: Sequence[str]) -> dict[str, Any]:
    return default_client.update_item(itemId, tags=list(tags))


def EAGLE_list_items(
    limit: int = 200,
    offset: int = 0,
    orderBy: Optional[str] = None,
    keyword: Optional[str] = None,
    ext: Optional[str] = None,
    tags: Sequence[str] | None = None,
    folders: Sequence[str] | None = None,
    **filters: Any,
) -> dict[str, Any]:
    # V2 item/get intentionally has no orderBy. Preserve the argument for callers,
    # and use item/query when they explicitly ask for full-text keywords elsewhere.
    response = default_client.get_items(limit=limit, offset=offset, keywords=[keyword] if keyword else None, ext=ext, tags=tags, folders=folders, **filters)
    return _flatten_page(response)


def EAGLE_query_items(query: str, limit: int = 120, offset: int = 0) -> dict[str, Any]:
    return _flatten_page(default_client.query_items(query, offset=offset, limit=limit))


def EAGLE_get_smart_folders() -> dict[str, Any]:
    return _flatten_page(default_client.get_smart_folders())


def EAGLE_get_smart_folder_items(smart_folder_id: str, **options: Any) -> dict[str, Any]:
    options.setdefault("limit", 200)
    return _flatten_page(default_client.get_smart_folder_items(smart_folder_id, **options))


def EAGLE_ai_is_ready() -> bool:
    response = default_client.ai_status("isReady")
    return response.get("status") == "success" and response.get("data") is True


def EAGLE_ai_search_text(query: str, limit: int = 20) -> dict[str, Any]:
    return default_client.ai_search_text(query, limit)


def EAGLE_ai_search_similar(item_id: str, limit: int = 8) -> dict[str, Any]:
    return default_client.ai_search_item(item_id, limit)


__all__ = [name for name in globals() if name.startswith("EAGLE_")] + ["EagleClient", "send_request_to_eagle"]
