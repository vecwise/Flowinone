"""Explicitly reviewed URL-owner consolidations; never called by normal sync.

Only disjoint pairs with no user state, content jobs, independent origins or
manual references are supported. A changed preview fails the entire batch.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from sqlalchemy import text

from .backfill import digest, read_bookmarks
from .identity import origin_key
from .mirror import _refresh_titles_tags
from src.flowinone.resource_library.canonical import hash_url, normalize_resource_url
from src.flowinone.resource_library.models import utc_now_text


def _rows(conn, sql, params=None):
    return [dict(r) for r in conn.execute(text(sql), params or {}).mappings()]


def _capture(conn, cid, rid):
    p = {"cid": cid, "rid": rid}
    queries = {
        "catalog": "SELECT * FROM catalog_items WHERE id=:cid",
        "resource": "SELECT * FROM resources WHERE id=:rid",
        "bindings": "SELECT * FROM bookmark_bindings WHERE catalog_item_id=:cid OR resource_id=:rid",
        "nodes": "SELECT * FROM chrome_bookmark_nodes WHERE catalog_item_id=:cid OR resource_id=:rid",
        "catalog_origins": "SELECT * FROM catalog_origins WHERE catalog_item_id=:cid OR (source_kind='resources' AND source_key=:rid)",
        "resource_origins": "SELECT * FROM resource_origins WHERE resource_id=:rid",
        "catalog_tags": "SELECT it.*,t.name FROM catalog_item_tags it JOIN catalog_tags t ON t.id=it.tag_id WHERE catalog_item_id=:cid",
        "resource_tags": "SELECT it.*,t.name FROM resource_tags it JOIN tags t ON t.id=it.tag_id WHERE resource_id=:rid",
        "state": "SELECT * FROM item_user_state WHERE catalog_item_id=:cid",
        "events": "SELECT * FROM catalog_events WHERE catalog_item_id=:cid",
        "artifacts": "SELECT * FROM catalog_artifacts WHERE catalog_item_id=:cid",
        "people": "SELECT * FROM item_people WHERE catalog_item_id=:cid",
        "sessions": "SELECT * FROM browse_sessions WHERE focused_item_id=:cid",
        "reviews": "SELECT * FROM catalog_duplicate_reviews WHERE canonical_item_id=:cid",
        "ai": "SELECT * FROM ai_artifacts WHERE resource_id=:rid",
        "jobs": "SELECT * FROM processing_jobs WHERE resource_id=:rid",
        "contents": "SELECT * FROM resource_contents WHERE resource_id=:rid",
        "relations": "SELECT * FROM item_relations WHERE source_item_id=:cid OR target_item_id=:cid",
        "history": "SELECT h.* FROM bookmark_url_history h JOIN bookmark_bindings b ON b.id=h.binding_id WHERE b.catalog_item_id=:cid OR b.resource_id=:rid",
        "catalog_fts": "SELECT * FROM catalog_fts WHERE catalog_item_id=:cid",
        "resource_fts": "SELECT * FROM resource_fts WHERE resource_id=:rid",
    }
    return {key: sorted(_rows(conn, sql, p), key=digest) for key, sql in queries.items()}


def _check_member(member, cid, rid, record):
    if len(member["catalog"]) != 1 or len(member["resource"]) != 1 or len(member["bindings"]) != 1 or len(member["nodes"]) != 1:
        raise ValueError("Each reviewed member must have exactly one row pair and Chrome binding")
    b = member["bindings"][0]
    if b["catalog_item_id"] != cid or b["resource_id"] != rid:
        raise ValueError("Catalog/Resource pairing changed")
    if not record or any(str(record.get(k) or "") != str(b.get(k) or "") for k in ("guid", "chrome_id", "date_added", "title", "folder_path")):
        raise ValueError("Chrome identity/title/folder changed; review again")
    node = member["nodes"][0]
    if any(node[k] != record[k] for k in ("guid", "chrome_id", "url", "title", "folder_path")) or node["unsupported_reason"]:
        raise ValueError("Chrome inventory has not caught up; synchronize before reviewing")
    if b["observed_url"] != record["url"] or member["catalog"][0]["is_deleted"]:
        raise ValueError("Observed URL or row availability changed")
    if max((h["revision"] for h in member["history"]), default=0) != b["url_revision"]:
        raise ValueError("URL history and binding revision disagree")
    if any(member[k] for k in ("state", "events", "artifacts", "people", "sessions", "reviews", "ai", "jobs", "contents")):
        raise ValueError("User state, references or resource work requires a new review")
    if any(r["relation_type"] != "metadata_similar" or r["algorithm_version"] != "metadata-v1" for r in member["relations"]):
        raise ValueError("Manual relations require a new review")
    if any(r.get("extracted_text") for k in ("catalog_fts", "resource_fts") for r in member[k]):
        raise ValueError("Extracted content requires a new review")
    key = origin_key(b["id"])
    if len(member["catalog_origins"]) != 2 or {
        (o["catalog_item_id"], o["source_kind"], o["source_key"], o["stale"]) for o in member["catalog_origins"]
    } != {(cid, "bookmarks", key, 0), (cid, "resources", rid, 0)}:
        raise ValueError("Independent or extra Catalog origins require a new review")
    if len(member["resource_origins"]) != 1 or any(o["source"] != "chrome_json" or o["source_key"] != key for o in member["resource_origins"]):
        raise ValueError("Independent or extra Resource origins require a new review")
    resource = member["resource"][0]
    if any(resource[k] for k in ("description", "author", "language", "published_at", "summary_one_line", "summary_short", "summary_structured_json", "why_this_matters", "content_hash")):
        raise ValueError("Enriched Resource content requires a new review")


def _tag_set(tags):
    return {(t.get("tag_id", t.get("id")), t["name"], t["source"]) for t in tags}


def build_plan(conn, bookmarks: Path, selection: dict) -> dict:
    """Read-only, binding-scoped plan for an explicit reviewed selection."""
    records, sha = read_bookmarks(bookmarks)
    if len({r["guid"] for r in records}) != len(records) or len({r["chrome_id"] for r in records}) != len(records):
        raise ValueError("Duplicate Chrome identities")
    by_guid = {r["guid"]: r for r in records}
    source = _rows(conn, "SELECT * FROM bookmark_sources WHERE bookmark_path=:path AND sync_mode='mirror'",
                   {"path": str(bookmarks.expanduser().resolve())})
    if len(source) != 1:
        raise ValueError("A registered Chrome mirror is required")
    cases, used_c, used_r, used_b = [], set(), set(), set()
    if not selection.get("cases"):
        raise ValueError("A nonempty explicit selection is required")
    for chosen in selection["cases"]:
        old_c, old_r = chosen["retire_catalog_item_id"], chosen["retire_resource_id"]
        keep_c, keep_r = chosen["keep_catalog_item_id"], chosen["keep_resource_id"]
        if old_c == keep_c or old_r == keep_r or used_c & {old_c, keep_c} or used_r & {old_r, keep_r}:
            raise ValueError("Overlapping/shared pairs require a separate review")
        old, keep = _capture(conn, old_c, old_r), _capture(conn, keep_c, keep_r)
        for member, cid, rid in ((old, old_c, old_r), (keep, keep_c, keep_r)):
            b = member["bindings"][0] if member["bindings"] else {}
            _check_member(member, cid, rid, by_guid.get(b.get("guid")))
            if b["source_id"] != source[0]["id"]:
                raise ValueError("The selected pair belongs to a different source")
        ob, kb = old["bindings"][0], keep["bindings"][0]
        if set(chosen["keep_bindings"]) != {ob["id"], kb["id"]} or chosen["rebind_binding_id"] != ob["id"] or used_b & {ob["id"], kb["id"]}:
            raise ValueError("Reviewed binding identities changed")
        if set(chosen["keep_guids"]) != {ob["guid"], kb["guid"]} or set(chosen["keep_chrome_ids"]) != {ob["chrome_id"], kb["chrome_id"]}:
            raise ValueError("Reviewed Chrome identities changed")
        if ob["status"] != "conflict" or ob["conflict_reason"] != "target_url_already_owned" or kb["status"] != "matched":
            raise ValueError("This is no longer the reviewed URL-owner conflict")
        target = chosen["canonical_url_after"]
        for b in (ob, kb):
            if by_guid[b["guid"]]["url"] != chosen["raw_chrome_urls_unchanged"][b["guid"]] or normalize_resource_url(by_guid[b["guid"]]["url"]) != target:
                raise ValueError("Reviewed Chrome destination changed")
        if ob["applied_url"] != chosen["old_applied_url"] or kb["canonical_url"] != target:
            raise ValueError("Accepted URL changed")
        if ob["canonical_url"] == target or normalize_resource_url(ob["applied_url"]) != ob["canonical_url"]:
            raise ValueError("The reviewed old URL no longer identifies a distinct owner")
        for member, canonical in ((old, ob["canonical_url"]), (keep, target)):
            if member["resource"][0]["canonical_url"] != canonical or member["resource"][0]["url_hash"] != hash_url(canonical) or member["catalog"][0]["identity_key"] != "url:" + hash_url(canonical):
                raise ValueError("Stored URL ownership changed")
        folders = sorted({ob["folder_path"], kb["folder_path"]})
        if folders != sorted(chosen["current_folder_paths_after"]):
            raise ValueError("Reviewed folders changed")
        for kind in ("catalog", "resource"):
            if _tag_set(old[kind + "_tags"] + keep[kind + "_tags"]) != _tag_set(chosen[kind + "_tags_after"]):
                raise ValueError("Reviewed tags changed")
        descriptions = list(dict.fromkeys(x for x in (keep["catalog"][0]["description"], old["catalog"][0]["description"]) if x))
        if descriptions != chosen["description_nonempty_to_preserve"]:
            raise ValueError("Reviewed descriptions changed")
        metadata = json.loads(keep["catalog"][0]["metadata_json"] or "{}")
        for key, value in json.loads(old["catalog"][0]["metadata_json"] or "{}").items():
            if key in metadata and metadata[key] != value:
                raise ValueError("Catalog metadata disagreement requires review")
            metadata[key] = value
        metadata["chrome_primary_guid"] = kb["guid"]
        cases.append({"chosen": chosen, "old": old, "keep": keep, "metadata_after": metadata,
                      "description_after": "\n\n".join(descriptions) or None})
        used_c.update((old_c, keep_c)); used_r.update((old_r, keep_r)); used_b.update((ob["id"], kb["id"]))
    plan = {"format_version": 1, "source_id": source[0]["id"], "bookmark_path": str(bookmarks.resolve()),
            "chrome_sha256": sha, "selection": selection, "cases": cases}
    plan["approval_digest"] = digest(plan)
    return plan


def apply_plan(conn, plan: dict, bookmarks: Path) -> dict:
    """Apply atomically inside a caller-owned BEGIN IMMEDIATE transaction."""
    payload = {k: v for k, v in plan.items() if k != "approval_digest"}
    if digest(payload) != plan["approval_digest"]:
        raise ValueError("The consolidation plan was edited")
    fresh = build_plan(conn, bookmarks, plan["selection"])
    if fresh["approval_digest"] != plan["approval_digest"]:
        raise ValueError("The consolidation plan is stale; no changes applied")
    now = utc_now_text()
    for case in plan["cases"]:
        old, keep, chosen = case["old"], case["keep"], case["chosen"]
        ob, kb = old["bindings"][0], keep["bindings"][0]
        p = {"old_c": chosen["retire_catalog_item_id"], "old_r": chosen["retire_resource_id"],
             "keep_c": chosen["keep_catalog_item_id"], "keep_r": chosen["keep_resource_id"]}
        for table, column, old_id, keep_id in (("catalog_item_tags", "catalog_item_id", p["old_c"], p["keep_c"]),
                                                ("resource_tags", "resource_id", p["old_r"], p["keep_r"])):
            conn.execute(text(f"""INSERT INTO {table} ({column},tag_id,source,confidence,created_at)
                SELECT :keep,tag_id,source,confidence,created_at FROM {table} WHERE {column}=:old
                ON CONFLICT({column},tag_id,source) DO UPDATE SET
                    confidence=MAX(COALESCE(confidence,0),COALESCE(excluded.confidence,0)),
                    created_at=MIN(created_at,excluded.created_at)"""), {"keep": keep_id, "old": old_id})
        conn.execute(text("UPDATE catalog_origins SET catalog_item_id=:keep_c WHERE catalog_item_id=:old_c AND source_kind='bookmarks'"), p)
        conn.execute(text("UPDATE resource_origins SET resource_id=:keep_r WHERE resource_id=:old_r"), p)
        # Keep the original origin IDs, first-seen dates, source title and raw URL.
        moved_origin = next(o for o in old["catalog_origins"] if o["source_kind"] == "bookmarks")
        origin_meta = json.loads(moved_origin["metadata_json"] or "{}")
        origin_meta["consolidated_from"] = {"catalog_item_id": p["old_c"], "resource_id": p["old_r"],
                                             "resource_original_url": old["resource"][0]["original_url"]}
        conn.execute(text("UPDATE catalog_origins SET metadata_json=:meta WHERE id=:id"),
                     {"meta": json.dumps(origin_meta, ensure_ascii=False), "id": moved_origin["id"]})
        revision = ob["url_revision"] + 1
        raw = chosen["raw_chrome_urls_unchanged"][ob["guid"]]
        conn.execute(text("""INSERT INTO bookmark_url_history(id,binding_id,old_url,new_url,revision,changed_at)
            VALUES(:id,:binding,:old,:new,:revision,:now)"""),
                     {"id": uuid.uuid4().hex, "binding": ob["id"], "old": ob["applied_url"], "new": raw, "revision": revision, "now": now})
        conn.execute(text("""UPDATE bookmark_bindings SET catalog_item_id=:keep_c,resource_id=:keep_r,
            applied_url=:raw,canonical_url=:canonical,observed_url=:raw,status='matched',conflict_reason=NULL,
            url_revision=:revision,last_seen_at=:now WHERE id=:id"""),
                     {**p, "raw": raw, "canonical": chosen["canonical_url_after"], "revision": revision, "now": now, "id": ob["id"]})
        conn.execute(text("UPDATE chrome_bookmark_nodes SET catalog_item_id=:keep_c,resource_id=:keep_r WHERE catalog_item_id=:old_c AND resource_id=:old_r"), p)
        conn.execute(text("""UPDATE catalog_items SET metadata_json=:meta,description=:description,
            indexed_at=:now WHERE id=:id"""), {"id": p["keep_c"], "meta": json.dumps(case["metadata_after"], ensure_ascii=False),
                                              "description": case["description_after"], "now": now})
        conn.execute(text("DELETE FROM catalog_origins WHERE catalog_item_id=:old_c AND source_kind='resources' AND source_key=:old_r"), p)
        for table, field, key in (("catalog_fts", "catalog_item_id", "old_c"), ("resource_fts", "resource_id", "old_r"),
                                  ("resources", "id", "old_r"), ("catalog_items", "id", "old_c")):
            conn.execute(text(f"DELETE FROM {table} WHERE {field}=:id"), {"id": p[key]})
    # Rebuild folder-derived tags from every surviving placement, retaining all
    # resource-projected/user tag unions. Both binding IDs and histories survive.
    records, sha = read_bookmarks(bookmarks)
    bindings = []
    for case in plan["cases"]:
        bindings.extend(_rows(conn, "SELECT * FROM bookmark_bindings WHERE catalog_item_id=:id",
                              {"id": case["chosen"]["keep_catalog_item_id"]}))
    _refresh_titles_tags(conn, bindings, records, now)
    if sha != plan["chrome_sha256"] or read_bookmarks(bookmarks)[1] != sha:
        raise ValueError("Chrome changed during consolidation; rolling back")
    if conn.exec_driver_sql("PRAGMA foreign_key_check").first():
        raise ValueError("Consolidation failed foreign-key validation")
    return {"consolidated_pairs": len(plan["cases"]), "retired_catalog_rows": len(plan["cases"]),
            "retired_resource_rows": len(plan["cases"]), "preserved_bindings": len(bindings),
            "appended_url_history": len(plan["cases"]), "approval_digest": plan["approval_digest"]}
