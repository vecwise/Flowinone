"""Catalog projection, discovery, sessions, and sidecar coverage."""

from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from bs4 import BeautifulSoup
from flask import Flask
from sqlalchemy import event

from routes import register_routes
from src.file_handler import item_db
from src.file_handler.sidecars import SidecarService
from src.flowinone.catalog.discovery import DiscoveryService
from src.flowinone.catalog.service import CatalogQuery, CatalogService, CatalogSyncService
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
            tags=(("visual", "source"), ("reference", "source"), ("taxonomy", "source")),
        )
        third = sync._upsert(
            conn, identity_key="local:three", source_kind="local", source_key="three",
            item_type="image", title="Knowledge reference image", detail_uri="/image/three.jpg",
            tags=(("knowledge", "folder"), ("reference", "folder")),
        )
    assert first == merged
    return database, first, second, third


def test_initial_catalog_sync_is_single_flight(monkeypatch, tmp_path):
    database = get_resource_database(tmp_path / "single-flight.db")
    loader_started = Event()
    allow_loader_to_finish = Event()
    calls = 0

    def load_one_bookmark(service, conn):
        nonlocal calls
        calls += 1
        loader_started.set()
        assert allow_loader_to_finish.wait(timeout=2)
        service._upsert(
            conn,
            identity_key="url:single-flight",
            source_kind="bookmarks",
            source_key="https://example.com/single-flight",
            item_type="bookmark",
            title="Single flight",
            detail_uri="https://example.com/single-flight",
            original_url="https://example.com/single-flight",
        )
        return 1

    monkeypatch.setattr(CatalogSyncService, "_sync_bookmarks", load_one_bookmark)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            CatalogSyncService(database).sync_if_empty,
            ("bookmarks",),
        )
        assert loader_started.wait(timeout=2)
        second = executor.submit(
            CatalogSyncService(database).sync_if_empty,
            ("bookmarks",),
        )
        allow_loader_to_finish.set()
        assert first.result(timeout=5)["bookmarks"]["status"] == "complete"
        assert second.result(timeout=5) == {}

    assert calls == 1


def test_catalog_retries_a_transient_sqlite_lock_with_backoff(monkeypatch, tmp_path):
    database = get_resource_database(tmp_path / "transient-lock.db")
    with database.engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA busy_timeout=10")

    monkeypatch.setattr(CatalogSyncService, "_sync_bookmarks", lambda _self, _conn: 2)
    blocker = sqlite3.connect(database.path)
    blocker.execute("BEGIN IMMEDIATE")
    delays = []

    def release_lock(delay):
        delays.append(delay)
        blocker.rollback()

    try:
        result = CatalogSyncService(
            database,
            lock_max_retries=2,
            lock_retry_base_delay=0.01,
            sleep=release_lock,
        ).sync(("bookmarks",))
    finally:
        blocker.close()

    assert result["bookmarks"] == {
        "status": "complete",
        "count": 2,
        "attempts": 2,
        "max_attempts": 3,
        "retry_count": 1,
        "error": None,
        "synced_at": result["bookmarks"]["synced_at"],
    }
    assert delays == [0.01]
    assert CatalogService(database).sync_status()["bookmarks"]["status"] == "complete"


def test_catalog_lock_failure_is_bounded_and_does_not_raise(tmp_path):
    database = get_resource_database(tmp_path / "locked.db")
    with database.engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA busy_timeout=10")

    blocker = sqlite3.connect(database.path)
    blocker.execute("BEGIN IMMEDIATE")
    delays = []
    try:
        result = CatalogSyncService(
            database,
            lock_max_retries=2,
            lock_retry_base_delay=0.01,
            sleep=delays.append,
        ).sync(("bookmarks",))
    finally:
        blocker.rollback()
        blocker.close()

    assert result["bookmarks"]["status"] == "failed"
    assert result["bookmarks"]["locked"] is True
    assert result["bookmarks"]["attempts"] == 3
    assert result["bookmarks"]["retry_count"] == 2
    assert delays == [0.01, 0.02]
    assert "database is locked" in result["bookmarks"]["error"]
    assert CatalogService(database).sync_status()["bookmarks"]["status"] == "failed"


def test_catalog_preserves_non_lock_failure_record(monkeypatch, tmp_path):
    database = get_resource_database(tmp_path / "source-failure.db")

    def fail_source(_service, _conn):
        raise ValueError("bookmark parser failed")

    monkeypatch.setattr(CatalogSyncService, "_sync_bookmarks", fail_source)
    result = CatalogSyncService(database, sleep=lambda _delay: None).sync(("bookmarks",))

    assert result["bookmarks"]["status"] == "failed"
    assert result["bookmarks"]["locked"] is False
    assert result["bookmarks"]["recorded"] is True
    assert result["bookmarks"]["retry_count"] == 0
    with database.engine.connect() as conn:
        state = conn.exec_driver_sql(
            "SELECT status,error_message FROM catalog_sync_state WHERE source_kind='bookmarks'"
        ).mappings().one()
    assert dict(state) == {
        "status": "failed",
        "error_message": "bookmark parser failed",
    }


def test_catalog_exposes_retrying_status_while_backing_off(monkeypatch, tmp_path):
    database = get_resource_database(tmp_path / "live-retry.db")
    retrying = Event()
    continue_retry = Event()
    calls = 0

    def lock_once(_service, _conn):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise sqlite3.OperationalError("database is locked")
        return 1

    def wait_during_backoff(_delay):
        retrying.set()
        assert continue_retry.wait(timeout=2)

    monkeypatch.setattr(CatalogSyncService, "_sync_bookmarks", lock_once)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            CatalogSyncService(
                database,
                lock_max_retries=2,
                lock_retry_base_delay=0.01,
                sleep=wait_during_backoff,
            ).sync,
            ("bookmarks",),
        )
        assert retrying.wait(timeout=2)
        live = CatalogService(database).sync_status()["bookmarks"]
        assert live["status"] == "retrying"
        assert live["next_attempt"] == 2
        continue_retry.set()
        assert future.result(timeout=5)["bookmarks"]["status"] == "complete"


def test_concurrent_catalog_sync_requests_return_200(monkeypatch, tmp_path):
    database = get_resource_database(tmp_path / "concurrent-api.db")
    first_started = Event()
    allow_first = Event()
    calls = 0

    def slow_bookmark_sync(_service, _conn):
        nonlocal calls
        calls += 1
        if calls == 1:
            first_started.set()
            assert allow_first.wait(timeout=2)
        return 0

    monkeypatch.setattr(CatalogSyncService, "_sync_bookmarks", slow_bookmark_sync)
    app = Flask(
        "catalog-concurrent",
        template_folder=str(Path(__file__).parents[1] / "templates"),
        static_folder=str(Path(__file__).parents[1] / "static"),
    )
    app.config.update(
        TESTING=True,
        FLOWINONE_RESOURCE_DB_PATH=str(database.path),
        FLOWINONE_RESOURCE_LINK_THUMBNAILS=False,
    )
    register_routes(app)

    def request_sync():
        with app.test_client() as client:
            return client.post(
                "/api/catalog/sync",
                json={"sources": ["bookmarks"]},
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(request_sync)
        assert first_started.wait(timeout=2)
        second = executor.submit(request_sync)
        allow_first.set()
        responses = (first.result(timeout=5), second.result(timeout=5))

    assert [response.status_code for response in responses] == [200, 200]
    assert all(response.get_json()["bookmarks"]["status"] == "complete" for response in responses)


def test_catalog_sync_route_retries_only_the_requested_failed_source(monkeypatch, tmp_path):
    database = get_resource_database(tmp_path / "single-source-retry.db")
    database.set_catalog_sync_status(
        "bookmarks", status="failed", error="bookmark source failed"
    )
    database.set_catalog_sync_status(
        "resources", status="failed", error="resource source failed"
    )
    calls = []

    def sync_bookmarks(_service, _conn):
        calls.append("bookmarks")
        return 7

    def unexpected_sync(source):
        def fail_if_called(_service, _conn):
            calls.append(source)
            raise AssertionError(f"{source} must not be synchronized")

        return fail_if_called

    monkeypatch.setattr(CatalogSyncService, "_sync_bookmarks", sync_bookmarks)
    for source in ("local", "eagle", "resources"):
        monkeypatch.setattr(
            CatalogSyncService, f"_sync_{source}", unexpected_sync(source)
        )

    app = Flask(
        "catalog-single-source-retry",
        template_folder=str(Path(__file__).parents[1] / "templates"),
        static_folder=str(Path(__file__).parents[1] / "static"),
    )
    app.config.update(
        TESTING=True,
        FLOWINONE_RESOURCE_DB_PATH=str(database.path),
        FLOWINONE_RESOURCE_LINK_THUMBNAILS=False,
    )
    register_routes(app)
    response = app.test_client().post(
        "/api/catalog/sync", json={"sources": ["bookmarks"]}
    )

    assert response.status_code == 200
    assert calls == ["bookmarks"]
    assert set(response.get_json()) == {"bookmarks"}
    assert response.get_json()["bookmarks"]["status"] == "complete"
    assert response.get_json()["bookmarks"]["count"] == 7
    status = CatalogService(database).sync_status()
    assert status["bookmarks"]["status"] == "complete"
    assert status["bookmarks"]["item_count"] == 7
    assert status["resources"]["status"] == "failed"
    assert status["resources"]["error"] == "resource source failed"


def test_catalog_merges_origins_filters_fts_cursor_events_and_sessions(tmp_path):
    database, first, second, third = _catalog(tmp_path)
    service = CatalogService(database)
    detail = service.get(first)
    assert {origin["source_kind"] for origin in detail["origins"]} == {"bookmarks", "resources"}

    searched = service.list(CatalogQuery.create(q="projection", sources=["resources"], sort="relevance"))
    assert [item["id"] for item in searched["items"]] == [first]
    assert searched["items"][0]["match_reason"] == {
        "field": "content",
        "label": "擷取內容",
        "excerpt": "catalog search projection",
    }
    title_match = service.list(CatalogQuery.create(q="visual", sources=["eagle"], sort="relevance"))
    assert title_match["items"][0]["match_reason"] == {
        "field": "title",
        "label": "標題",
        "excerpt": "Visual reference board",
    }
    tag_match = service.list(CatalogQuery.create(q="taxonomy", sources=["eagle"], sort="relevance"))
    assert tag_match["items"][0]["match_reason"]["field"] == "tags"
    assert tag_match["items"][0]["match_reason"]["label"] == "標籤"
    assert "taxonomy" in tag_match["items"][0]["match_reason"]["excerpt"]
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


def test_catalog_saved_searches_keep_named_queries_separate_from_sessions(tmp_path):
    database, _first, second, _third = _catalog(tmp_path)
    service = CatalogService(database)

    saved = service.save_search(
        "  視覺靈感  ",
        {
            "q": "visual",
            "scope": "gallery",
            "sources": ["eagle"],
            "sort": "title",
            "cursor": "discard-this-page-cursor",
        },
    )
    service.save_session(
        {
            "query": {"q": "other", "sources": ["local"]},
            "focused_item_id": second,
            "scroll_position": 900,
        }
    )

    assert saved["label"] == "視覺靈感"
    assert saved["pinned"] is True
    assert saved["query"] == {
        "q": "visual",
        "scope": "gallery",
        "sources": ["eagle"],
        "type": None,
        "tags": [],
        "tag_mode": "any",
        "favorite": False,
        "unviewed": False,
        "duration_min": None,
        "duration_max": None,
        "added_from": None,
        "added_to": None,
        "sort": "title",
        "seed": None,
        "limit": 48,
        "cursor": None,
    }
    assert service.list_saved_searches() == [saved]

    updated = service.save_search(
        "視覺靈感（未看）",
        {"scope": "gallery", "sources": ["eagle"], "unviewed": True},
        saved_search_id=saved["id"],
        pinned=False,
    )
    assert updated["id"] == saved["id"]
    assert updated["pinned"] is False
    assert updated["query"]["unviewed"] is True

    service.delete_saved_search(saved["id"])
    assert service.list_saved_searches() == []
    with pytest.raises(LookupError):
        service.get_saved_search(saved["id"])


def test_item_relations_are_explainable_renderer_recommendations(tmp_path):
    database, first, second, third = _catalog(tmp_path)
    discovery = DiscoveryService(database)
    rebuilt = discovery.rebuild_item_relations()
    assert rebuilt["relations"] >= 2
    related = discovery.related_items(first)
    assert related
    assert all(item["id"] != first for item in related)
    assert all(item["reason"] for item in related)


def test_gallery_detail_has_a_source_launch_and_related_items_before_rebuild(tmp_path):
    database, first, second, third = _catalog(tmp_path)
    app = Flask(
        "catalog-gallery-detail",
        template_folder=str(Path(__file__).parents[1] / "templates"),
        static_folder=str(Path(__file__).parents[1] / "static"),
    )
    app.config.update(
        TESTING=True,
        FLOWINONE_RESOURCE_DB_PATH=str(database.path),
        FLOWINONE_RESOURCE_LINK_THUMBNAILS=False,
    )
    register_routes(app)
    client = app.test_client()

    bookmark = client.get(f"/api/catalog/items/{first}?source=bookmarks").get_json()
    resource = client.get(f"/api/catalog/items/{first}?source=resources").get_json()
    related_response = client.get(f"/api/catalog/items/{first}/related?source=bookmarks")

    assert bookmark["launch_uri"] == "https://example.com/one"
    assert resource["launch_uri"] == "/resources/resource-1/"
    assert related_response.status_code == 200
    related = related_response.get_json()["items"]
    assert {item["id"] for item in related} == {second, third}
    assert all(item["launch_uri"] and item["reason"]["shared_tags"] for item in related)


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
    database.set_catalog_sync_status("local", status="complete", item_count=1, retry_count=0)
    database.set_catalog_sync_status("eagle", status="retrying", next_attempt=2, max_attempts=4)
    database.set_catalog_sync_status("bookmarks", status="failed", error="bookmark source failed")
    database.set_catalog_sync_status("resources", status="syncing", attempt=1, max_attempts=4)
    saved = CatalogService(database).save_search(
        "未看書籤",
        {"scope": "gallery", "sources": ["bookmarks"], "unviewed": True},
    )
    app = Flask("catalog-next", template_folder=str(Path(__file__).parents[1] / "templates"), static_folder=str(Path(__file__).parents[1] / "static"))
    app.config.update(TESTING=True, FLOWINONE_RESOURCE_DB_PATH=str(tmp_path / "web.db"), FLOWINONE_RESOURCE_LINK_THUMBNAILS=False)
    register_routes(app)
    client = app.test_client()
    gallery = client.get("/navigator/?scope=gallery")
    assert gallery.status_code == 200
    assert b"Cross-source navigator" in gallery.data
    assert gallery.data.count(b'type="checkbox" name="source"') == 3
    sync_rows = BeautifulSoup(gallery.data, "html.parser").select("[data-catalog-sync-source]")
    assert [row["data-status"] for row in sync_rows] == [
        "complete",
        "retrying",
        "failed",
        "syncing",
    ]
    assert [row.select_one("strong").get_text(strip=True) for row in sync_rows] == [
        "成功",
        "重試中",
        "最終失敗",
        "同步中",
    ]
    retry_buttons = [
        row.select_one("[data-catalog-sync-retry]") for row in sync_rows
    ]
    assert [button["data-catalog-sync-retry"] for button in retry_buttons] == [
        "local",
        "eagle",
        "bookmarks",
        "resources",
    ]
    assert [button.has_attr("hidden") for button in retry_buttons] == [
        True,
        True,
        False,
        True,
    ]
    assert retry_buttons[2]["aria-label"] == "重試書籤同步"
    assert BeautifulSoup(gallery.data, "html.parser").select_one(
        'script[src$="/static/js/navigator_sync.js"]'
    )
    assert BeautifulSoup(gallery.data, "html.parser").select_one(
        'script[src$="/static/js/navigator_commands.js"]'
    )
    assert BeautifulSoup(gallery.data, "html.parser").select_one(
        'script[src$="/static/js/navigator_watch.js"]'
    )
    assert BeautifulSoup(gallery.data, "html.parser").select_one(
        'script[src$="/static/js/navigator_similarity.js"]'
    )
    assert BeautifulSoup(gallery.data, "html.parser").select_one(
        "[data-catalog-similarity-status]"
    )
    saved_card = BeautifulSoup(gallery.data, "html.parser").select_one(
        f'[data-saved-search-id="{saved["id"]}"]'
    )
    assert saved_card is not None
    assert saved_card.select_one("a").get_text(" ", strip=True).startswith("素材 未看書籤")
    assert BeautifulSoup(gallery.data, "html.parser").select_one(
        "[data-command-palette]"
    )
    status_payload = client.get("/api/catalog/sync/status").get_json()["sources"]
    assert status_payload["eagle"]["status"] == "retrying"
    assert status_payload["bookmarks"]["error"] == "bookmark source failed"
    watch_status = client.get("/api/catalog/watch")
    assert watch_status.status_code == 200
    assert watch_status.get_json()["enabled"] is False
    enabled_watch = client.post("/api/catalog/watch", json={"enabled": True})
    assert enabled_watch.status_code == 200
    assert enabled_watch.get_json()["enabled"] is True
    parsed_gallery = BeautifulSoup(gallery.data, "html.parser")
    assert parsed_gallery.select_one("form.search-container") is None
    gallery_search = parsed_gallery.select_one("form.navigator-query")
    assert gallery_search is not None
    assert gallery_search.select_one('input[name="scope"]')["value"] == "gallery"

    all_content = client.get("/navigator/?scope=all")
    assert all_content.status_code == 200
    assert all_content.data.count(b'type="checkbox" name="source"') == 4
    all_search = BeautifulSoup(all_content.data, "html.parser").select_one(
        "form.navigator-query"
    )
    assert all_search is not None
    assert all_search.select_one('input[name="scope"]')["value"] == "all"

    assert client.get("/api/catalog/items").status_code == 200
    bookmark_search = client.get("/navigator/?scope=all&source=bookmarks")
    assert b"https://example.com/web" in bookmark_search.data
    assert b"/resources/resource-web/" not in bookmark_search.data
    explained_search = client.get("/navigator/?scope=all&source=bookmarks&q=web")
    assert "命中：標題" in explained_search.get_data(as_text=True)
    explained_api = client.get("/api/catalog/items?source=bookmarks&q=web")
    assert explained_api.status_code == 200
    assert explained_api.get_json()["items"][0]["match_reason"] == {
        "field": "title",
        "label": "標題",
        "excerpt": "Web resource",
    }

    created = client.post(
        "/api/catalog/saved-searches",
        json={
            "label": "Web bookmarks",
            "query": {
                "q": "web",
                "scope": "all",
                "sources": ["bookmarks"],
                "sort": "relevance",
            },
        },
    )
    assert created.status_code == 201
    created_payload = created.get_json()
    assert created_payload["label"] == "Web bookmarks"
    assert created_payload["query"]["sources"] == ["bookmarks"]
    saved_list = client.get("/api/catalog/saved-searches?limit=20")
    assert saved_list.status_code == 200
    assert {item["id"] for item in saved_list.get_json()["items"]} == {
        saved["id"],
        created_payload["id"],
    }
    deleted = client.delete(f'/api/catalog/saved-searches/{created_payload["id"]}')
    assert deleted.status_code == 200
    assert deleted.get_json() == {"id": created_payload["id"]}


def test_catalog_browse_metadata_cache_reuses_paging_and_is_filter_keyed(tmp_path):
    """A repeated Navigator browse must not repeat COUNT/facet aggregation."""
    database, _first, _second, _third = _catalog(tmp_path)
    query = CatalogQuery.create(
        scope="all", sources=["bookmarks", "eagle", "local", "resources"],
        q="visual", limit=1,
    )
    statements: list[str] = []

    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(" ".join(statement.split()))

    event.listen(database.engine, "before_cursor_execute", capture)
    try:
        first = CatalogService(database).list(query)
        assert first["total_estimate"] == 2
        assert first["next_cursor"]

        statements.clear()
        next_page = CatalogService(database).list(
            CatalogQuery.create(
                scope="all",
                sources=["bookmarks", "eagle", "local", "resources"],
                q="visual",
                limit=1,
                cursor=first["next_cursor"],
            )
        )
        assert next_page["total_estimate"] == 2
        assert not any("COUNT(DISTINCT i.id)" in statement for statement in statements)
        assert not any("GROUP BY source_kind" in statement for statement in statements)
        assert not any("GROUP BY t.id" in statement for statement in statements)
        assert not any("GROUP BY item_type" in statement for statement in statements)

        statements.clear()
        filtered = CatalogService(database).list(
            CatalogQuery.create(
                scope="all",
                sources=["bookmarks", "eagle", "local", "resources"],
                q="board",
                limit=1,
            )
        )
        assert filtered["total_estimate"] == 1
        assert any("COUNT(DISTINCT i.id)" in statement for statement in statements)
    finally:
        event.remove(database.engine, "before_cursor_execute", capture)


def test_catalog_cold_cursor_keeps_unpaged_total(tmp_path):
    database, _first, _second, _third = _catalog(tmp_path)
    service = CatalogService(database)
    first = service.list(CatalogQuery.create(sort="title", limit=1))
    service._browse_cache.clear()

    next_page = service.list(
        CatalogQuery.create(sort="title", limit=1, cursor=first["next_cursor"])
    )

    assert len(next_page["items"]) == 1
    assert next_page["total_estimate"] == first["total_estimate"] == 3


def test_catalog_browse_metadata_cache_invalidates_after_sync(monkeypatch, tmp_path):
    database, _first, _second, local_id = _catalog(tmp_path)
    query = CatalogQuery.create(scope="gallery", sources=["local"])
    service = CatalogService(database)
    warmed = service.list(query)
    assert warmed["total_estimate"] == 1
    assert warmed["facets"]["sources"]["local"] == 1

    def sync_two_local_items(sync, conn):
        sync._upsert(
            conn,
            identity_key="local:three",
            source_kind="local",
            source_key="three",
            item_type="image",
            title="Knowledge reference image",
            detail_uri="/image/three.jpg",
        )
        sync._upsert(
            conn,
            identity_key="local:four",
            source_kind="local",
            source_key="four",
            item_type="image",
            title="Freshly synchronized image",
            detail_uri="/image/four.jpg",
        )
        return 2

    monkeypatch.setattr(CatalogSyncService, "_sync_local", sync_two_local_items)
    assert CatalogSyncService(database).sync(("local",))["local"]["count"] == 2

    statements: list[str] = []

    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(" ".join(statement.split()))

    event.listen(database.engine, "before_cursor_execute", capture)
    try:
        refreshed = CatalogService(database).list(query)
    finally:
        event.remove(database.engine, "before_cursor_execute", capture)

    assert local_id in {item["id"] for item in refreshed["items"]}
    assert refreshed["total_estimate"] == 2
    assert refreshed["facets"]["sources"]["local"] == 2
    assert any("COUNT(DISTINCT i.id)" in statement for statement in statements)
    assert any("GROUP BY source_kind" in statement for statement in statements)
