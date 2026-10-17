"""Identity/backfill contracts use temporary databases and bookmark files only."""

from __future__ import annotations

import copy
import json
import sqlite3
import uuid
from dataclasses import replace
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from scripts.bookmark_bindings import preview
from src.file_handler import chrome_bookmarks
from src.flowinone.bookmarks.backfill import apply_plan, build_plan, read_bookmarks
from src.flowinone.bookmarks.identity import reconcile_bound_bookmarks
from src.flowinone.bookmarks.snapshot import read_snapshot
from src.flowinone.catalog.browse import CatalogService
from src.flowinone.catalog.sync import CatalogSyncService
from src.flowinone.resource_library.database import ResourceDatabase
from src.flowinone.resource_library.importers import load_bookmarks
from src.flowinone.resource_library.service import ResourceService


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    def seed(urls=("https://example.com/old",), selected=None):
        path = tmp_path / "Default" / "AccountBookmarks"
        path.parent.mkdir(exist_ok=True)
        folder_guid = str(uuid.uuid4())
        nodes = [{"type": "url", "id": str(i + 10), "guid": str(uuid.uuid4()),
                  "date_added": "13300000000000000", "name": f"Bookmark {i}", "url": url}
                 for i, url in enumerate(urls)]
        payload = {"roots": {"bookmark_bar": {"type": "folder", "name": "Bookmarks Bar", "guid": str(uuid.uuid4()),
                    "children": [{"type": "folder", "name": "hin", "guid": folder_guid, "children": nodes}]}}}
        path.write_text(json.dumps(payload))
        monkeypatch.setattr(chrome_bookmarks, "CHROME_BOOKMARK_PATH", str(path))
        monkeypatch.setattr("src.flowinone.catalog.sync.lookup_thumbnail_for_bookmark",
                            lambda *a, **kw: SimpleNamespace(route="/thumbnail/old", sub_type="metadata"))
        db = ResourceDatabase(tmp_path / "library.db")
        service = ResourceService(db, link_thumbnail_cache=False)
        service.import_records(load_bookmarks(path), enqueue=False)
        sync = CatalogSyncService(db)
        assert sync.sync(("resources", "bookmarks"))["bookmarks"]["status"] == "complete"
        baseline = {"snapshot": {"bookmark_path": str(path)},
                    "hin_folders": [{"guid": folder_guid, "path": "Bookmarks Bar / hin"}], "bookmarks": []}
        with db.engine.connect() as conn:
            for index, node in enumerate(nodes):
                identity, canonical = sync._url_identity(node["url"])
                cid = conn.execute(text("SELECT id FROM catalog_items WHERE identity_key=:key"), {"key": identity}).scalar_one()
                rid = conn.execute(text("SELECT id FROM resources WHERE canonical_url=:url"), {"url": canonical}).scalar_one()
                baseline["bookmarks"].append({"classification": "matched" if selected is None or index in selected else "unmatched",
                    "guid": node["guid"], "title": node["name"], "canonical": canonical, "catalog_id": cid, "resource_id": rid})
        first = baseline["bookmarks"][0]
        CatalogService(db).record_event(first["catalog_id"], "favorite")
        service.replace_tags(first["resource_id"], ["Keep my tag"])
        return SimpleNamespace(db=db, path=path, nodes=nodes, payload=payload, baseline=baseline,
                               service=service, sync=sync, cid=first["catalog_id"], rid=first["resource_id"])
    return seed


def protected(seed):
    with seed.db.engine.connect() as conn:
        return {table: [tuple(row) for row in conn.execute(text(f"SELECT * FROM {table} ORDER BY 1,2"))]
                for table in ("item_user_state", "catalog_item_tags", "resource_tags")}


def plan_for(seed):
    with seed.db.engine.connect() as conn:
        return build_plan(conn, seed.path, seed.baseline)


def apply(seed, plan=None):
    with seed.db.write_transaction() as conn:
        return apply_plan(conn, plan or plan_for(seed), seed.path, seed.baseline)


def observe(seed):
    records, _ = read_bookmarks(seed.path)
    with seed.db.write_transaction() as conn:
        return reconcile_bound_bookmarks(conn, records, str(seed.path))


def write_nodes(seed):
    seed.path.write_text(json.dumps(seed.payload))


def test_preview_backfill_preserve_rows_state_tags_and_are_idempotent(seeded):
    seed = seeded(("https://example.com/old", "https://example.com/old"))
    before = protected(seed)
    with seed.db.engine.connect() as conn:
        old_items = list(conn.execute(text("SELECT id FROM catalog_items" )).scalars())
        old_resources = list(conn.execute(text("SELECT id FROM resources")).scalars())
    plan = plan_for(seed)
    assert plan["counts"]["new_bindings"] == 2
    assert plan["counts"]["shared_rows"] == 1
    assert protected(seed) == before
    apply(seed, plan)
    assert protected(seed) == before
    with seed.db.engine.connect() as conn:
        assert list(conn.execute(text("SELECT id FROM catalog_items")).scalars()) == old_items
        assert list(conn.execute(text("SELECT id FROM resources")).scalars()) == old_resources
        assert conn.execute(text("SELECT COUNT(*) FROM bookmark_bindings")).scalar() == 2
        assert conn.execute(text("SELECT COUNT(*) FROM catalog_origins WHERE source_key LIKE 'chrome-bookmark:%'")).scalar() == 2
    again = plan_for(seed)
    assert again["counts"]["new_bindings"] == 0
    assert again["counts"]["already_bound"] == 2
    apply(seed, again)
    assert protected(seed) == before


def test_url_change_keeps_ids_and_resource_projection_and_import_do_not_duplicate(seeded):
    seed = seeded()
    apply(seed)
    before = protected(seed)
    seed.nodes[0]["url"] = "https://new.example.com/moved"
    write_nodes(seed)
    assert seed.sync.sync(("bookmarks",))["bookmarks"]["status"] == "complete"
    assert seed.sync.sync(("resources",))["resources"]["status"] == "complete"
    seed.service.import_file(seed.path, enqueue=False)
    assert protected(seed) == before
    with seed.db.engine.connect() as conn:
        assert conn.execute(text("SELECT id FROM catalog_items")).scalars().all() == [seed.cid]
        resource = conn.execute(text("SELECT id,canonical_url,original_url FROM resources")).mappings().one()
        assert resource["id"] == seed.rid
        assert resource["canonical_url"] == seed.nodes[0]["url"]
        assert resource["original_url"] == "https://example.com/old"
        assert conn.execute(text("SELECT COUNT(*) FROM bookmark_url_history")).scalar() == 1
    observe(seed)
    with seed.db.engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM bookmark_url_history")).scalar() == 1


def test_shared_row_divergence_holds_then_converges_without_splitting(seeded):
    seed = seeded(("https://example.com/old", "https://example.com/old"))
    apply(seed)
    before = protected(seed)
    seed.nodes[0]["url"] = "https://example.com/new"
    write_nodes(seed)
    assert seed.sync.sync(("bookmarks",))["bookmarks"]["status"] == "complete"
    with seed.db.engine.connect() as conn:
        assert set(conn.execute(text("SELECT conflict_reason FROM bookmark_bindings")).scalars()) == {"shared_row_divergence"}
        assert conn.execute(text("SELECT canonical_url FROM resources")).scalar() == "https://example.com/old"
        assert conn.execute(text("SELECT COUNT(*) FROM catalog_origins WHERE source_key LIKE 'chrome-bookmark:%' AND stale=0")).scalar() == 2
    seed.nodes[1]["url"] = seed.nodes[0]["url"]
    write_nodes(seed)
    assert observe(seed)["matched"] == 2
    assert protected(seed) == before
    with seed.db.engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM catalog_items")).scalar() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM resources")).scalar() == 1


def test_target_collision_does_not_merge_or_steal_favorite(seeded):
    seed = seeded(("https://example.com/old", "https://example.com/occupied"), selected={0})
    apply(seed)
    before = protected(seed)
    seed.nodes[0]["url"] = seed.nodes[1]["url"]
    write_nodes(seed)
    assert observe(seed)["conflicts"] == 1
    with seed.db.engine.connect() as conn:
        assert conn.execute(text("SELECT conflict_reason FROM bookmark_bindings")).scalar() == "target_url_already_owned"
        assert conn.execute(text("SELECT canonical_url FROM resources WHERE id=:id"), {"id": seed.rid}).scalar() == "https://example.com/old"
        assert conn.execute(text("SELECT COUNT(*) FROM resources")).scalar() == 2
    assert protected(seed) == before


def test_excluded_nodes_not_imported_and_renames_keep_binding(seeded):
    seed = seeded()
    apply(seed)
    before = protected(seed)
    seed.payload["roots"]["bookmark_bar"]["children"][0]["name"] = "renamed hin"
    seed.nodes.append({**seed.nodes[0], "guid": str(uuid.uuid4()), "id": "100", "url": "https://example.com/excluded"})
    write_nodes(seed)
    seed.service.import_file(seed.path, enqueue=False)
    seed.sync.sync(("bookmarks",))
    with seed.db.engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM resources")).scalar() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM catalog_items")).scalar() == 1
        assert conn.execute(text("SELECT folder_path FROM bookmark_bindings")).scalar() == "Bookmarks Bar / renamed hin"
    assert protected(seed) == before


def test_stale_or_edited_preview_rolls_back_every_binding(seeded):
    seed = seeded()
    plan = plan_for(seed)
    tampered = copy.deepcopy(plan)
    tampered["bindings"][0]["id"] = "different-id"
    with pytest.raises(ValueError, match="edited"):
        apply(seed, tampered)
    CatalogService(seed.db).record_event(seed.cid, "unfavorite")
    with pytest.raises(ValueError, match="stale"):
        apply(seed, plan)
    with seed.db.engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM bookmark_sources")).scalar() == 0
        assert conn.execute(text("SELECT COUNT(*) FROM bookmark_bindings")).scalar() == 0


def test_numeric_fallback_can_gain_guid_but_reused_id_is_held(seeded):
    seed = seeded()
    apply(seed)
    with seed.db.write_transaction() as conn:
        conn.execute(text("UPDATE bookmark_bindings SET guid=NULL"))
    assert observe(seed)["matched"] == 1
    with seed.db.engine.connect() as conn:
        assert conn.execute(text("SELECT guid FROM bookmark_bindings")).scalar() == seed.nodes[0]["guid"]
    seed.nodes[0]["guid"] = str(uuid.uuid4())
    write_nodes(seed)
    assert observe(seed)["conflicts"] == 1
    with seed.db.engine.connect() as conn:
        assert conn.execute(text("SELECT conflict_reason FROM bookmark_bindings")).scalar() == "guid_id_disagreement"


def test_running_resource_job_holds_url_change(seeded):
    seed = seeded()
    apply(seed)
    from src.flowinone.resource_library.jobs import JobQueue
    JobQueue(seed.db).queue("fetch_metadata", resource_id=seed.rid, payload={"url": "https://example.com/old"})
    with seed.db.write_transaction() as conn:
        conn.execute(text("UPDATE processing_jobs SET status='running'"))
    seed.nodes[0]["url"] = "https://example.com/new"
    write_nodes(seed)
    assert observe(seed)["conflicts"] == 1
    with seed.db.engine.connect() as conn:
        assert conn.execute(text("SELECT conflict_reason FROM bookmark_bindings")).scalar() == "resource_job_running"


def test_bound_metadata_cannot_change_resource_url(seeded, monkeypatch, tmp_path):
    seed = seeded()
    apply(seed)
    from src.flowinone.resource_library.enrichment import EnrichmentService
    from src.flowinone.resource_library.extractors import ExtractedMetadata
    from src.flowinone.resource_library.settings import ResourceSettings
    fake = SimpleNamespace(fetch_metadata=lambda url: ExtractedMetadata(title="Refreshed", canonical_url="https://example.com/unapproved"))
    monkeypatch.setattr("src.flowinone.resource_library.enrichment.extractor_for", lambda *a, **k: fake)
    settings = replace(ResourceSettings.from_environment(), data_dir=tmp_path / "data",
                       database_path=seed.db.path, content_dir=tmp_path / "content")
    EnrichmentService(seed.db, settings=settings).fetch_metadata(seed.rid)
    with seed.db.engine.connect() as conn:
        assert conn.execute(text("SELECT canonical_url FROM resources")).scalar() == "https://example.com/old"
        assert conn.execute(text("SELECT COUNT(*) FROM catalog_items")).scalar() == 1


def test_snapshot_includes_committed_wal_and_does_not_touch_source(tmp_path):
    path = tmp_path / "wal.db"
    writer = sqlite3.connect(path)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("CREATE TABLE data(value TEXT)")
    writer.execute("INSERT INTO data VALUES ('committed')")
    writer.commit()
    files = [path, tmp_path / "wal.db-wal", tmp_path / "wal.db-shm"]
    before = {p: p.read_bytes() for p in files}
    memory, metadata = read_snapshot(path)
    assert memory.execute("SELECT * FROM data").fetchall() == [("committed",)]
    assert metadata["committed_wal_frames"] > 0
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        memory.execute("INSERT INTO data VALUES ('blocked')")
    memory.close()
    assert {p: p.read_bytes() for p in files} == before
    writer.close()


def test_standalone_preview_does_not_migrate_database(seeded):
    seed = seeded()
    with seed.db.write_transaction() as conn:
        for table in ("bookmark_url_history", "bookmark_bindings", "bookmark_sources"):
            conn.execute(text(f"DROP TABLE {table}"))
        conn.execute(text("UPDATE alembic_version SET version_num='0012_catalog_duplicate_reviews'"))
    result = preview(seed.db.path, seed.path, seed.baseline, expected_count=1)
    assert result["counts"]["new_bindings"] == 1
    with seed.db.engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0012_catalog_duplicate_reviews"
        assert not conn.execute(text("SELECT 1 FROM sqlite_master WHERE name='bookmark_bindings'")).scalar()


def test_missing_bookmarks_keep_content_and_state(seeded):
    seed = seeded()
    apply(seed)
    before = protected(seed)
    seed.nodes.clear()
    write_nodes(seed)
    seed.sync.sync(("bookmarks",))
    assert protected(seed) == before
    with seed.db.engine.connect() as conn:
        assert conn.execute(text("SELECT status FROM bookmark_bindings")).scalar() == "missing"
        assert conn.execute(text("SELECT COUNT(*) FROM catalog_items")).scalar() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM resources")).scalar() == 1


def test_source_switch_and_partial_snapshot_fail_closed(seeded, tmp_path):
    seed = seeded(("https://example.com/a", "https://example.com/b"))
    apply(seed)
    records = load_bookmarks(seed.path)
    with pytest.raises(ValueError, match="complete source"):
        seed.service.import_records(records[:1], enqueue=False)
    records, _ = read_bookmarks(seed.path)
    other = tmp_path / "AnotherProfile" / "AccountBookmarks"
    with seed.db.write_transaction() as conn:
        with pytest.raises(ValueError, match="profile/storage changed"):
            reconcile_bound_bookmarks(conn, records, str(other))


def test_changed_bookmark_invalidates_preview_without_writes(seeded):
    seed = seeded()
    plan = plan_for(seed)
    seed.nodes[0]["url"] = "https://example.com/changed-before-approval"
    write_nodes(seed)
    with pytest.raises(ValueError, match="stale"):
        apply(seed, plan)
    with seed.db.engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM bookmark_bindings")).scalar() == 0


def test_invalid_apply_confirmation_never_upgrades_database(seeded, monkeypatch, tmp_path):
    seed = seeded()
    from scripts.bookmark_bindings import main
    baseline_file = tmp_path / "baseline.json"
    plan_file = tmp_path / "plan.json"
    baseline_file.write_text(json.dumps(seed.baseline))
    plan_file.write_text(json.dumps(preview(seed.db.path, seed.path, seed.baseline)))
    monkeypatch.setattr("sys.argv", ["bookmark_bindings.py", "apply", "--database", str(seed.db.path),
        "--bookmarks", str(seed.path), "--baseline", str(baseline_file), "--plan", str(plan_file),
        "--confirm-plan", "unapproved"])
    monkeypatch.setattr("src.flowinone.resource_library.database.backup_database",
                        lambda *a: pytest.fail("backup must not run before valid approval"))
    assert main() == 1


def test_explicit_cli_apply_creates_backup_and_only_approved_bindings(seeded, monkeypatch, tmp_path):
    seed = seeded()
    from scripts.bookmark_bindings import main
    baseline_file = tmp_path / "baseline.json"
    plan_file = tmp_path / "plan.json"
    baseline_file.write_text(json.dumps(seed.baseline))
    reviewed = preview(seed.db.path, seed.path, seed.baseline)
    plan_file.write_text(json.dumps(reviewed))
    before = protected(seed)
    monkeypatch.setattr("sys.argv", ["bookmark_bindings.py", "apply", "--database", str(seed.db.path),
        "--bookmarks", str(seed.path), "--baseline", str(baseline_file), "--plan", str(plan_file),
        "--confirm-plan", reviewed["approval_digest"]])
    assert main() == 0
    assert list((tmp_path / "backups").glob("*.bak"))
    assert protected(seed) == before
    with seed.db.engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM bookmark_bindings")).scalar() == 1


def test_external_resource_url_edit_is_reported_without_another_change(seeded):
    seed = seeded()
    apply(seed)
    with seed.db.write_transaction() as conn:
        conn.execute(text("UPDATE resources SET canonical_url='https://example.com/external'"))
    assert observe(seed)["conflicts"] == 1
    with seed.db.engine.connect() as conn:
        assert conn.execute(text("SELECT conflict_reason FROM bookmark_bindings")).scalar() == "stored_url_changed_outside_binding"
        assert conn.execute(text("SELECT COUNT(*) FROM resources")).scalar() == 1


def test_snapshot_ignores_uncommitted_wal_frames(tmp_path):
    path = tmp_path / "uncommitted.db"
    writer = sqlite3.connect(path)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("PRAGMA cache_size=1")
    writer.execute("CREATE TABLE data(value TEXT)")
    writer.execute("INSERT INTO data VALUES ('committed')")
    writer.commit()
    writer.executemany("INSERT INTO data VALUES (?)", [("pending" * 1000,) for _ in range(40)])
    memory, _ = read_snapshot(path)
    assert memory.execute("SELECT COUNT(*) FROM data").fetchone()[0] == 1
    assert memory.execute("SELECT value FROM data").fetchone()[0] == "committed"
    memory.close()
    writer.rollback()
    writer.close()
