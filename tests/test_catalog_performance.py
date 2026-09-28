"""Repeatable local measurements for Catalog browse and projection paths."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from src.flowinone.catalog import sync as catalog_sync
from src.flowinone.catalog.service import CatalogQuery, CatalogService, CatalogSyncService
from src.flowinone.resource_library.database import get_resource_database


@pytest.mark.performance
def test_catalog_100k_keyset_query_contract(tmp_path):
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

    query = CatalogQuery.create(scope="gallery", sources=["local"], limit=48)
    service = CatalogService(database)
    started = time.perf_counter()
    page = service.list(query)
    cold_seconds = time.perf_counter() - started
    started = time.perf_counter()
    warm_page = service.list(query)
    warm_seconds = time.perf_counter() - started

    print(f"catalog_100k cold={cold_seconds:.3f}s warm={warm_seconds:.3f}s")
    assert page["total_estimate"] == warm_page["total_estimate"] == 100_000
    assert len(page["items"]) == len(warm_page["items"]) == 48
    assert page["next_cursor"]
    assert cold_seconds < 2.5
    assert warm_seconds < 0.75


@pytest.mark.performance
def test_bookmark_projection_1000_item_baseline(monkeypatch, tmp_path):
    database = get_resource_database(tmp_path / "bookmarks-1000.db")
    records = [
        {"url": f"https://example.com/{index}", "title": f"Bookmark {index}"}
        for index in range(1_000)
    ]
    monkeypatch.setattr(catalog_sync, "iter_chrome_bookmark_records", lambda: iter(records))
    monkeypatch.setattr(
        catalog_sync,
        "lookup_thumbnail_for_bookmark",
        lambda *_args, **_kwargs: SimpleNamespace(route=None, sub_type=None),
    )

    started = time.perf_counter()
    result = CatalogSyncService(database).sync(("bookmarks",))
    elapsed = time.perf_counter() - started

    print(f"bookmark_sync_1000 elapsed={elapsed:.3f}s")
    assert result["bookmarks"]["status"] == "complete"
    assert result["bookmarks"]["count"] == 1_000
    assert CatalogService(database).count() == 1_000
