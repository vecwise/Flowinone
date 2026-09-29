"""Choose safe source-specific launch targets for Catalog results."""

from __future__ import annotations

from urllib.parse import urlsplit

from .browse import CatalogService
from .query import CatalogQuery

SOURCE_LABELS = {
    "local": "本機",
    "eagle": "EAGLE",
    "bookmarks": "書籤",
    "resources": "Resources",
}


def _card_description(value: object, folder_path: str = "") -> str:
    description = " ".join(str(value or "").split())
    return description[:180] if description and description != folder_path else ""


def visible_items(service: CatalogService, payload: dict, query: CatalogQuery) -> list[dict]:
    """Decorate canonical rows with a source-specific, usable launch target."""
    visible_items = []
    origins_by_item = service.get_origins_for_items(
        (item["id"] for item in payload["items"]), folder=query.folder
    )
    for item in payload["items"]:
        available = origins_by_item.get(item["id"], {})
        origins = []
        for source in query.sources:
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
        item["thumbnail_ref"] = metadata.get("thumbnail_ref") or item.get("thumbnail_ref")
        item["folder_path"] = metadata.get("folder_path") or ""
        item["target_blank"] = source == "bookmarks"
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
    return visible_items
