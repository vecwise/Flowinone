"""Repeatable local measurements for Catalog browse and projection paths."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup
from sqlalchemy import event

from run import create_app
from src.flowinone.catalog import sync as catalog_sync
from src.flowinone.catalog.service import CatalogQuery, CatalogService, CatalogSyncService
from src.flowinone.resource_library.database import get_resource_database


def _catalog_100k(tmp_path):
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
    return database


@pytest.mark.performance
def test_catalog_100k_keyset_query_contract(tmp_path):
    database = _catalog_100k(tmp_path)
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
def test_navigator_home_100k_topics_query_contract(tmp_path, monkeypatch):
    database = _catalog_100k(tmp_path)
    now = "2026-10-01"
    with database.engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO catalog_tags(id,name,normalized_name,created_at) VALUES(?,?,?,?)",
            [(f"t{index}", f"Topic {index}", f"topic {index}", now) for index in range(6)],
        )
        conn.exec_driver_sql(
            "INSERT INTO catalog_item_tags(catalog_item_id,tag_id,source,created_at) VALUES(?,?,?,?)",
            [(f"{index:032x}", f"t{index % 6}", "source", now) for index in range(100_000)],
        )
    monkeypatch.setattr("src.flowinone.catalog.blueprint.is_eagle_available", lambda: False)
    monkeypatch.setattr("src.flowinone.web.common.compute_feature_flags", lambda: {})
    client = create_app({
        "TESTING": True, "FLOWINONE_RESOURCE_DB_PATH": str(database.path),
        "FLOWINONE_RESOURCE_LINK_THUMBNAILS": False, "FLOWINONE_CATALOG_SYNC_INLINE": False,
    }).test_client()
    statements = []

    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(" ".join(statement.split()))

    timings = []
    event.listen(database.engine, "before_cursor_execute", capture)
    try:
        for _ in range(2):
            statements.clear()
            started = time.perf_counter()
            response = client.get("/navigator/?scope=all&source=local")
            timings.append(time.perf_counter() - started)
            assert response.status_code == 200
            soup = BeautifulSoup(response.data, "html.parser")
            assert len(soup.select(".navigator-topic-card")) == 3
            assert len(soup.select("[data-topic-item]")) == 9
            assert len(soup.select(".navigator-topic-list a.navigator-chip")) == 6
            # One result page plus three previews; remaining chips need only totals.
            assert sum(sql.startswith("SELECT i.*,") for sql in statements) == 4
        assert not any("COUNT(DISTINCT i.id)" in sql for sql in statements)
    finally:
        event.remove(database.engine, "before_cursor_execute", capture)
    print(f"navigator_home_100k_topics cold={timings[0]:.3f}s warm={timings[1]:.3f}s")
    assert timings[0] < 2.5
    assert timings[1] < 0.75


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
