"""Resolve approved source identities before URL deduplication.

The registry is opt-in: only an explicit, reviewed backfill creates bindings.
Unbound nodes inside a registered scope are held, never implicitly imported.
Shared content is updated as a group or held as a conflict, never split/merged.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter, defaultdict
from dataclasses import asdict, is_dataclass
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import text

from src.flowinone.resource_library.canonical import normalize_resource_url
from src.flowinone.resource_library.models import utc_now_text


def stable_id(value: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, "flowinone:" + value).hex


def registry_exists(conn) -> bool:
    return bool(conn.execute(text(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bookmark_bindings'"
    )).scalar())


def source_details(path: Path) -> dict:
    path = path.expanduser().resolve()
    storage = {"Bookmarks": "local", "AccountBookmarks": "account"}.get(path.name)
    if storage is None:
        raise ValueError("Use the configured live Bookmarks or AccountBookmarks JSON, not an export")
    profile = str(path.parent)
    return {"id": stable_id(profile + ":" + storage), "profile_path": profile,
            "storage_scope": storage, "bookmark_path": str(path)}


def record_dict(record) -> dict:
    value = asdict(record) if is_dataclass(record) else dict(record)
    value.setdefault("folder_path", value.get("folder", ""))
    return value


def origin_key(binding_id: str) -> str:
    return "chrome-bookmark:" + binding_id


def write_origins(conn, binding: dict, *, now: str) -> None:
    """Project one binding without touching content IDs, tags, or user state."""
    key = origin_key(binding["id"])
    old_metadata = conn.execute(text("""SELECT metadata_json FROM catalog_origins
        WHERE catalog_item_id=:id AND source_kind='bookmarks'
        ORDER BY CASE WHEN source_key=:key THEN 0 ELSE 1 END LIMIT 1"""),
                                {"id": binding["catalog_item_id"], "key": key}).scalar()
    existing_metadata = json.loads(old_metadata or "{}")
    thumbnail = conn.execute(text("SELECT thumbnail_ref FROM catalog_items WHERE id=:id"),
                             {"id": binding["catalog_item_id"]}).scalar()
    metadata = json.dumps({"folder_path": binding["folder_path"],
                           "bookmark_binding_id": binding["id"],
                           "thumbnail_ref": existing_metadata.get("thumbnail_ref") or thumbnail or "",
                           "thumbnail_sub_type": existing_metadata.get("thumbnail_sub_type") or ""}, ensure_ascii=False)
    conn.execute(text("""
        INSERT INTO catalog_origins
            (id,catalog_item_id,source_kind,source_key,detail_uri,original_url,
             source_path,metadata_json,last_seen_at,stale)
        VALUES (:id,:item,'bookmarks',:key,:url,:url,:folder,:metadata,:now,0)
        ON CONFLICT(source_kind,source_key) DO UPDATE SET
            detail_uri=excluded.detail_uri, original_url=excluded.original_url,
            source_path=excluded.source_path, metadata_json=excluded.metadata_json,
            last_seen_at=excluded.last_seen_at,stale=0
    """), {"id": stable_id(key + ":catalog-origin"), "item": binding["catalog_item_id"],
           "key": key, "url": binding["canonical_url"], "folder": binding["folder_path"],
           "metadata": metadata, "now": now})
    conn.execute(text("""
        INSERT INTO resource_origins
            (id,resource_id,source,source_key,source_title,folder_path,dedupe_key,
             captured_at,first_seen_at,last_seen_at)
        VALUES (:id,:resource,'chrome_json',:key,:title,:folder,:dedupe,:now,:now,:now)
        ON CONFLICT(dedupe_key) DO UPDATE SET
            source_title=excluded.source_title,folder_path=excluded.folder_path,
            last_seen_at=excluded.last_seen_at
    """), {"id": stable_id(key + ":resource-origin"), "resource": binding["resource_id"],
           "key": key, "title": binding["title"], "folder": binding["folder_path"],
           "dedupe": hashlib.sha256(key.encode()).hexdigest(), "now": now})


def _scoped(record: dict, source: dict) -> bool:
    guids = record.get("folder_guids") or ()
    if guids:
        return source["scope_folder_guid"] in guids
    # Legacy callers may not expose ancestor GUIDs. A known binding is still
    # matched by identity; this fallback only holds unbound nodes in the scope.
    folder = str(record.get("folder_path") or "")
    prefix = source["scope_folder_path"]
    return folder == prefix or folder.startswith(prefix + " / ")


def reconcile_bound_bookmarks(conn, records: list[dict], source_path: str, *, _mirror_dispatch: bool = True) -> dict:
    """Apply a full source observation atomically inside the caller transaction.

    Return record indexes handled by the registry (including held/excluded
    records), so callers cannot fall through to their old URL-based inserts.
    """
    result = {"handled": set(), "matched": 0, "conflicts": 0}
    if not source_path or not registry_exists(conn):
        return result
    source = conn.execute(text("SELECT * FROM bookmark_sources WHERE bookmark_path=:path"),
                          {"path": str(Path(source_path).expanduser().resolve())}).mappings().first()
    if source is None:
        if Path(source_path).name in {"Bookmarks", "AccountBookmarks"} and conn.execute(
            text("SELECT 1 FROM bookmark_sources LIMIT 1")
        ).scalar():
            raise ValueError("Chrome profile/storage changed; review the new source before synchronizing")
        return result
    source = dict(source)
    if _mirror_dispatch and source.get("sync_mode") == "mirror":
        from .mirror import reconcile_mirror
        return reconcile_mirror(conn, records, source)
    bindings = [dict(row) for row in conn.execute(text(
        "SELECT * FROM bookmark_bindings WHERE source_id=:source"
    ), {"source": source["id"]}).mappings()]
    by_guid = {b["guid"]: b for b in bindings if b["guid"]}
    by_id = {b["chrome_id"]: b for b in bindings if b["chrome_id"]}
    guid_counts = Counter(r.get("guid") for r in records if r.get("guid"))
    id_counts = Counter(r.get("chrome_id") for r in records if r.get("chrome_id"))
    observed: dict[str, dict] = {}
    issues: dict[str, str] = {}
    for index, record in enumerate(records):
        guid, numeric = record.get("guid"), record.get("chrome_id")
        binding = by_guid.get(guid) if guid else None
        fallback = by_id.get(numeric) if numeric else None
        if binding is None and fallback:
            # A changed GUID must not be mistaken for numeric ID reuse.
            if (not fallback["guid"] or not guid) and str(record.get("date_added") or "") == fallback["date_added"]:
                binding = fallback
            else:
                issues[fallback["id"]] = "guid_id_disagreement"
                binding = fallback
        if binding is not None:
            result["handled"].add(index)
            observed[binding["id"]] = record
            if guid:
                try:
                    if str(uuid.UUID(guid)) != guid.lower():
                        issues[binding["id"]] = "invalid_guid"
                except (ValueError, AttributeError):
                    issues[binding["id"]] = "invalid_guid"
            if guid_counts[guid] > 1 or id_counts[numeric] > 1:
                issues[binding["id"]] = "duplicate_source_identity"
            if fallback and fallback["id"] != binding["id"]:
                issues[binding["id"]] = issues[fallback["id"]] = "guid_id_disagreement"
        elif _scoped(record, source):
            result["handled"].add(index)  # Explicitly excluded from this rollout.

    groups = defaultdict(list)
    for binding in bindings:
        groups[(binding["catalog_item_id"], binding["resource_id"])].append(binding)
    now = utc_now_text()
    for (item_id, resource_id), group in groups.items():
        group_ids = {b["id"] for b in group}
        present = [b for b in group if b["id"] in observed]
        if not present:
            for b in group:
                conn.execute(text("UPDATE bookmark_bindings SET status='missing',conflict_reason=NULL WHERE id=:id"), b)
                conn.execute(text("UPDATE catalog_origins SET stale=1 WHERE source_kind='bookmarks' AND source_key=:key"),
                             {"key": origin_key(b["id"])})
            continue
        reason = next((issues[b["id"]] for b in group if b["id"] in issues), None)
        try:
            targets = {normalize_resource_url(observed[b["id"]]["url"]) for b in present}
        except ValueError:
            targets = set()
            reason = reason or "unsupported_url"
        target = next(iter(targets)) if len(targets) == 1 else None
        changed = bool(target and any(b["canonical_url"] != target for b in group))
        if len(targets) > 1 or (changed and len(present) != len(group)):
            reason = reason or "shared_row_divergence"
        item = conn.execute(text("SELECT * FROM catalog_items WHERE id=:id"), {"id": item_id}).mappings().one()
        resource = conn.execute(text("SELECT * FROM resources WHERE id=:id"), {"id": resource_id}).mappings().one()
        if item["is_deleted"]:
            reason = reason or "catalog_row_deleted"
        old_urls = {b["canonical_url"] for b in group}
        if resource["canonical_url"] not in old_urls or item["identity_key"] not in {
            "url:" + hashlib.sha256(u.encode()).hexdigest() for u in old_urls
        }:
            reason = reason or "stored_url_changed_outside_binding"
        if changed and not reason:
            # A row shared with bindings from another scope or resource is held.
            others = conn.execute(text("""SELECT id FROM bookmark_bindings
                WHERE catalog_item_id=:item OR resource_id=:resource"""),
                                  {"item": item_id, "resource": resource_id}).scalars()
            if any(other not in group_ids for other in others):
                reason = "shared_row_other_source"
            bound_identities = {(r.get("guid"), r.get("chrome_id")) for r in observed.values()}
            for r in records:
                try:
                    if normalize_resource_url(r["url"]) in old_urls and (r.get("guid"), r.get("chrome_id")) not in bound_identities:
                        reason = reason or "shared_row_unbound_bookmark"
                except ValueError:
                    pass
            target_hash = hashlib.sha256(target.encode()).hexdigest()
            owner = conn.execute(text("SELECT id FROM catalog_items WHERE identity_key=:key"),
                                 {"key": "url:" + target_hash}).scalar()
            owner_r = conn.execute(text("SELECT id FROM resources WHERE url_hash=:hash"),
                                   {"hash": target_hash}).scalar()
            if (owner and owner != item_id) or (owner_r and owner_r != resource_id):
                reason = reason or "target_url_already_owned"
            if conn.execute(text("SELECT 1 FROM processing_jobs WHERE resource_id=:id AND status='running' LIMIT 1"),
                            {"id": resource_id}).scalar():
                reason = reason or "resource_job_running"
            external = conn.execute(text("""SELECT source_kind,source_key FROM catalog_origins
                WHERE catalog_item_id=:id AND stale=0 AND source_kind!='bookmarks'"""), {"id": item_id}).mappings()
            if any(o["source_kind"] != "resources" or o["source_key"] != resource_id for o in external):
                reason = reason or "shared_row_other_origin"
            if source.get('sync_mode') == 'mirror' and conn.execute(text("""SELECT 1 FROM resource_origins
                WHERE resource_id=:id AND (source!='chrome_json' OR COALESCE(source_key,'') NOT LIKE 'chrome-bookmark:%') LIMIT 1"""), {'id':resource_id}).scalar():
                reason = reason or 'shared_row_other_origin'
        if reason:
            for b in group:
                record = observed.get(b["id"])
                conn.execute(text("""UPDATE bookmark_bindings SET status='conflict',conflict_reason=:reason,
                    observed_url=:url,last_seen_at=:now WHERE id=:id"""),
                             {"id": b["id"], "reason": reason, "url": record["url"] if record else b["observed_url"], "now": now})
                # A full Catalog scan marked origins stale before loading. Keep
                # the last accepted origin visible while a change is held.
                conn.execute(text("UPDATE catalog_origins SET stale=0 WHERE source_kind='bookmarks' AND source_key=:key"),
                             {"key": origin_key(b["id"])})
            result["conflicts"] += len(present)
            continue
        if changed:
            target_hash = hashlib.sha256(target.encode()).hexdigest()
            conn.execute(text("""UPDATE resources SET canonical_url=:url,url_hash=:hash,domain=:domain,
                enrichment_status='pending',updated_at=:now WHERE id=:id"""),
                         {"id": resource_id, "url": target, "hash": target_hash,
                          "domain": urlsplit(target).hostname, "now": now})
            conn.execute(text("""UPDATE catalog_items SET identity_key=:key,original_url=:url,
                primary_detail_uri=CASE WHEN primary_detail_uri LIKE 'http%' THEN :url ELSE primary_detail_uri END,
                indexed_at=:now WHERE id=:id"""),
                         {"id": item_id, "key": "url:" + target_hash, "url": target, "now": now})
            conn.execute(text("""UPDATE catalog_origins SET original_url=:url WHERE
                catalog_item_id=:item AND source_kind='resources' AND source_key=:resource"""),
                         {"url": target, "item": item_id, "resource": resource_id})
            conn.execute(text("""UPDATE processing_jobs SET status='cancelled',updated_at=:now
                WHERE resource_id=:id AND status IN ('pending','retry')"""), {"id": resource_id, "now": now})
        # Clear numeric IDs first to permit harmless Chrome ID renumbering.
        for b in present:
            if b["guid"]:
                conn.execute(text("UPDATE bookmark_bindings SET chrome_id=NULL WHERE id=:id"), b)
        for b in group:
            record = observed.get(b["id"])
            if record is None:
                conn.execute(text("UPDATE bookmark_bindings SET status='missing' WHERE id=:id"), b)
                conn.execute(text("UPDATE catalog_origins SET stale=1 WHERE source_kind='bookmarks' AND source_key=:key"),
                             {"key": origin_key(b["id"])})
                continue
            revision = b["url_revision"] + int(record["url"] != b["applied_url"])
            if revision != b["url_revision"]:
                conn.execute(text("""INSERT INTO bookmark_url_history
                    (id,binding_id,old_url,new_url,revision,changed_at) VALUES (:id,:binding,:old,:new,:revision,:now)"""),
                             {"id": uuid.uuid4().hex, "binding": b["id"], "old": b["applied_url"],
                              "new": record["url"], "revision": revision, "now": now})
            b.update(applied_url=record["url"], observed_url=record["url"], canonical_url=target,
                     folder_path=record["folder_path"], title=record["title"],
                     guid=record.get("guid") or b["guid"], chrome_id=record.get("chrome_id") or None,
                     url_revision=revision, now=now)
            conn.execute(text("""UPDATE bookmark_bindings SET guid=:guid,chrome_id=:chrome_id,
                applied_url=:applied_url,canonical_url=:canonical_url,observed_url=:observed_url,
                folder_path=:folder_path,title=:title,status='matched',conflict_reason=NULL,
                url_revision=:url_revision,last_seen_at=:now WHERE id=:id"""), b)
            write_origins(conn, b, now=now)
            result["matched"] += 1
    return result
