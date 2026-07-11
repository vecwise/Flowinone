"""Typed HTTP client for Eagle Web API v2.

The client deliberately returns Eagle's JSend dictionaries.  That keeps transport
details in one place while allowing callers to decide whether an API error should
be shown, retried, or ignored as an optional capability.
"""

from __future__ import annotations

import os
from typing import Any, Iterable, Mapping, Sequence

import requests


DEFAULT_BASE_URL = "http://localhost:41595/api/v2"
DEFAULT_TIMEOUT = 10.0
MAX_PAGE_SIZE = 1000


def _compact(values: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


class EagleClient:
    """Small, reusable client covering Eagle's public Web API v2 endpoints."""

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("EAGLE_API_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.token = token if token is not None else os.getenv("EAGLE_API_TOKEN")
        self.timeout = timeout
        self.session = session or requests.Session()

    def request(
        self,
        endpoint: str,
        method: str = "GET",
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        params: dict[str, Any] = {}
        if self.token:
            params["token"] = self.token
        request_kwargs: dict[str, Any] = {"params": params, "timeout": self.timeout}
        if method.upper() == "GET":
            params.update(_compact(payload or {}))
        else:
            request_kwargs["json"] = _compact(payload or {})
        try:
            response = self.session.request(method.upper(), url, **request_kwargs)
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                return {"status": "error", "message": "Eagle returned a non-object response"}
            return result
        except (requests.RequestException, ValueError) as exc:
            return {"status": "error", "message": str(exc), "data": str(exc)}

    def all_pages(
        self,
        endpoint: str,
        *,
        method: str = "GET",
        payload: Mapping[str, Any] | None = None,
        page_size: int = MAX_PAGE_SIZE,
    ) -> dict[str, Any]:
        """Collect a v2 paginated endpoint into one standard paginated response."""
        base = dict(payload or {})
        offset = max(0, int(base.pop("offset", 0)))
        page_size = max(1, min(int(page_size), MAX_PAGE_SIZE))
        collected: list[Any] = []
        total: int | None = None
        while True:
            page_payload = {**base, "offset": offset, "limit": page_size}
            response = self.request(endpoint, method, page_payload)
            if response.get("status") != "success":
                return response
            envelope = response.get("data") or {}
            if not isinstance(envelope, dict):
                return {"status": "error", "message": f"Invalid pagination response from {endpoint}"}
            rows = envelope.get("data") or []
            if not isinstance(rows, list):
                return {"status": "error", "message": f"Invalid result list from {endpoint}"}
            collected.extend(rows)
            total = envelope.get("total", total)
            offset += len(rows)
            if not rows or len(rows) < page_size or (total is not None and offset >= int(total)):
                break
        return {
            "status": "success",
            "data": {"data": collected, "total": total if total is not None else len(collected), "offset": 0, "limit": len(collected)},
        }

    # App and library
    def app_info(self) -> dict[str, Any]:
        return self.request("app/info")

    def library_info(self) -> dict[str, Any]:
        return self.request("library/info")

    def library_history(self, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        return self.request("library/history", payload={"offset": offset, "limit": limit})

    def switch_library(self, library_path: str) -> dict[str, Any]:
        return self.request("library/switch", "POST", {"libraryPath": library_path})

    def library_icon(self, library_path: str) -> dict[str, Any]:
        return self.request("library/icon", payload={"libraryPath": library_path})

    # Items
    def get_items(self, **filters: Any) -> dict[str, Any]:
        method = "POST" if any(isinstance(value, (list, tuple, set)) for value in filters.values()) else "GET"
        normalized = {key: list(value) if isinstance(value, (tuple, set)) else value for key, value in filters.items()}
        return self.request("item/get", method, normalized)

    def query_items(self, query: str, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        return self.request("item/query", "POST", {"query": query, "offset": offset, "limit": limit})

    def count_items(self) -> dict[str, Any]:
        return self.request("item/countAll")

    def add_item(self, **item: Any) -> dict[str, Any]:
        return self.request("item/add", "POST", item)

    def add_items(self, items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        return self.request("item/add", "POST", {"items": list(items)})

    def update_item(self, item_id: str, **changes: Any) -> dict[str, Any]:
        return self.request("item/update", "POST", {"id": item_id, **changes})

    def set_custom_thumbnail(self, item_id: str, file_path: str) -> dict[str, Any]:
        return self.request("item/setCustomThumbnail", "POST", {"itemId": item_id, "filePath": file_path})

    def refresh_thumbnail(self, item_id: str) -> dict[str, Any]:
        return self.request("item/refreshThumbnail", "POST", {"itemId": item_id})

    def get_comments(self, item_id: str) -> dict[str, Any]:
        return self.request("item/getComments", payload={"id": item_id})

    def add_comment(self, item_id: str, **comment: Any) -> dict[str, Any]:
        return self.request("item/addComment", "POST", {"id": item_id, **comment})

    def update_comment(self, item_id: str, comment_id: str, **changes: Any) -> dict[str, Any]:
        return self.request("item/updateComment", "POST", {"id": item_id, "commentId": comment_id, **changes})

    def remove_comment(self, item_id: str, comment_id: str) -> dict[str, Any]:
        return self.request("item/removeComment", "POST", {"id": item_id, "commentId": comment_id})

    # Folders
    def get_folders(self, **filters: Any) -> dict[str, Any]:
        return self.request("folder/get", "POST" if filters.get("ids") else "GET", filters)

    def create_folder(self, name: str, **details: Any) -> dict[str, Any]:
        return self.request("folder/create", "POST", {"name": name, **details})

    def update_folder(self, folder_id: str, **changes: Any) -> dict[str, Any]:
        return self.request("folder/update", "POST", {"id": folder_id, **changes})

    # Smart folders
    def get_smart_folders(self, **filters: Any) -> dict[str, Any]:
        return self.request("smartFolder/get", "POST" if filters.get("ids") else "GET", filters)

    def create_smart_folder(self, name: str, conditions: Sequence[Mapping[str, Any]], **details: Any) -> dict[str, Any]:
        return self.request("smartFolder/create", "POST", {"name": name, "conditions": list(conditions), **details})

    def update_smart_folder(self, smart_folder_id: str, **changes: Any) -> dict[str, Any]:
        return self.request("smartFolder/update", "POST", {"id": smart_folder_id, **changes})

    def remove_smart_folder(self, smart_folder_id: str) -> dict[str, Any]:
        return self.request("smartFolder/remove", "POST", {"id": smart_folder_id})

    def get_smart_folder_items(self, smart_folder_id: str, **options: Any) -> dict[str, Any]:
        return self.request("smartFolder/getItems", "POST", {"smartFolderId": smart_folder_id, **options})

    def get_smart_folder_rules(self) -> dict[str, Any]:
        return self.request("smartFolder/getRules")

    # Tags and tag groups
    def get_tags(self, **filters: Any) -> dict[str, Any]:
        return self.request("tag/get", payload=filters)

    def get_recent_tags(self, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        return self.request("tag/getRecentTags", payload={"offset": offset, "limit": limit})

    def get_starred_tags(self, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        return self.request("tag/getStarredTags", payload={"offset": offset, "limit": limit})

    def rename_tag(self, original_name: str, name: str) -> dict[str, Any]:
        return self.request("tag/update", "POST", {"originalName": original_name, "name": name})

    def merge_tags(self, source: str, target: str) -> dict[str, Any]:
        return self.request("tag/merge", "POST", {"source": source, "target": target})

    def get_tag_groups(self, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        return self.request("tagGroup/get", payload={"offset": offset, "limit": limit})

    def create_tag_group(self, name: str, tags: Sequence[str], **details: Any) -> dict[str, Any]:
        return self.request("tagGroup/create", "POST", {"name": name, "tags": list(tags), **details})

    def update_tag_group(self, group_id: str, name: str, tags: Sequence[str], **details: Any) -> dict[str, Any]:
        return self.request("tagGroup/update", "POST", {"id": group_id, "name": name, "tags": list(tags), **details})

    def remove_tag_group(self, group_id: str) -> dict[str, Any]:
        return self.request("tagGroup/remove", "POST", {"id": group_id})

    def add_tags_to_group(self, group_id: str, tags: Sequence[str], remove_from_source: bool = False) -> dict[str, Any]:
        return self.request("tagGroup/addTags", "POST", {"groupId": group_id, "tags": list(tags), "removeFromSource": remove_from_source})

    def remove_tags_from_group(self, group_id: str, tags: Sequence[str]) -> dict[str, Any]:
        return self.request("tagGroup/removeTags", "POST", {"groupId": group_id, "tags": list(tags)})

    # Optional AI Search plugin
    def ai_status(self, status: str) -> dict[str, Any]:
        allowed = {"isInstalled", "isReady", "isStarting", "isSyncing", "getSyncStatus", "checkServiceHealth"}
        if status not in allowed:
            raise ValueError(f"Unknown AI Search status endpoint: {status}")
        return self.request(f"aiSearch/{status}")

    def ai_search_text(self, query: str, limit: int = 20) -> dict[str, Any]:
        return self.request("aiSearch/searchByText", "POST", {"query": query, "options": {"limit": limit}})

    def ai_search_base64(self, base64_data: str, limit: int = 20) -> dict[str, Any]:
        return self.request("aiSearch/searchByBase64", "POST", {"base64": base64_data, "options": {"limit": limit}})

    def ai_search_item(self, item_id: str, limit: int = 20) -> dict[str, Any]:
        return self.request("aiSearch/searchByItemId", "POST", {"itemId": item_id, "options": {"limit": limit}})


default_client = EagleClient()
