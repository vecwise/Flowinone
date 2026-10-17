"""Build a reviewable allowlist plan, then apply exactly that plan explicitly."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter
from pathlib import Path

from sqlalchemy import text

from src.flowinone.resource_library.canonical import normalize_resource_url
from src.flowinone.resource_library.importers import iter_chrome_json_bookmarks
from src.flowinone.resource_library.models import utc_now_text

from .identity import origin_key, record_dict, registry_exists, source_details, stable_id, write_origins


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def _rows(conn, sql: str) -> list[dict]:
    return [dict(row) for row in conn.execute(text(sql)).mappings()]


def read_bookmarks(path: Path) -> tuple[list[dict], str]:
    path = path.expanduser().resolve(strict=True)
    before = path.stat()
    raw = path.read_bytes()
    after = path.stat()
    if (before.st_ino, before.st_mtime_ns, before.st_size) != (after.st_ino, after.st_mtime_ns, after.st_size):
        raise ValueError("Bookmark file changed while reading; regenerate the preview")
    records = [record_dict(record) for record in iter_chrome_json_bookmarks(json.loads(raw))]
    for record in records:
        record["source_path"] = str(path)
    return records, hashlib.sha256(raw).hexdigest()


def build_plan(conn, bookmark_path: Path, baseline: dict, *, expected_count: int | None = None) -> dict:
    """No writes, migrations, thumbnail registration, or network calls."""
    source = source_details(bookmark_path)
    if str(Path(baseline["snapshot"]["bookmark_path"]).expanduser().resolve()) != source["bookmark_path"]:
        raise ValueError("Baseline belongs to another profile or storage")
    folders = baseline.get("hin_folders") or []
    if len(folders) != 1 or not folders[0].get("guid"):
        raise ValueError("Baseline must identify exactly one scope folder by GUID")
    source.update(scope_folder_guid=folders[0]["guid"], scope_folder_path=folders[0]["path"],
                  baseline_sha256=digest(baseline))
    selected = [r for r in baseline["bookmarks"] if r["classification"] == "matched"]
    if expected_count is not None and len(selected) != expected_count:
        raise ValueError(f"Expected {expected_count} approved candidates, found {len(selected)}")
    if len({r["guid"] for r in selected}) != len(selected):
        raise ValueError("Baseline has duplicate or missing GUIDs")
    records, bookmark_hash = read_bookmarks(bookmark_path)
    by_guid = {r["guid"]: r for r in records if r["guid"]}
    guids = Counter(r["guid"] for r in records if r["guid"])
    ids = Counter(r["chrome_id"] for r in records if r["chrome_id"])
    catalog = {r["id"]: r for r in _rows(conn, "SELECT * FROM catalog_items")}
    resources = {r["id"]: r for r in _rows(conn, "SELECT * FROM resources")}
    origins = _rows(conn, "SELECT * FROM catalog_origins")
    resource_origins = _rows(conn, "SELECT * FROM resource_origins ORDER BY id")
    projected_resources = {}
    projected_bookmarks = {}
    for origin in origins:
        if origin["source_kind"] == "resources" and not origin["stale"]:
            projected_resources.setdefault(origin["source_key"], set()).add(origin["catalog_item_id"])
        if origin["source_kind"] == "bookmarks":
            projected_bookmarks.setdefault(origin["source_key"], set()).add(origin["catalog_item_id"])
    projected_resource_origins = {}
    for origin in resource_origins:
        projected_resource_origins.setdefault(origin["dedupe_key"], set()).add(origin["resource_id"])
    registered = registry_exists(conn)
    existing = _rows(conn, "SELECT * FROM bookmark_bindings") if registered else []
    sources = _rows(conn, "SELECT * FROM bookmark_sources") if registered else []
    current_source = next((s for s in sources if s["bookmark_path"] == source["bookmark_path"]), None)
    if current_source and any(current_source[k] != source[k] for k in
                              ("id", "profile_path", "storage_scope", "scope_folder_guid", "baseline_sha256")):
        raise ValueError("Source already registered against another baseline or scope")
    bound = {(b["source_id"], b["guid"]): b for b in existing}
    planned, conflicts = [], []
    for old in selected:
        live = by_guid.get(old["guid"])
        reason = None
        cid, rid = old.get("catalog_id"), old.get("resource_id")
        c, r = catalog.get(cid), resources.get(rid)
        try:
            if live is None:
                reason = "bookmark_missing"
            elif str(uuid.UUID(live["guid"])) != live["guid"].lower() or guids[live["guid"]] != 1 or ids[live["chrome_id"]] != 1:
                reason = "source_identity_not_unique"
            elif normalize_resource_url(live["url"]) != old["canonical"]:
                reason = "bookmark_url_changed_since_baseline"
            elif c is None or r is None or c["is_deleted"]:
                reason = "original_row_missing_or_deleted"
            elif c["identity_key"] != "url:" + hashlib.sha256(old["canonical"].encode()).hexdigest() or normalize_resource_url(r["canonical_url"]) != old["canonical"]:
                reason = "original_row_url_changed"
            elif projected_resources.get(rid, set()) != {cid}:
                reason = "resource_catalog_mapping_changed"
        except (ValueError, TypeError):
            reason = "invalid_identity_or_url"
        previous = bound.get((source["id"], old["guid"]))
        if previous and (previous["catalog_item_id"] != cid or previous["resource_id"] != rid):
            reason = reason or "existing_binding_disagrees"
        if previous:
            key = origin_key(previous["id"])
            if projected_bookmarks.get(key, set()) != {cid} or projected_resource_origins.get(
                hashlib.sha256(key.encode()).hexdigest(), set()
            ) != {rid}:
                reason = reason or "bound_origin_missing_or_mismatched"
        if reason:
            conflicts.append({"guid": old["guid"], "title": old["title"], "reason": reason,
                              "catalog_item_id": cid, "resource_id": rid})
            continue
        planned.append({"id": stable_id(source["id"] + ":guid:" + live["guid"]),
                        "source_id": source["id"], "guid": live["guid"],
                        "chrome_id": live["chrome_id"] or None, "date_added": live["date_added"],
                        "catalog_item_id": cid, "resource_id": rid,
                        "applied_url": live["url"], "canonical_url": old["canonical"],
                        "observed_url": live["url"], "folder_path": live["folder_path"],
                        "title": live["title"], "already_bound": bool(previous)})
    cids = {b["catalog_item_id"] for b in planned}
    rids = {b["resource_id"] for b in planned}
    prefix = source["scope_folder_path"]
    legacy = []
    for origin in origins:
        if origin["source_kind"] != "bookmarks" or origin["catalog_item_id"] not in cids or origin["source_key"].startswith("chrome-bookmark:"):
            continue
        folder = json.loads(origin["metadata_json"] or "{}").get("folder_path", "")
        if folder == prefix or folder.startswith(prefix + " / "):
            legacy.append(origin["id"])
    protected = {
        "catalog": [catalog[i] for i in sorted(cids)],
        "resources": [resources[i] for i in sorted(rids)],
        "origins": sorted((o for o in origins if o["catalog_item_id"] in cids), key=lambda o: o["id"]),
        "resource_origins": resource_origins,
        "state": [r for r in _rows(conn, "SELECT * FROM item_user_state ORDER BY catalog_item_id") if r["catalog_item_id"] in cids],
        "catalog_tags": [r for r in _rows(conn, "SELECT * FROM catalog_item_tags ORDER BY catalog_item_id,tag_id,source") if r["catalog_item_id"] in cids],
        "resource_tags": [r for r in _rows(conn, "SELECT * FROM resource_tags ORDER BY resource_id,tag_id,source") if r["resource_id"] in rids],
        "bindings": sorted((b for b in existing if b["source_id"] == source["id"]), key=lambda b: b["id"]),
    }
    shared = Counter(b["catalog_item_id"] for b in planned)
    counts = {"selected": len(selected), "ready": len(planned), "new_bindings": sum(not b["already_bound"] for b in planned),
              "new_sources": int(not current_source),
              "new_catalog_origins": sum(not b["already_bound"] for b in planned),
              "new_resource_origins": sum(not b["already_bound"] for b in planned),
              "already_bound": sum(b["already_bound"] for b in planned), "conflicts": len(conflicts),
              "catalog_rows": len(cids), "resource_rows": len(rids),
              "shared_rows": sum(n > 1 for n in shared.values()),
              "legacy_origins_to_retire": len(legacy), "content_rows_to_create": 0,
              "content_rows_to_delete": 0, "content_urls_to_change": 0,
              "favorites_to_change": 0, "tags_to_change": 0,
              "excluded_baseline_bookmarks": len(baseline["bookmarks"]) - len(selected)}
    plan = {"version": 1, "source": source, "bookmark_sha256": bookmark_hash,
            "guard_sha256": digest(protected), "bindings": planned,
            "retire_legacy_origin_ids": sorted(legacy), "conflicts": conflicts,
            "counts": counts}
    plan["approval_digest"] = digest(plan)
    return plan


def apply_plan(conn, plan: dict, bookmark_path: Path, baseline: dict) -> dict:
    """Caller must use one write transaction and explicitly obtain approval."""
    if not registry_exists(conn):
        raise ValueError("Apply migration 0013_bookmark_bindings explicitly first")
    reviewed_payload = {key: value for key, value in plan.items() if key not in {"approval_digest", "snapshot"}}
    if digest(reviewed_payload) != plan["approval_digest"]:
        raise ValueError("Preview was edited; regenerate it before applying")
    fresh = build_plan(conn, bookmark_path, baseline, expected_count=plan["counts"]["selected"])
    if fresh["approval_digest"] != plan["approval_digest"]:
        raise ValueError("Preview is stale or edited; regenerate and review before applying")
    if fresh["conflicts"]:
        raise ValueError("Preview contains conflicts; no partial backfill will be applied")
    now = utc_now_text()
    source = {**plan["source"], "now": now}
    conn.execute(text("""INSERT INTO bookmark_sources
        (id,profile_path,storage_scope,bookmark_path,scope_folder_guid,scope_folder_path,baseline_sha256,created_at)
        VALUES (:id,:profile_path,:storage_scope,:bookmark_path,:scope_folder_guid,:scope_folder_path,:baseline_sha256,:now)
        ON CONFLICT(id) DO NOTHING"""), source)
    for binding in plan["bindings"]:
        if binding["already_bound"]:
            continue
        conn.execute(text("""INSERT INTO bookmark_bindings
            (id,source_id,guid,chrome_id,date_added,catalog_item_id,resource_id,applied_url,canonical_url,
             observed_url,folder_path,title,status,url_revision,last_seen_at)
            VALUES (:id,:source_id,:guid,:chrome_id,:date_added,:catalog_item_id,:resource_id,:applied_url,:canonical_url,
                    :observed_url,:folder_path,:title,'matched',0,:now)"""), {**binding, "now": now})
        write_origins(conn, binding, now=now)
    for origin in plan["retire_legacy_origin_ids"]:
        conn.execute(text("UPDATE catalog_origins SET stale=1 WHERE id=:id"), {"id": origin})
    # Chrome is external to the transaction; reject a changed source before
    # committing the reviewed snapshot's identities.
    if hashlib.sha256(bookmark_path.read_bytes()).hexdigest() != plan["bookmark_sha256"]:
        raise ValueError("Bookmark file changed during backfill; rolling back")
    return plan["counts"]
