"""Choose safe source-specific launch targets for Catalog results."""

from __future__ import annotations

from .browse import CatalogService
from .query import CatalogQuery

SOURCE_LABELS = {
    "local": "本機",
    "eagle": "EAGLE",
    "bookmarks": "書籤",
    "resources": "Resources",
}
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
        visible_items.append(item)
    return visible_items
