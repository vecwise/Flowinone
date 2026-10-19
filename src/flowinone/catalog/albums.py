"""Project direct Eagle image folders as searchable album items."""

import hashlib
import json
from urllib.parse import urlencode

from sqlalchemy import text

from src.file_handler.eagle_integration import _extract_folder_ids
from src.flowinone.ordering import natural_sort_key

from .fts import CatalogSearchIndex
from .query import _json


def album_source_key(library, folder_id):
    return f"folder:{library}:{folder_id}"


def album_content_uri(library, folder_id):
    return "/navigator/?" + urlencode({
        "scope": "all", "source": "eagle", "media": "image", "sort": "page_order",
        "target": f"eagle_folder:{library}:{folder_id}",
    })


def sync_albums(sync, conn, *, folders, library, scan_id, full_rescan=False):
    """Use one local image scan, never one Eagle request per folder.

    Run only after the complete asset snapshot has been verified. Membership
    stays in asset origins; folders without direct images get no album item.
    """
    groups = {}
    rows = conn.execute(text("""
        SELECT i.id,i.title,i.thumbnail_ref,i.captured_at,o.source_key,o.metadata_json,
               (SELECT GROUP_CONCAT(name,char(31)) FROM (
                   SELECT DISTINCT t.name FROM catalog_tags t JOIN catalog_item_tags it
                   ON it.tag_id=t.id WHERE it.catalog_item_id=i.id ORDER BY t.name
               )) AS tags
        FROM catalog_origins o JOIN catalog_items i ON i.id=o.catalog_item_id
        WHERE o.source_kind='eagle' AND o.last_seen_at=:scan AND o.stale=0 AND i.item_type='image'
    """), {"scan": scan_id}).mappings()
    for row in rows:
        metadata = _json(row["metadata_json"], {})
        for folder_id in _extract_folder_ids(metadata.get("folders")):
            if folder_id not in folders:
                continue
            group = groups.setdefault(folder_id, {"count": 0, "cover": row, "tags": set(), "captured": row["captured_at"]})
            group["count"] += 1
            group["tags"].update(filter(None, str(row["tags"] or "").split("\x1f")))
            if (natural_sort_key(row["title"]), row["source_key"]) < (natural_sort_key(group["cover"]["title"]), group["cover"]["source_key"]):
                group["cover"] = row
            group["captured"] = min(group["captured"] or "", row["captured_at"] or "")

    existing = {row["source_key"]: dict(row) for row in conn.execute(text(
        "SELECT source_key,catalog_item_id,metadata_json FROM catalog_origins "
        "WHERE source_kind='eagle' AND source_key LIKE 'folder:%'"
    )).mappings()}
    index = None
    for folder_id, group in groups.items():
        folder, cover = folders[folder_id], group["cover"]
        title = str(folder.get("name") or folder_id)
        description = str(folder.get("description") or "")
        thumbnail = cover["thumbnail_ref"] or "/static/default_thumbnail.svg"
        metadata = {
            "source_object": "folder", "library_signature": library, "folder_id": folder_id,
            "parent_id": folder.get("parent_id"), "image_count": group["count"],
            "cover_item_id": cover["id"], "thumbnail_ref": thumbnail,
        }
        fingerprint = hashlib.sha256(json.dumps(
            [title, description, metadata, sorted(group["tags"])], ensure_ascii=False, sort_keys=True,
        ).encode()).hexdigest()
        metadata["source_fingerprint"] = fingerprint
        source_key = album_source_key(library, folder_id)
        previous = existing.get(source_key)
        if not full_rescan and previous and _json(previous["metadata_json"], {}).get("source_fingerprint") == fingerprint:
            conn.execute(text("UPDATE catalog_origins SET stale=0,last_seen_at=:scan WHERE source_kind='eagle' AND source_key=:key"), {"scan": scan_id, "key": source_key})
            conn.execute(text("UPDATE catalog_items SET is_deleted=0,availability='available' WHERE id=:id"), {"id": previous["catalog_item_id"]})
            continue
        if previous:
            conn.execute(text("DELETE FROM catalog_item_tags WHERE catalog_item_id=:id AND source='eagle_folder_member'"), {"id": previous["catalog_item_id"]})
        if index is None:
            index = CatalogSearchIndex(conn)
        sync._upsert(
            conn, identity_key=f"eagle-folder:{library}:{folder_id}", source_kind="eagle",
            source_key=source_key, item_type="album", title=title, description=description,
            thumbnail_ref=thumbnail, detail_uri=album_content_uri(library, folder_id),
            tags=[(tag, "eagle_folder_member") for tag in sorted(group["tags"])],
            captured_at=group["captured"], metadata=metadata, prefer=True,
            seen_at=scan_id, content_fingerprint=fingerprint, search_index=index,
        )
    return len(groups)
