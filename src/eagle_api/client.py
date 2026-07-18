"""HTTP clients for Eagle Web API v2.

``EagleClient`` exposes normal reading and asset-editing operations. Destructive
library administration lives in ``EagleAdminClient`` so it is opt-in.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .models import EagleCapabilities, EaglePage


DEFAULT_BASE_URL = "http://localhost:41595/api/v2"
DEFAULT_TIMEOUT = 10.0
MAX_PAGE_SIZE = 1000
DEFAULT_MAX_PAGES = 10_000


class EagleAPIError(RuntimeError):
    """Raised by ``request_or_raise`` when Eagle returns an error response."""


def _compact(values: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def _new_session() -> requests.Session:
    """Create a conservative session: retry idempotent reads, never POST writes."""
    session = requests.Session()
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.15,
        status_forcelist=(429, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": "Flowinone/0.1 Eagle-Web-API-v2"})
    return session


def _build_at_least(build_version: str | None, minimum: int) -> bool | None:
    """Interpret classic ``build22`` strings without guessing date-based builds."""
    if not build_version:
        return None
    match = re.fullmatch(r"build\s*(\d+)", build_version.strip(), flags=re.IGNORECASE)
    return int(match.group(1)) >= minimum if match else None


class EagleClient:
    """Client for Eagle v2 reads and non-destructive asset metadata writes."""

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
        self.session = session or _new_session()
        self._owns_session = session is None

    def close(self) -> None:
        if self._owns_session:
            self.session.close()

    def __enter__(self) -> "EagleClient":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def request(
        self,
        endpoint: str,
        method: str = "GET",
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make one request while retaining Flowinone's JSend result contract."""
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        params: dict[str, Any] = {"token": self.token} if self.token else {}
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
                return {"status": "error", "message": "Eagle returned a non-object response", "error_type": "protocol"}
            return result
        except requests.RequestException as exc:
            return {"status": "error", "message": str(exc), "data": str(exc), "error_type": "transport"}
        except ValueError as exc:
            return {"status": "error", "message": str(exc), "data": str(exc), "error_type": "protocol"}

    def request_or_raise(
        self,
        endpoint: str,
        method: str = "GET",
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self.request(endpoint, method, payload)
        if response.get("status") != "success":
            raise EagleAPIError(str(response.get("message") or response.get("data") or endpoint))
        return response

    def iter_pages(
        self,
        endpoint: str,
        *,
        method: str = "GET",
        payload: Mapping[str, Any] | None = None,
        page_size: int = MAX_PAGE_SIZE,
        max_pages: int = DEFAULT_MAX_PAGES,
    ) -> Iterator[EaglePage[dict[str, Any]]]:
        """Yield stable pages and stop rather than endlessly following bad offsets."""
        base = dict(payload or {})
        offset = max(0, int(base.pop("offset", 0)))
        page_size = max(1, min(int(page_size), MAX_PAGE_SIZE))
        for _ in range(max_pages):
            response = self.request(endpoint, method, {**base, "offset": offset, "limit": page_size})
            if response.get("status") != "success":
                raise EagleAPIError(str(response.get("message") or response.get("data") or endpoint))
            try:
                page = EaglePage.from_response(response)
            except ValueError as exc:
                raise EagleAPIError(f"Invalid pagination response from {endpoint}: {exc}") from exc
            yield page
            if not page.has_more or not page.items:
                return
            next_offset = page.offset + len(page.items)
            if next_offset <= offset:
                raise EagleAPIError(f"Eagle returned a non-advancing page for {endpoint}")
            offset = next_offset
        raise EagleAPIError(f"Pagination limit reached for {endpoint}")

    def all_pages(
        self,
        endpoint: str,
        *,
        method: str = "GET",
        payload: Mapping[str, Any] | None = None,
        page_size: int = MAX_PAGE_SIZE,
        max_pages: int = DEFAULT_MAX_PAGES,
    ) -> dict[str, Any]:
        """Collect pages only for callers that explicitly need the full result set."""
        try:
            pages = list(self.iter_pages(endpoint, method=method, payload=payload, page_size=page_size, max_pages=max_pages))
        except EagleAPIError as exc:
            return {"status": "error", "message": str(exc), "data": str(exc), "error_type": "pagination"}
        items = [item for page in pages for item in page.items]
        total = pages[-1].total if pages else 0
        return {"status": "success", "data": {"data": items, "total": total, "offset": 0, "limit": len(items)}}

    # App and library
    def app_info(self) -> dict[str, Any]:
        return self.request("app/info")

    def library_info(self) -> dict[str, Any]:
        return self.request("library/info")

    def library_history(self, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        return self.request("library/history", payload={"offset": offset, "limit": limit})

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

    # Folders and Smart Folders
    def get_folders(self, **filters: Any) -> dict[str, Any]:
        return self.request("folder/get", "POST" if filters.get("ids") else "GET", filters)

    def create_folder(self, name: str, **details: Any) -> dict[str, Any]:
        return self.request("folder/create", "POST", {"name": name, **details})

    def update_folder(self, folder_id: str, **changes: Any) -> dict[str, Any]:
        return self.request("folder/update", "POST", {"id": folder_id, **changes})

    def get_smart_folders(self, **filters: Any) -> dict[str, Any]:
        return self.request("smartFolder/get", "POST" if filters.get("ids") else "GET", filters)

    def create_smart_folder(self, name: str, conditions: Sequence[Mapping[str, Any]], **details: Any) -> dict[str, Any]:
        return self.request("smartFolder/create", "POST", {"name": name, "conditions": list(conditions), **details})

    def update_smart_folder(self, smart_folder_id: str, **changes: Any) -> dict[str, Any]:
        return self.request("smartFolder/update", "POST", {"id": smart_folder_id, **changes})

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

    def get_tag_groups(self, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        return self.request("tagGroup/get", payload={"offset": offset, "limit": limit})

    def create_tag_group(self, name: str, tags: Sequence[str], **details: Any) -> dict[str, Any]:
        return self.request("tagGroup/create", "POST", {"name": name, "tags": list(tags), **details})

    def update_tag_group(self, group_id: str, name: str, tags: Sequence[str], **details: Any) -> dict[str, Any]:
        return self.request("tagGroup/update", "POST", {"id": group_id, "name": name, "tags": list(tags), **details})

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

    def capabilities(self) -> EagleCapabilities:
        """Inspect optional features without treating an absent AI plugin as failure."""
        errors: list[str] = []
        app_response = self.app_info()
        if app_response.get("status") != "success":
            return EagleCapabilities(available=False, errors=(str(app_response.get("message") or app_response.get("data") or "Eagle unavailable"),))
        app = app_response.get("data") or {}

        def flag(name: str) -> bool:
            response = self.ai_status(name)
            if response.get("status") != "success":
                errors.append(f"{name}: {response.get('message') or response.get('data')}")
                return False
            return response.get("data") is True

        sync_response = self.ai_status("getSyncStatus")
        sync_data = sync_response.get("data") if sync_response.get("status") == "success" else {}
        if sync_response.get("status") != "success":
            errors.append(f"getSyncStatus: {sync_response.get('message') or sync_response.get('data')}")
        smart_response = self.get_smart_folders(limit=1)
        if smart_response.get("status") != "success":
            errors.append(f"smartFolder: {smart_response.get('message') or smart_response.get('data')}")
        build_version = app.get("buildVersion")
        return EagleCapabilities(
            available=True,
            version=app.get("version"),
            build_version=build_version,
            supports_smart_folders=smart_response.get("status") == "success",
            supports_comments=_build_at_least(build_version, 22),
            ai_installed=flag("isInstalled"),
            ai_ready=flag("isReady"),
            ai_starting=flag("isStarting"),
            ai_syncing=flag("isSyncing"),
            ai_sync_progress=sync_data.get("progress") if isinstance(sync_data, Mapping) else None,
            errors=tuple(errors),
        )


class EagleAdminClient(EagleClient):
    """Opt-in client for destructive library-wide operations."""

    def switch_library(self, library_path: str) -> dict[str, Any]:
        return self.request("library/switch", "POST", {"libraryPath": library_path})

    def remove_smart_folder(self, smart_folder_id: str) -> dict[str, Any]:
        return self.request("smartFolder/remove", "POST", {"id": smart_folder_id})

    def merge_tags(self, source: str, target: str) -> dict[str, Any]:
        return self.request("tag/merge", "POST", {"source": source, "target": target})

    def remove_tag_group(self, group_id: str) -> dict[str, Any]:
        return self.request("tagGroup/remove", "POST", {"id": group_id})


default_client = EagleClient()
