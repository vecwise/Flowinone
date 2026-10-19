"""Choose safe source-specific launch targets for Catalog results."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlencode

from flask import has_request_context, request

from src.file_handler.thumbnails.store import get_thumbnail_store
from src.file_handler.thumbnails.retry import resolve_bookmark_ids

from .browse import CatalogService
from .query import CatalogQuery

SOURCE_LABELS = {
    "local": "本機",
    "eagle": "EAGLE",
    "bookmarks": "Chrome 書籤",
    "resources": "新增網址",
}
BOOKMARK_THUMBNAIL_ROUTE = re.compile(r"^/api/bookmark-thumbnails/([0-9a-f]{40})/image$")


def _card_description(value: object, folder_path: str = "") -> str:
    description = " ".join(str(value or "").split())
    return description[:180] if description and description != folder_path else ""


def visible_items(service: CatalogService, payload: dict, query: CatalogQuery) -> list[dict]:
    """Decorate canonical rows with a source-specific, usable launch target."""
    visible_items = []
    origins_by_item = service.get_origins_for_items(
        (item["id"] for item in payload["items"]), folder=query.folder
    )
    bookmark_thumbnails: list[tuple[dict, str]] = []
    for item in payload["items"]:
        available = origins_by_item.get(item["id"], {})
        origins = []
        preferred_sources = list(query.sources)
        if (
            query.q
            and (item.get("match_reason") or {}).get("field") == "content"
            and "resources" in preferred_sources
            and len(preferred_sources) > 1
        ):
            preferred_sources.remove("resources")
            preferred_sources.insert(0, "resources")
        for source in preferred_sources:
            origin = available.get(source)
            if origin:
                origins.append(origin)
        if not origins:
            for source in item.get("sources") or available:
                origin = available.get(source)
                if origin:
                    origins.append(origin)
        chosen = origins[0] if origins else None
        if not chosen:
            continue
        metadata = chosen.get("metadata") or {}
        source = chosen["source_kind"]
        item["launch_source"] = source
        item["launch_source_label"] = SOURCE_LABELS[source]
        item["launch_uri"] = (
            chosen.get("original_url") or chosen.get("detail_uri")
            if source == "bookmarks"
            else chosen.get("detail_uri")
        )
        item["original_uri"] = chosen.get("original_url") if source in {"bookmarks", "resources"} else None
        item["resource_detail_uri"] = (available.get("resources") or {}).get("detail_uri")
        item["thumbnail_ref"] = metadata.get("thumbnail_ref") or item.get("thumbnail_ref")
        item["thumbnail_is_fallback"] = source == "bookmarks" and not item["thumbnail_ref"]
        thumbnail_id = None
        if source == "bookmarks" and item["thumbnail_ref"]:
            match = BOOKMARK_THUMBNAIL_ROUTE.fullmatch(str(item["thumbnail_ref"]))
            if match:
                thumbnail_id = match.group(1)
        if source == 'bookmarks' and not thumbnail_id:
            ids = resolve_bookmark_ids(get_thumbnail_store(), [{'url': item['launch_uri'], 'title': item['title'],
                'folder_path': metadata.get('folder_path') or ''}])
            if ids:
                thumbnail_id = ids[0]
        if thumbnail_id:
            bookmark_thumbnails.append((item, thumbnail_id))
        item["folder_path"] = metadata.get("folder_path") or ""
        item["target_blank"] = source == "bookmarks"
        item["is_eagle_album"] = source == "eagle" and metadata.get("source_object") == "folder" and item["item_type"] == "album"
        if item["is_eagle_album"]:
            item["image_count"] = int(metadata.get("image_count") or 0)
            pairs = [("scope", "all"), *(("source", value) for value in query.sources),
                     ("target", f"eagle_folder:{metadata['library_signature']}:{metadata['folder_id']}"),
                     ("media", "image"), ("sort", "page_order")]
            if has_request_context():
                pairs.extend((key, request.args[key]) for key in ("view", "shape") if request.args.get(key))
                if request.path.rstrip("/") == "/navigator":
                    outer = [(key, value) for key, value in request.args.items(multi=True) if key not in {"return_to", "resume_scroll"}]
                    return_to = request.path + ("?" + urlencode(outer) if outer else "") + f"#item-{item['id']}"
                    if len(return_to) <= 4000:
                        pairs.append(("return_to", return_to))
            item["launch_uri"] = "/navigator/?" + urlencode(pairs)
        item["card_description"] = _card_description(item.get("description"), item["folder_path"])
        item["site_domain"] = ""
        item["site_mark"] = ""
        if source == "bookmarks":
            try:
                domain = (urlsplit(item["launch_uri"] or "").hostname or "").lower().removeprefix("www.")
            except ValueError:
                domain = ""
            item["site_domain"] = domain
            item["site_mark"] = domain[:2].upper() if domain else "↗"
            collection_path = item["folder_path"]
        elif source == "local":
            relative = str(chosen.get("source_path") or "").replace("\\", "/")
            collection_path = relative.rpartition("/")[0]
        else:
            collection_path = ""
        item["collection_path"] = collection_path
        item["collection_label"] = collection_path.rsplit(" / ", 1)[-1].rsplit("/", 1)[-1] if collection_path else ""
        folder_tags = {part.strip().casefold() for part in item["folder_path"].split(" / ") if part.strip()}
        item["card_tags"] = [
            tag for tag in item.get("tags") or []
            if source != "bookmarks" or tag.casefold() not in folder_tags
        ][:2]
        visible_items.append(item)
    if bookmark_thumbnails:
        statuses = {status['id']: status for status in get_thumbnail_store().get_statuses(
            media_id for _, media_id in bookmark_thumbnails
        )}
        for item, media_id in bookmark_thumbnails:
            state = statuses[media_id]
            item['thumbnail_id'] = media_id
            item['thumbnail_status'] = state['status']
            item['thumbnail_provider'] = state.get('provider')
            item["thumbnail_is_fallback"] = not state.get('thumbnail_url') or state.get('provider') == 'site-tile'
            if state.get('thumbnail_url'):
                item['thumbnail_ref'] = state['thumbnail_url']
    return visible_items


def bookmark_source_health(path: Path, sync_state: dict, indexed_count: int) -> dict[str, str | int]:
    """Describe whether the visible bookmark index still reflects its source."""
    raw_synced_at = sync_state.get("synced_at")
    synced_at = None
    if raw_synced_at:
        try:
            synced_at = datetime.fromisoformat(str(raw_synced_at).replace("Z", "+00:00"))
            if synced_at.tzinfo is None:
                synced_at = synced_at.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    display_time = synced_at.astimezone().strftime("%Y-%m-%d %H:%M") if synced_at else "尚未同步"
    count = indexed_count
    try:
        source_mtime = path.stat().st_mtime
    except OSError:
        state = "unavailable"
    else:
        if sync_state.get("status") == "failed":
            state = "failed"
        elif not synced_at:
            state = "never_synced"
        elif source_mtime > synced_at.timestamp() + 1:
            state = "changed"
        else:
            state = "current"
    return {"state": state, "count": count, "synced_at": display_time}
