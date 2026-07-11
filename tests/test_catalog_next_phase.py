"""Catalog projection, discovery, sessions, transitions, and sidecar coverage."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from flask import Flask

from routes import register_routes
from src.file_handler import item_db
from src.file_handler.sidecars import SidecarService
from src.flowinone.catalog.discovery import DiscoveryService
from src.flowinone.catalog.service import CatalogQuery, CatalogService, CatalogSyncService
from src.flowinone.entry_system.service import EntryService
from src.flowinone.gallery.models import GalleryQuery
from src.flowinone.gallery.service import CatalogGalleryService
from src.flowinone.resource_library.curation import CollectionService
from src.flowinone.resource_library.database import get_resource_database


def _catalog(tmp_path: Path):
    database = get_resource_database(tmp_path / "catalog.db")
    sync = CatalogSyncService(database)
    with database.engine.begin() as conn:
        first = sync._upsert(
            conn, identity_key="url:one", source_kind="bookmarks", source_key="https://example.com/one",
            item_type="bookmark", title="Visual knowledge reference", detail_uri="https://example.com/one",
            original_url="https://example.com/one", tags=(("knowledge", "folder"), ("visual", "folder")),
        )
        merged = sync._upsert(
            conn, identity_key="url:one", source_kind="resources", source_key="resource-1",
            item_type="article", title="Visual knowledge system", detail_uri="/resources/resource-1/",
            original_url="https://example.com/one", tags=(("knowledge", "resource"),), prefer=True,
            extracted_text="catalog search projection",
        )
        second = sync._upsert(
            conn, identity_key="eagle:two", source_kind="eagle", source_key="two",
            item_type="image", title="Visual reference board", detail_uri="/EAGLE_image/two/",
            tags=(("visual", "source"), ("reference", "source")),
        )
        third = sync._upsert(
            conn, identity_key="local:three", source_kind="local", source_key="three",
            item_type="image", title="Knowledge reference image", detail_uri="/image/three.jpg",
            tags=(("knowledge", "folder"), ("reference", "folder")),
        )
    assert first == merged
    return database, first, second, third


def test_catalog_merges_origins_filters_fts_cursor_events_and_sessions(tmp_path):
    database, first, second, third = _catalog(tmp_path)
    service = CatalogService(database)
    detail = service.get(first)
    assert {origin["source_kind"] for origin in detail["origins"]} == {"bookmarks", "resources"}

    searched = service.list(CatalogQuery.create(q="projection", sources=["resources"], sort="relevance"))
    assert [item["id"] for item in searched["items"]] == [first]
    tag_all = service.list(CatalogQuery.create(tags=["knowledge", "reference"], tag_mode="all", sort="title", limit=1))
    assert tag_all["items"][0]["id"] == third
    assert CatalogQuery.create(tags=["knowledge,reference"]).tags == ("knowledge", "reference")

    page = service.list(CatalogQuery.create(sort="title", limit=1))
    assert page["next_cursor"]
    next_page = service.list(CatalogQuery.create(sort="title", limit=1, cursor=page["next_cursor"]))
    assert next_page["items"][0]["id"] != page["items"][0]["id"]
    with pytest.raises(ValueError, match="查詢不相符"):
        service.list(CatalogQuery.create(sort="recently_added", limit=1, cursor=page["next_cursor"]))

    first_random = service.list(CatalogQuery.create(sort="random", seed=11))["items"]
    assert [item["id"] for item in first_random] == [item["id"] for item in service.list(CatalogQuery.create(sort="random", seed=11))["items"]]
    second_scores = {item["id"]: item["sort_value"] for item in service.list(CatalogQuery.create(sort="random", seed=29))["items"]}
    assert any(item["sort_value"] != second_scores[item["id"]] for item in first_random)

    service.record_event(second, "favorite")
    service.record_event(second, "open")
    favorites = service.list(CatalogQuery.create(favorite=True, sources=["eagle"]))
    assert favorites["items"][0]["favorite"] is True
    session = service.save_session({"query": {"q": "visual", "sources": ["eagle"]}, "focused_item_id": second, "scroll_position": 900})
    assert session["scroll_position"] == 900
    assert service.recent_sessions(1)[0]["id"] == session["id"]


def test_generated_and_smart_collections_use_catalog_items(tmp_path):
    database, first, second, third = _catalog(tmp_path)
    collections = CollectionService(database)
    smart = collections.create("Knowledge", membership_mode="smart", query={"tags": ["knowledge"], "tag_mode": "any"})
    assert collections.get(smart["id"])["item_count"] == 2

    discovery = DiscoveryService(database)
    rebuilt = discovery.rebuild_item_relations()
    assert rebuilt["relations"] >= 2
    generated = discovery.generate_collections(minimum_size=2, limit=5)
    assert generated
    assert generated[0]["membership_mode"] == "generated"
    assert generated[0]["lifecycle_status"] == "draft"


def test_gallery_uses_selected_origin_and_hides_unavailable_eagle(monkeypatch, tmp_path):
    database, first, second, _ = _catalog(tmp_path)
    gallery = CatalogGalleryService(database)

    bookmark_page = gallery.list_items(GalleryQuery.create(sources=["bookmarks"], limit=10))
    selected = next(item for item in bookmark_page.items if item.id == first)
    assert selected.source == "bookmarks"
    assert selected.detail_uri == "https://example.com/one"
    assert selected.original_url == "https://example.com/one"

    monkeypatch.setattr("src.flowinone.gallery.service.is_eagle_available", lambda: False)
    eagle_page = gallery.list_items(GalleryQuery.create(sources=["eagle"], limit=10))
    assert eagle_page.items == []
    assert "eagle" in eagle_page.source_errors
    assert second


def test_entry_transition_is_explicit_and_linked(tmp_path):
    database = get_resource_database(tmp_path / "entries.db")
    service = EntryService(database)
    source = service.create_entry({
        "name": "Bounded scan", "mode": "scan", "status": "active",
        "context": {"scan": {"item_limit": 5}},
    })
    result = service.transition_entry(source["id"], {
        "target_mode": "learn", "learning_question": "What matters?",
        "stop_condition": "Explain it", "expected_output": "BUILD task",
    })
    assert result["source_entry"]["status"] == "active"
    assert result["target_entry"]["mode"] == "learn"
    assert service.repository.list_links(source["id"])["outgoing"][0]["entry_id"] == result["target_entry"]["id"]
    incoming = service.repository.list_links(result["target_entry"]["id"])["incoming"][0]
    assert (incoming["linked_type"], incoming["linked_id"]) == ("entry", source["id"])

    from_resource = service.create_source_entry({
        "source_kind": "resource", "source_id": "resource-1", "title": "Source",
        "mode": "learn", "target_uri": "/resources/resource-1/",
    })
    resource_link = service.repository.list_links(from_resource["id"])["incoming"][0]
    assert (resource_link["linked_type"], resource_link["linked_id"]) == ("resource", "resource-1")


def test_sidecar_roundtrip_preserves_portable_identity(monkeypatch, tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    media = root / "sample.jpg"
    media.write_bytes(b"fake-image-content")
    db_path = tmp_path / "item.db"
    monkeypatch.setattr(item_db, "ITEM_DB_PATH", str(db_path))
    item_db._ensure_item_db()
    with item_db._get_db_connection() as conn:
        conn.execute(
            "INSERT INTO items(item_id,name,data_source,item_type,tags,relative_path,absolute_path,library_root) VALUES(?,?,?,?,?,?,?,?)",
            ("one", "sample.jpg", "filesystem", "image", json.dumps(["reference"]), "sample.jpg", str(media), str(root)),
        )
    service = SidecarService()
    assert service.export(root, dry_run=False)["written"] == 1
    manifest = root / ".flowinone.json"
    assert manifest.exists()
    with item_db._get_db_connection() as conn:
        before = conn.execute("SELECT portable_uid,content_fingerprint FROM items WHERE item_id='one'").fetchone()
    moved = root / "moved.jpg"
    media.rename(moved)
    payload = json.loads(manifest.read_text())
    payload["items"][0]["path"] = "moved.jpg"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    assert service.import_(root, dry_run=False)["updated"] == 1
    with item_db._get_db_connection() as conn:
        after = conn.execute("SELECT portable_uid,content_fingerprint,absolute_path FROM items WHERE item_id='one'").fetchone()
    assert before["portable_uid"] == after["portable_uid"]
    assert before["content_fingerprint"] == after["content_fingerprint"]
    assert after["absolute_path"] == str(moved)


def test_new_catalog_routes_render(tmp_path):
    database = get_resource_database(tmp_path / "web.db")
    sync = CatalogSyncService(database)
    with database.engine.begin() as conn:
        sync._upsert(conn, identity_key="local:web", source_kind="local", source_key="web", item_type="image", title="Web item", detail_uri="/image/web.jpg")
        sync._upsert(conn, identity_key="url:web", source_kind="bookmarks", source_key="https://example.com/web", item_type="bookmark", title="Web bookmark", detail_uri="https://example.com/web", original_url="https://example.com/web")
        sync._upsert(conn, identity_key="url:web", source_kind="resources", source_key="resource-web", item_type="article", title="Web resource", detail_uri="/resources/resource-web/", original_url="https://example.com/web", prefer=True)
    app = Flask("catalog-next", template_folder=str(Path(__file__).parents[1] / "templates"), static_folder=str(Path(__file__).parents[1] / "static"))
    app.config.update(TESTING=True, FLOWINONE_RESOURCE_DB_PATH=str(tmp_path / "web.db"), FLOWINONE_RESOURCE_LINK_THUMBNAILS=False)
    register_routes(app)
    client = app.test_client()
    assert client.get("/search/").status_code == 200
    assert client.get("/api/catalog/items").status_code == 200
    bookmark_search = client.get("/search/?source=bookmarks")
    assert b"https://example.com/web" in bookmark_search.data
    assert b"/resources/resource-web/" not in bookmark_search.data


def test_catalog_100k_keyset_query_contract(tmp_path):
    """The materialized projection must avoid the former load-every-source request path."""
    database = get_resource_database(tmp_path / "catalog-100k.db")
    now = "2026-07-11T00:00:00+00:00"
    items = [
        (f"{index:032x}", f"synthetic:{index}", "image", f"Item {index:06d}", now, now, "available", "{}")
        for index in range(100_000)
    ]
    origins = [
        (f"o{index:031x}", f"{index:032x}", "local", str(index), now)
        for index in range(100_000)
    ]
    with database.engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO catalog_items(id,identity_key,item_type,title,indexed_at,captured_at,availability,metadata_json) VALUES(?,?,?,?,?,?,?,?)",
            items,
        )
        conn.exec_driver_sql(
            "INSERT INTO catalog_origins(id,catalog_item_id,source_kind,source_key,last_seen_at) VALUES(?,?,?,?,?)",
            origins,
        )
    started = time.perf_counter()
    page = CatalogService(database).list(CatalogQuery.create(scope="gallery", sources=["local"], sort="recently_added", limit=48))
    elapsed = time.perf_counter() - started
    assert page["total_estimate"] == 100_000
    assert len(page["items"]) == 48
    assert page["next_cursor"]
    assert elapsed < 2.5
