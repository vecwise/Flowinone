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


def enable_mirror(seed, monkeypatch, tmp_path):
    from src.file_handler.thumbnails.store import ThumbnailStore
    store = ThumbnailStore(str(tmp_path / 'mirror-cache.db'),str(tmp_path / 'mirror-thumbs'))
    monkeypatch.setattr('src.file_handler.thumbnails.store.get_thumbnail_store',lambda:store)
    apply(seed)
    with seed.db.write_transaction() as conn:
        conn.execute(text("UPDATE bookmark_sources SET sync_mode='mirror'"))
    return store


def test_mirror_delete_last_source_cascades_state_search_and_cache(seeded,monkeypatch,tmp_path):
    seed = seeded()
    store = enable_mirror(seed,monkeypatch,tmp_path)
    assert seed.sync.sync(('bookmarks',))['bookmarks']['status']=='complete'
    lookup = store.register_bookmark(seed.nodes[0]['url'],'cached')
    image = tmp_path / 'mirror-thumbs' / 'old.webp'
    image.write_bytes(b'image')
    store.record_thumbnail(lookup.media_id,str(image),provider='metadata',source_url=None,width=10,height=10)
    seed.nodes.clear();write_nodes(seed)
    result = seed.sync.sync(('bookmarks',))['bookmarks']
    assert result['status']=='complete'
    assert result['deleted_bindings']==result['deleted_catalog']==result['deleted_resources']==1
    with seed.db.engine.connect() as conn:
        for table in ('catalog_items','resources','bookmark_bindings','chrome_bookmark_nodes','catalog_origins',
                      'resource_origins','catalog_item_tags','resource_tags','item_user_state','catalog_events','catalog_fts','resource_fts'):
            assert conn.execute(text('SELECT count(*) FROM '+table)).scalar()==0,table
        assert not conn.exec_driver_sql('PRAGMA foreign_key_check').all()
    with store.connect() as conn:
        for table in ('media_items','thumbnails','thumbnail_jobs'):
            assert conn.execute('SELECT count(*) FROM '+table).fetchone()[0]==0
    assert not image.exists()


def test_mirror_dedup_then_url_change_preserves_surviving_row_and_user_metadata(seeded,monkeypatch,tmp_path):
    seed = seeded(('https://example.com/old','https://example.com/old'))
    enable_mirror(seed,monkeypatch,tmp_path)
    seed.nodes.pop();seed.nodes[0]['url']='https://example.com/new';seed.nodes[0]['name']='Renamed'
    seed.payload['roots']['bookmark_bar']['children'][0]['name']='Moved folder'
    write_nodes(seed)
    assert seed.sync.sync(('bookmarks',))['bookmarks']['status']=='complete'
    with seed.db.engine.connect() as conn:
        assert conn.execute(text('SELECT id FROM catalog_items')).scalars().all()==[seed.cid]
        assert conn.execute(text('SELECT id FROM resources')).scalars().all()==[seed.rid]
        assert conn.execute(text('SELECT canonical_url FROM resources')).scalar()=='https://example.com/new'
        assert conn.execute(text('SELECT title FROM catalog_items')).scalar()=='Renamed'
        assert conn.execute(text('SELECT status FROM bookmark_bindings')).scalar()=='matched'
        assert conn.execute(text('SELECT favorite FROM item_user_state')).scalar()==1
        assert conn.execute(text("SELECT count(*) FROM resource_tags WHERE source='user'")).scalar()==1
        assert conn.execute(text("SELECT count(*) FROM catalog_tags t JOIN catalog_item_tags x ON x.tag_id=t.id WHERE x.source='folder' AND t.name='Moved folder'")).scalar()==1
        assert conn.execute(text("SELECT count(*) FROM catalog_fts WHERE catalog_fts MATCH 'Renamed'")).scalar()==1
    assert seed.sync.sync(('bookmarks',))['bookmarks']['status']=='complete'
    with seed.db.engine.connect() as conn:
        assert conn.execute(text('SELECT count(*) FROM bookmark_url_history')).scalar()==1


def test_mirror_manual_origin_prevents_content_and_thumbnail_deletion(seeded,monkeypatch,tmp_path):
    seed = seeded();store = enable_mirror(seed,monkeypatch,tmp_path)
    seed.service.create_url('https://example.com/old',enqueue=False)
    assert seed.sync.sync(('bookmarks',))['bookmarks']['status']=='complete'
    media = store.register_bookmark('https://example.com/old','manual')
    image = tmp_path/'mirror-thumbs'/'manual.webp';image.write_bytes(b'keep')
    store.record_thumbnail(media.media_id,str(image),provider='metadata',source_url=None,width=1,height=1)
    seed.nodes.clear();write_nodes(seed)
    assert seed.sync.sync(('bookmarks',))['bookmarks']['status']=='complete'
    with seed.db.engine.connect() as conn:
        assert conn.execute(text('SELECT id FROM resources')).scalar()==seed.rid
        assert conn.execute(text('SELECT id FROM catalog_items')).scalar()==seed.cid
        assert conn.execute(text('SELECT count(*) FROM bookmark_bindings')).scalar()==0
        assert conn.execute(text('SELECT favorite FROM item_user_state')).scalar()==1
    assert image.exists()
    assert store.get_thumbnail_path(media.media_id)==str(image)


def test_mirror_adopts_unbound_and_non_web_nodes_and_removes_gone_legacy(seeded,monkeypatch,tmp_path):
    seed=seeded(('https://example.com/keep','https://example.com/remove'),selected={0})
    enable_mirror(seed,monkeypatch,tmp_path)
    seed.nodes.pop()
    seed.nodes.extend([
        {**seed.nodes[0],'id':'101','guid':str(uuid.uuid4()),'url':'https://example.com/new'},
        {**seed.nodes[0],'id':'102','guid':str(uuid.uuid4()),'url':'chrome://newtab/'},
    ])
    write_nodes(seed)
    result=seed.sync.sync(('bookmarks',))['bookmarks']
    assert result['status']=='complete'
    with seed.db.engine.connect() as conn:
        assert conn.execute(text('SELECT count(*) FROM chrome_bookmark_nodes')).scalar()==3
        assert conn.execute(text('SELECT count(*) FROM bookmark_bindings')).scalar()==2
        assert conn.execute(text('SELECT count(*) FROM resources')).scalar()==2
        assert not conn.execute(text("SELECT id FROM resources WHERE canonical_url='https://example.com/remove'")).scalar()
        assert conn.execute(text("SELECT unsupported_reason FROM chrome_bookmark_nodes WHERE url='chrome://newtab/'")).scalar()
        assert not conn.execute(text("SELECT 1 FROM catalog_origins WHERE source_kind='bookmarks' AND source_key NOT LIKE 'chrome-bookmark:%'")).scalar()


def test_mirror_collision_keeps_two_rows_without_merging(seeded,monkeypatch,tmp_path):
    seed=seeded(('https://example.com/one','https://example.com/two'))
    enable_mirror(seed,monkeypatch,tmp_path)
    seed.nodes[0]['url']=seed.nodes[1]['url'];write_nodes(seed)
    assert seed.sync.sync(('bookmarks',))['bookmarks']['status']=='complete'
    with seed.db.engine.connect() as conn:
        assert conn.execute(text('SELECT count(*) FROM catalog_items')).scalar()==2
        assert conn.execute(text('SELECT count(*) FROM resources')).scalar()==2
        assert conn.execute(text("SELECT count(*) FROM bookmark_bindings WHERE conflict_reason='target_url_already_owned'")).scalar()==1
        assert conn.execute(text('SELECT count(*) FROM chrome_bookmark_nodes')).scalar()==2
        assert conn.execute(text('SELECT favorite FROM item_user_state')).scalar()==1


def test_mirror_malformed_or_missing_source_cannot_delete(seeded,monkeypatch,tmp_path):
    seed=seeded();store=enable_mirror(seed,monkeypatch,tmp_path)
    assert seed.sync.sync(('bookmarks',))['bookmarks']['status']=='complete'
    before=protected(seed)
    seed.path.write_text(json.dumps({'roots':{}}))
    assert seed.sync.sync(('bookmarks',))['bookmarks']['status']=='failed'
    assert protected(seed)==before
    seed.path.unlink()
    assert seed.sync.sync(('bookmarks',))['bookmarks']['status']=='failed'
    with seed.db.engine.connect() as conn:
        assert conn.execute(text('SELECT count(*) FROM resources')).scalar()==1
        assert conn.execute(text('SELECT count(*) FROM bookmark_bindings')).scalar()==1
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) FROM media_items').fetchone()[0]==1


def test_mirror_auto_watcher_is_bookmark_only_and_queues_first_snapshot(seeded,monkeypatch,tmp_path):
    from src.flowinone.catalog.watch import CatalogSourceWatcher
    seed=seeded();enable_mirror(seed,monkeypatch,tmp_path)
    watcher=CatalogSourceWatcher(seed.db,signatures={'bookmarks':lambda:'first','local':lambda:pytest.fail('unrelated scan')})
    assert watcher.process_once()['enabled'] is True
    with seed.db.engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM processing_jobs WHERE job_type='catalog_sync'")).scalar()==1
    assert watcher.process_once()['enabled'] is True
    watcher.set_enabled(False)
    assert watcher.process_once()['enabled'] is False


def test_mirror_independent_resource_origin_blocks_url_reassignment(seeded,monkeypatch,tmp_path):
    seed=seeded();enable_mirror(seed,monkeypatch,tmp_path)
    seed.service.create_url('https://example.com/old',enqueue=False)
    seed.nodes[0]['url']='https://example.com/new';write_nodes(seed)
    assert seed.sync.sync(('bookmarks',))['bookmarks']['status']=='complete'
    with seed.db.engine.connect() as conn:
        assert conn.execute(text('SELECT canonical_url FROM resources')).scalar()=='https://example.com/old'
        assert conn.execute(text('SELECT conflict_reason FROM bookmark_bindings')).scalar()=='shared_row_other_origin'


def test_mirror_deleted_resource_reclaims_owned_files_but_preserves_shared_paths(seeded,monkeypatch,tmp_path):
    seed=seeded();enable_mirror(seed,monkeypatch,tmp_path)
    own=tmp_path/'content'/'text'/seed.rid/'old.txt';own.parent.mkdir(parents=True);own.write_text('owned')
    external=tmp_path/'external.txt';external.write_text('external')
    seed.nodes.clear();write_nodes(seed)
    assert seed.sync.sync(('bookmarks',))['bookmarks']['status']=='complete'
    assert not own.exists()
    assert external.exists()


def test_mirror_cli_enables_full_profile_without_backup_and_reports_schema(seeded,monkeypatch,tmp_path):
    from scripts.chrome_mirror import main
    from src.file_handler.thumbnails.store import ThumbnailStore
    seed=seeded()
    store=ThumbnailStore(str(tmp_path/'cli-cache.db'),str(tmp_path/'cli-thumbs'))
    monkeypatch.setattr('scripts.chrome_mirror.get_thumbnail_store',lambda:store)
    monkeypatch.setattr('src.file_handler.thumbnails.store.get_thumbnail_store',lambda:store)
    monkeypatch.setattr('src.flowinone.resource_library.database.backup_database',lambda *a:pytest.fail('No backup requested'))
    output=tmp_path/'status.json'
    monkeypatch.setattr('sys.argv',['chrome_mirror.py','enable','--database',str(seed.db.path),'--bookmarks',str(seed.path),'--output',str(output),
                                  '--cache-database',store.db_path,'--cache-dir',store.cache_dir])
    assert main()==0
    report=json.loads(output.read_text())
    assert report['exact_node_match'] and report['db_nodes']==report['chrome_nodes']==1
    assert report['source']['sync_mode']=='mirror'
    assert report['schema']['chrome_bookmark_nodes'] and report['schema']['cache.thumbnails']
    assert not (tmp_path/'backups').exists()


def test_deleted_resource_running_fetch_does_not_leave_late_files(seeded,monkeypatch,tmp_path):
    from src.flowinone.resource_library.worker import ResourceWorker
    seed=seeded();enable_mirror(seed,monkeypatch,tmp_path)
    late=tmp_path/'content'/'text'/seed.rid/'late.txt'
    def fetch(_job):
        seed.nodes.clear();write_nodes(seed)
        assert seed.sync.sync(('bookmarks',))['bookmarks']['status']=='complete'
        late.parent.mkdir(parents=True,exist_ok=True);late.write_text('late fetch')
    worker=ResourceWorker(seed.db)
    monkeypatch.setattr(worker.enrichment,'process_job',fetch)
    worker._process({'id':'deleted-job','job_type':'fetch_metadata','resource_id':seed.rid})
    assert not late.exists()


def test_later_independent_json_import_is_not_retired_as_legacy_chrome(seeded,monkeypatch,tmp_path):
    from src.flowinone.resource_library.importers import BookmarkRecord
    seed=seeded();enable_mirror(seed,monkeypatch,tmp_path)
    assert seed.sync.sync(('bookmarks',))['bookmarks']['status']=='complete'
    seed.service.import_records([BookmarkRecord(url='https://example.com/old',title='Independent export',source_key='file:other.json')],enqueue=False)
    seed.nodes.clear();write_nodes(seed)
    assert seed.sync.sync(('bookmarks',))['bookmarks']['status']=='complete'
    with seed.db.engine.connect() as conn:
        assert conn.execute(text('SELECT id FROM resources')).scalar()==seed.rid
        assert conn.execute(text('SELECT source_key FROM resource_origins')).scalar()=='file:other.json'


def test_mirror_sqlite_deletion_reclaims_disk_pages(seeded,monkeypatch,tmp_path):
    from pathlib import Path
    from src.flowinone.bookmarks.mirror import configure_mirror_storage,reclaim_mirror_storage
    seed=seeded();store=enable_mirror(seed,monkeypatch,tmp_path)
    configure_mirror_storage(seed.db.path,Path(store.db_path))
    with sqlite3.connect(seed.db.path) as conn:
        assert conn.execute('PRAGMA auto_vacuum').fetchone()[0]==2
        conn.execute('CREATE TABLE vacuum_probe(data BLOB)')
        conn.executemany('INSERT INTO vacuum_probe VALUES(?)',[(b'x'*8192,)]*256)
        conn.commit();conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        size=seed.db.path.stat().st_size
        conn.execute('DELETE FROM vacuum_probe');conn.commit()
    result=reclaim_mirror_storage(seed.db.path,Path(store.db_path))
    assert result[seed.db.path.name]['incremental_vacuum']
    assert seed.db.path.stat().st_size<size//2


def test_new_chrome_source_revives_a_previously_deleted_catalog_projection(seeded,monkeypatch,tmp_path):
    seed=seeded(('https://example.com/keep','https://example.com/revive'),selected={0})
    enable_mirror(seed,monkeypatch,tmp_path)
    with seed.db.write_transaction() as conn:
        conn.execute(text('UPDATE catalog_items SET is_deleted=1 WHERE id!=:id'),{'id':seed.cid})
    assert seed.sync.sync(('bookmarks',))['bookmarks']['status']=='complete'
    with seed.db.engine.connect() as conn:
        assert conn.execute(text('SELECT sum(is_deleted) FROM catalog_items')).scalar()==0
        assert conn.execute(text("SELECT count(*) FROM bookmark_bindings WHERE status='matched'")).scalar()==2
