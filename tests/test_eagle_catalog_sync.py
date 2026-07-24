"""Deterministic coverage for resumable Eagle Catalog synchronization."""

from __future__ import annotations

from pathlib import Path

from bs4 import BeautifulSoup
from flask import Flask
from sqlalchemy import text

from routes import register_routes
from src.eagle_api.models import EaglePage
from src.flowinone.catalog import blueprint as catalog_blueprint
from src.flowinone.catalog import service as catalog_service
from src.flowinone.catalog.service import CatalogService, CatalogSyncService
from src.flowinone.resource_library.database import get_resource_database


def _item(
    item_id: str,
    name: str,
    *,
    modified_at: str = "1",
    tags: tuple[str, ...] = (),
) -> dict:
    return {
        "id": item_id,
        "name": name,
        "ext": "jpg",
        "media_type": "image",
        "thumbnail_route": f"/eagle/{item_id}.jpg",
        "original_url": "",
        "tags": list(tags),
        "folders": ["folder-one"],
        "description": "",
        "captured_at": "",
        "modified_at": modified_at,
    }


def _install_eagle(monkeypatch, items: list[dict], offsets: list[int] | None = None):
    monkeypatch.setattr(
        catalog_service,
        "get_eagle_catalog_source",
        lambda **_options: {"identity": "library-one", "version": "snapshot-one"},
    )

    def page(offset=0, limit=500):
        if offsets is not None:
            offsets.append(offset)
        return EaglePage(
            items=list(items[offset : offset + limit]),
            total=len(items),
            offset=offset,
            limit=limit,
        )

    monkeypatch.setattr(catalog_service, "get_eagle_catalog_page", page)


def test_eagle_incremental_sync_skips_unchanged_items_and_full_rescan_fallback(
    monkeypatch, tmp_path
):
    database = get_resource_database(tmp_path / "eagle-unchanged.db")
    items = [_item("one", "One"), _item("two", "Two", tags=("blue",))]
    _install_eagle(monkeypatch, items)
    sync = CatalogSyncService(database)

    initial = sync.sync(("eagle",))["eagle"]
    assert initial["full_rescan"] is True
    assert initial["changed"] == 2
    assert initial["skipped"] == 0

    original_upsert = sync._upsert
    upserts = []

    def record_upsert(conn, **values):
        upserts.append(values["source_key"])
        return original_upsert(conn, **values)

    monkeypatch.setattr(sync, "_upsert", record_upsert)
    incremental = sync.sync(("eagle",))["eagle"]
    assert incremental["full_rescan"] is False
    assert incremental["processed"] == incremental["total"] == 2
    assert incremental["changed"] == 0
    assert incremental["skipped"] == 2
    assert upserts == []

    with database.engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE catalog_sync_state SET status='failed',cursor_version=999,"
                "cursor_json='{}' WHERE source_kind='eagle'"
            )
        )
    fallback = sync.sync(("eagle",))["eagle"]
    assert fallback["full_rescan"] is True
    assert fallback["changed"] == 2
    assert fallback["skipped"] == 0
    assert upserts == ["one", "two"]


def test_eagle_incremental_sync_updates_changes_and_marks_deletions(
    monkeypatch, tmp_path
):
    database = get_resource_database(tmp_path / "eagle-changed-deleted.db")
    items = [
        _item("one", "Unchanged", tags=("keep",)),
        _item("two", "Old title", tags=("old",)),
        _item("three", "Will disappear"),
    ]
    _install_eagle(monkeypatch, items)
    sync = CatalogSyncService(database)
    assert sync.sync(("eagle",))["eagle"]["count"] == 3

    items[:] = [
        _item("one", "Unchanged", tags=("keep",)),
        _item("two", "New title", modified_at="2", tags=("new",)),
    ]
    result = sync.sync(("eagle",))["eagle"]

    assert result["count"] == 2
    assert result["changed"] == 1
    assert result["skipped"] == 1
    assert result["deleted"] == 1
    with database.engine.connect() as conn:
        changed = conn.execute(
            text("SELECT title FROM catalog_items WHERE identity_key='eagle:two'")
        ).scalar_one()
        deleted = conn.execute(
            text(
                "SELECT i.is_deleted,o.stale FROM catalog_items i "
                "JOIN catalog_origins o ON o.catalog_item_id=i.id "
                "WHERE i.identity_key='eagle:three'"
            )
        ).one()
        tags = set(
            conn.execute(
                text(
                    "SELECT t.name FROM catalog_tags t "
                    "JOIN catalog_item_tags it ON it.tag_id=t.id "
                    "JOIN catalog_items i ON i.id=it.catalog_item_id "
                    "WHERE i.identity_key='eagle:two'"
                )
            ).scalars()
        )
    assert changed == "New title"
    assert tuple(deleted) == (1, 1)
    assert tags == {"new"}


def test_eagle_sync_resumes_only_after_validating_the_last_committed_page(
    monkeypatch, tmp_path
):
    database = get_resource_database(tmp_path / "eagle-resume.db")
    items = [_item(str(index), f"Item {index}") for index in range(4)]
    offsets = []
    _install_eagle(monkeypatch, items, offsets)
    monkeypatch.setattr(catalog_service, "EAGLE_SYNC_PAGE_SIZE", 2)
    real_page = catalog_service.get_eagle_catalog_page
    fail_once = True

    def interrupted_page(offset=0, limit=2):
        nonlocal fail_once
        if offset == 2 and fail_once:
            fail_once = False
            offsets.append(offset)
            raise RuntimeError("Eagle temporarily unavailable")
        return real_page(offset=offset, limit=limit)

    monkeypatch.setattr(catalog_service, "get_eagle_catalog_page", interrupted_page)
    sync = CatalogSyncService(database)

    failed = sync.sync(("eagle",))["eagle"]
    assert failed["status"] == "failed"
    assert failed["processed"] == 2
    with database.engine.connect() as conn:
        cursor_json = conn.execute(
            text(
                "SELECT cursor_json FROM catalog_sync_state WHERE source_kind='eagle'"
            )
        ).scalar_one()
    assert '"next_offset":2' in cursor_json
    database.dispose()
    durable_progress = CatalogService(database).sync_status()["eagle"]
    assert durable_progress["processed"] == 2
    assert durable_progress["total"] == 4

    completed = sync.sync(("eagle",))["eagle"]
    assert completed["status"] == "complete"
    assert completed["resumed"] is True
    assert completed["processed"] == completed["total"] == 4
    assert completed["changed"] == 4
    assert offsets == [0, 2, 0, 2]
    with database.engine.connect() as conn:
        state = conn.execute(
            text(
                "SELECT status,cursor_version,cursor_json FROM catalog_sync_state "
                "WHERE source_kind='eagle'"
            )
        ).one()
    assert tuple(state) == ("complete", 1, None)


def test_navigator_sync_status_displays_processed_total_progress(monkeypatch, tmp_path):
    database = get_resource_database(tmp_path / "eagle-progress.db")
    sync = CatalogSyncService(database)
    with database.engine.begin() as conn:
        sync._upsert(
            conn,
            identity_key="local:progress",
            source_kind="local",
            source_key="progress",
            item_type="image",
            title="Progress fixture",
            detail_uri="/image/progress.jpg",
        )
    database.set_catalog_sync_status(
        "eagle",
        status="syncing",
        attempt=1,
        max_attempts=4,
        processed=125,
        total=1000,
        changed=25,
        skipped=100,
    )
    monkeypatch.setattr(catalog_blueprint, "is_eagle_available", lambda: True)
    app = Flask(
        "eagle-progress",
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
    page = BeautifulSoup(
        client.get("/navigator/?scope=gallery").data, "html.parser"
    )
    eagle_row = page.select_one('[data-catalog-sync-source="eagle"]')
    assert "125/1000 筆" in eagle_row.select_one(
        "[data-catalog-sync-detail]"
    ).get_text(" ", strip=True)
    status = client.get("/api/catalog/sync/status").get_json()["sources"]["eagle"]
    assert (status["processed"], status["total"]) == (125, 1000)
