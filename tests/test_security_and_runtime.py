from __future__ import annotations

from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from sqlalchemy import event

from run import create_app
from src.flowinone.catalog.blueprint import _visible_items
from src.flowinone.catalog.service import CatalogQuery, CatalogService, CatalogSyncService
from src.flowinone.resource_library.database import (
    DatabaseUpgradeRequired,
    backup_database,
    get_resource_database,
)
from src.flowinone.resource_library.repository import ResourceRepository
from src.flowinone.resource_library.worker import ResourceWorker


def _app(tmp_path: Path, **overrides):
    config = {
        "TESTING": True,
        "FLOWINONE_RESOURCE_DB_PATH": str(tmp_path / "flowinone.db"),
        "FLOWINONE_RESOURCE_LINK_THUMBNAILS": False,
        "FLOWINONE_MEDIA_ROOTS": [str(tmp_path / "allowed")],
    }
    config.update(overrides)
    return create_app(config)


def test_local_request_boundary_file_containment_and_mutation_methods(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    inside = allowed / "inside.png"
    outside = tmp_path / "outside.png"
    inside.write_bytes(b"not-a-real-png")
    outside.write_bytes(b"not-a-real-png")
    escape = allowed / "escape.png"
    escape.symlink_to(outside)
    client = _app(tmp_path).test_client()

    inside_route = f"/serve_image/{str(inside).lstrip('/')}"
    outside_route = f"/serve_image/{str(outside).lstrip('/')}"
    assert client.get(inside_route).status_code == 200
    assert client.get(outside_route).status_code == 403
    assert client.get(f"/serve_image/{str(escape).lstrip('/')}").status_code == 403
    assert client.get("/serve_image/etc/hosts").status_code != 200
    assert client.get("/update_db").status_code == 405
    assert client.post("/update_db").status_code == 403
    assert client.post(
        "/api/catalog/sync",
        json={"sources": ["local"]},
        headers={"Origin": "https://attacker.example"},
    ).status_code == 403
    assert client.get(
        "/navigator/", base_url="http://flowinone.invalid"
    ).status_code == 403


def test_loading_missing_config_does_not_create_a_file(monkeypatch, tmp_path):
    import config

    missing = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_JSON_PATH", missing)
    assert config._load_config() == config.DEFAULT_CONFIG
    assert not missing.exists()


def test_rendered_form_with_session_csrf_token_can_submit(tmp_path):
    client = _app(tmp_path).test_client()
    page = client.get("/resources/")
    token = BeautifulSoup(page.data, "html.parser").select_one(
        'form[action="/resources/"] input[name="csrf_token"]'
    )["value"]
    response = client.post(
        "/resources/",
        data={
            "csrf_token": token,
            "url": "https://example.com/csrf-contract",
            "title": "CSRF contract",
            "enqueue": "0",
        },
    )
    assert response.status_code == 302


def test_debug_and_gallery_lab_are_not_registered_in_normal_runtime(tmp_path):
    client = _app(tmp_path, FLOWINONE_DEV_TOOLS=False).test_client()
    assert client.get("/debug/").status_code == 404
    assert client.get("/gallery/lab").status_code == 404


def test_runtime_refuses_to_implicitly_create_or_upgrade_database(tmp_path):
    with pytest.raises(DatabaseUpgradeRequired):
        get_resource_database(tmp_path / "missing.db", migrate=False)


def test_database_backup_is_consistent_before_manual_upgrade(tmp_path):
    database = get_resource_database(tmp_path / "source.db")
    with database.write_transaction() as connection:
        connection.exec_driver_sql(
            "INSERT INTO runtime_state(component,heartbeat_at) VALUES('test','now')"
        )
    backup = backup_database(database.path)
    assert backup is not None and backup.is_file()
    import sqlite3

    with sqlite3.connect(backup) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute(
            "SELECT component FROM runtime_state"
        ).fetchone() == ("test",)


def test_catalog_sync_can_run_as_a_durable_worker_job(monkeypatch, tmp_path):
    app = _app(tmp_path, FLOWINONE_CATALOG_SYNC_INLINE=False)
    client = app.test_client()
    queued = client.post(
        "/api/catalog/sync", json={"sources": ["bookmarks"]}
    )
    assert queued.status_code == 202
    job_id = queued.get_json()["job"]["id"]

    monkeypatch.setattr(
        CatalogSyncService,
        "sync",
        lambda _service, sources, full_rescan=False: {
            source: {"status": "complete", "count": 1} for source in sources
        },
    )
    database = get_resource_database(tmp_path / "flowinone.db")
    assert ResourceWorker(database, max_workers=1).run_until_idle(max_jobs=1) == 1
    completed = client.get(f"/api/catalog/sync/jobs/{job_id}")
    assert completed.status_code == 200
    assert completed.get_json()["job"]["status"] == "complete"


def test_navigator_origin_decoration_uses_one_batch_query(tmp_path):
    database = get_resource_database(tmp_path / "batch.db")
    sync = CatalogSyncService(database)
    with database.write_transaction() as connection:
        item_ids = [
            sync._upsert(
                connection,
                identity_key=f"local:{index}",
                source_kind="local",
                source_key=str(index),
                item_type="image",
                title=f"Item {index}",
                detail_uri=f"/image/{index}.jpg",
            )
            for index in range(5)
        ]
    statements = []

    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        if "catalog_origins" in statement:
            statements.append(statement)

    event.listen(database.engine, "before_cursor_execute", capture)
    try:
        payload = {
            "items": [
                {"id": item_id, "sources": ["local"], "title": f"Item {index}"}
                for index, item_id in enumerate(item_ids)
            ]
        }
        visible = _visible_items(
            CatalogService(database),
            payload,
            CatalogQuery.create(sources=["local"]),
        )
    finally:
        event.remove(database.engine, "before_cursor_execute", capture)
    assert len(visible) == 5
    assert len(statements) == 1


def test_worker_heartbeat_is_reflected_in_resource_stats(tmp_path):
    database = get_resource_database(tmp_path / "heartbeat.db")
    worker = ResourceWorker(database, max_workers=1)
    worker._heartbeat()
    assert ResourceRepository(database).stats()["worker_online"] is True
