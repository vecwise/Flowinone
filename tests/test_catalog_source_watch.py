"""Contracts for opt-in Catalog source change detection."""

from __future__ import annotations

import json

from sqlalchemy import text

from src.flowinone.catalog.service import CatalogSyncService
from src.flowinone.catalog.watch import CatalogSourceWatcher
from src.flowinone.resource_library.database import get_resource_database
from src.flowinone.resource_library.jobs import JobQueue
from src.flowinone.resource_library.worker import ResourceWorker


def test_watcher_baselines_then_queues_one_debounced_source_change(tmp_path):
    database = get_resource_database(tmp_path / "watch.db")
    observed = {"bookmarks": "first"}
    watcher = CatalogSourceWatcher(
        database,
        signatures={"bookmarks": lambda: observed["bookmarks"]},
        debounce_seconds=0,
    )
    watcher.set_enabled(True)

    baseline = watcher.process_once()
    assert baseline["sources"]["bookmarks"]["state"] == "watching"
    assert JobQueue(database).counts() == {}

    observed["bookmarks"] = "second"
    changed = watcher.process_once()
    state = changed["sources"]["bookmarks"]
    assert state["state"] == "queued"
    assert state["job_status"] == "pending"

    # Re-observing the same signature must reuse the durable queue key rather
    # than schedule a second competing projection.
    watcher.process_once()
    with database.engine.connect() as conn:
        jobs = list(
            conn.execute(
                text("SELECT payload_json FROM processing_jobs WHERE job_type='catalog_sync'")
            ).scalars()
        )
    assert len(jobs) == 1
    assert json.loads(jobs[0]) == {
        "full_rescan": False,
        "source_signature": "second",
        "sources": ["bookmarks"],
        "trigger": "source_watcher",
    }


def test_watcher_can_be_disabled_without_calling_source_providers(tmp_path):
    database = get_resource_database(tmp_path / "watch-disabled.db")
    calls = 0

    def signature():
        nonlocal calls
        calls += 1
        return "one"

    watcher = CatalogSourceWatcher(database, signatures={"bookmarks": signature})
    watcher.set_enabled(False)
    status = watcher.process_once()

    assert status["enabled"] is False
    assert calls == 0
    assert status["sources"]["bookmarks"] == {}


def test_watcher_marks_unavailable_sources_without_queueing(tmp_path):
    database = get_resource_database(tmp_path / "watch-unavailable.db")
    watcher = CatalogSourceWatcher(
        database,
        signatures={"bookmarks": lambda: None},
    )
    watcher.set_enabled(True)

    status = watcher.process_once()
    assert status["sources"]["bookmarks"]["state"] == "unavailable"
    assert JobQueue(database).counts() == {}


def test_local_watch_job_reindexes_before_incremental_catalog_sync(monkeypatch, tmp_path):
    database = get_resource_database(tmp_path / "watch-local-job.db")
    calls = []
    monkeypatch.setattr(
        "src.file_handler.item_db.update_item_database",
        lambda: calls.append("index") or {"errors": []},
    )
    monkeypatch.setattr(
        CatalogSyncService,
        "sync",
        lambda _service, sources, full_rescan=False: calls.append(
            (tuple(sources), full_rescan)
        )
        or {"local": {"status": "complete", "count": 1}},
    )
    JobQueue(database).queue(
        "catalog_sync",
        resource_id=None,
        payload={"sources": ["local"], "refresh_local_index": True},
        input_hash="watch-local",
    )

    assert ResourceWorker(database, max_workers=1).run_until_idle(max_jobs=1) == 1
    assert calls == ["index", (("local",), False)]
