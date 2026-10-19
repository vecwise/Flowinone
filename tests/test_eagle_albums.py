"""Eagle folders are searchable albums; their direct images remain independent."""

import copy
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup
import pytest
from sqlalchemy import text

from run import create_app
from src.eagle_api.models import EaglePage
from src.flowinone.catalog import eagle_sync, eagle_library
from src.flowinone.catalog.browse import CatalogService
from src.flowinone.catalog.eagle_library import EagleLibraryService
from src.flowinone.catalog.query import CatalogQuery
from src.flowinone.catalog.sync import CatalogSyncService
from src.flowinone.resource_library.database import get_resource_database
from src.flowinone.web.browse_sources import COOKIE_NAME

TITLE = "夏日森林自然攝影作品"


@pytest.fixture
def albums(tmp_path, monkeypatch):
    source = {"identity": "album-library", "name": "Albums", "version": "1"}
    signature = hashlib.sha256(source["identity"].encode()).hexdigest()
    folders = {
        "parent": {"name": "分類", "parent_id": None, "description": ""},
        "work": {"name": TITLE, "parent_id": "parent", "description": "拍攝筆記"},
        "child": {"name": "另一章", "parent_id": "work", "description": ""},
        "empty": {"name": "空資料夾", "parent_id": None, "description": ""},
        "videos": {"name": "影片資料夾", "parent_id": None, "description": ""},
    }
    items = [{"id": f"page-{page}", "name": f"{page}.jpg", "media_type": "image", "ext": "jpg",
              "folders": ["work"], "tags": ["nature"] if page == 2 else [],
              "thumbnail_route": f"/static/default_thumbnail.svg?page={page}",
              "captured_at": "2026-10-01", "modified_at": "1"}
             for page in range(100, 0, -1)]
    items.extend([
        {"id": "child-page", "name": "child.jpg", "media_type": "image", "folders": ["child"]},
        {"id": "loose", "name": "loose.jpg", "media_type": "image", "folders": []},
        {"id": "video", "name": "video", "media_type": "video", "folders": ["work", "videos"]},
    ])
    folder_calls = []

    def folder_snapshot(*, detailed=False):
        folder_calls.append(detailed)
        data = copy.deepcopy(folders) if detailed else {key: value["name"] for key, value in folders.items()}
        return hashlib.sha256(source["identity"].encode()).hexdigest(), data

    def page(offset=0, limit=500):
        return EaglePage(items=copy.deepcopy(items[offset:offset + limit]), total=len(items), offset=offset, limit=limit)

    for module in (eagle_sync, eagle_library):
        monkeypatch.setattr(module, "get_eagle_catalog_source", lambda **_: dict(source))
        monkeypatch.setattr(module, "get_eagle_catalog_page", page)
        monkeypatch.setattr(module, "get_eagle_catalog_folders", folder_snapshot)
    monkeypatch.setattr("src.flowinone.catalog.targets.get_eagle_catalog_folders", folder_snapshot)
    monkeypatch.setattr("src.flowinone.catalog.blueprint.is_eagle_available", lambda: True)
    monkeypatch.setattr("src.flowinone.web.common.compute_feature_flags",
                        lambda: {"eagle": True, "chrome": False, "youtube": False, "db": False})
    app = create_app({"TESTING": True, "FLOWINONE_CATALOG_SYNC_INLINE": False,
                      "FLOWINONE_RESOURCE_DB_PATH": str(tmp_path / "albums.db"),
                      "FLOWINONE_RESOURCE_LINK_THUMBNAILS": False})
    db = get_resource_database(Path(app.config["FLOWINONE_RESOURCE_DB_PATH"]), migrate=True)
    sync, catalog = CatalogSyncService(db), CatalogService(db)
    library = EagleLibraryService(db)
    library.pin(source["identity"])
    result = sync.sync(["eagle"])["eagle"]
    assert result["status"] == "complete", result
    return app, app.test_client(), db, sync, catalog, source, folders, items, signature, folder_calls


def album(catalog, **query):
    page = catalog.list(CatalogQuery.create(media="album", sources=["eagle"], q=TITLE, **query))
    assert page["total_estimate"] == 1
    return page["items"][0]


def test_direct_image_folders_are_albums_with_stable_covers_and_no_copied_members(albums):
    _app, _client, db, _sync, catalog, _source, _folders, _items, _sig, calls = albums
    assert calls == [True, True, True]
    page = catalog.list(CatalogQuery.create(media="album", sources=["eagle"]))
    assert {item["title"] for item in page["items"]} == {TITLE, "另一章"}
    work = album(catalog)
    assert work["item_type"] == "album"
    assert work["thumbnail_ref"].endswith("?page=1")
    assert catalog.list(CatalogQuery.create(media="image"))["total_estimate"] == 102
    assert catalog.list(CatalogQuery.create(media="video"))["total_estimate"] == 1
    assert catalog.list(CatalogQuery.create())["total_estimate"] == 105
    with db.engine.connect() as conn:
        origin = conn.execute(text("SELECT metadata_json FROM catalog_origins WHERE catalog_item_id=:id"), {"id": work["id"]}).scalar()
    metadata = json.loads(origin)
    assert metadata["image_count"] == 100
    assert "folders" not in metadata


@pytest.mark.parametrize("query", [TITLE, "森林", "自然攝影", "夏日 作品", "作品 夏日"])
def test_chinese_title_search_finds_one_album_before_pagination(albums, query):
    _app, client, _db, _sync, catalog, *_rest = albums
    page = catalog.list(CatalogQuery.create(q=query, media="album", sort="relevance", limit=1))
    assert page["total_estimate"] == 1
    assert page["items"][0]["title"] == TITLE
    assert page["next_cursor"] is None
    assert catalog.list(CatalogQuery.create(q=query, media="image"))["total_estimate"] == 0
    api = client.get("/api/catalog/items", query_string={"q": query, "media": "album"})
    assert api.status_code == 200 and api.json["total_estimate"] == 1


def test_album_card_opens_all_images_without_outer_filters_and_returns_to_search(albums):
    _app, client, _db, _sync, catalog, _source, _folders, _items, signature, _calls = albums
    response = client.get("/navigator/", query_string={"q": TITLE, "media": "album", "source": "eagle", "tags": "nature", "sort": "title", "view": "list"})
    assert response.status_code == 200
    soup = BeautifulSoup(response.data, "html.parser")
    assert len(soup.select('[data-navigator-item]')) == 1
    card = soup.select_one('[data-card-type="album"]')
    assert card.select_one('[data-gallery-detail-open]') is None
    assert card.select_one('.gallery-album-count').get_text(strip=True) == "100 張圖片"
    target = card.select_one('.gallery-card-media')["href"]
    params = parse_qs(urlsplit(target).query)
    assert not {"q", "tags", "favorite", "cursor", "type"} & params.keys()
    assert params["media"] == ["image"] and params["sort"] == ["page_order"]
    assert params["target"] == [f"eagle_folder:{signature}:work"]
    seen = []
    while target:
        contents = client.get(target)
        assert contents.status_code == 200
        page = BeautifulSoup(contents.data, "html.parser")
        assert page.select_one('h1').get_text(strip=True) == TITLE
        assert page.select_one('[data-album-return]') is not None
        seen.extend(item.select_one('h3').get_text(strip=True) for item in page.select('[data-navigator-item]'))
        next_link = page.select_one('.gallery-pagination a')
        target = next_link["href"] if next_link else None
    assert seen == [f"{number}.jpg" for number in range(1, 101)]
    back = client.get(page.select_one('[data-album-return]')["href"])
    assert len(BeautifulSoup(back.data, "html.parser").select('[data-card-type="album"]')) == 1


def test_rename_and_member_changes_keep_identity_and_refresh_derived_tags_cover_and_count(albums):
    _app, _client, _db, sync, catalog, _source, folders, items, _sig, _calls = albums
    original = album(catalog)
    catalog.record_event(original["id"], "favorite")
    folders["work"]["name"] = "更名作品"
    items[:] = [row for row in items if row["id"] not in {"page-1", "page-2"}]
    result = sync.sync(["eagle"])["eagle"]
    assert result["status"] == "complete", result
    updated = catalog.get(original["id"])
    assert updated["title"] == "更名作品" and updated["favorite"]
    assert updated["thumbnail_ref"].endswith("?page=3")
    assert "nature" not in updated["tags"]
    assert catalog.list(CatalogQuery.create(q=TITLE, media="album"))["total_estimate"] == 0
    assert catalog.list(CatalogQuery.create(q="更名", media="album"))["total_estimate"] == 1
    assert json.loads(updated["origins"][0]["metadata_json"])["image_count"] == 98


def test_incremental_sync_skips_unchanged_albums_and_cleanup_keeps_live_folders(albums, monkeypatch):
    _app, _client, _db, sync, catalog, _source, _folders, _items, _sig, _calls = albums
    original_upsert = sync._upsert
    upserts = []
    def record(conn, **values):
        upserts.append(values["source_key"])
        return original_upsert(conn, **values)
    monkeypatch.setattr(sync, "_upsert", record)
    result = sync.sync(["eagle"])["eagle"]
    assert result["status"] == "complete" and result["album_count"] == 2
    assert not upserts
    preview = EagleLibraryService(sync.database).preview_cleanup()
    assert preview["count"] == 0
    assert album(catalog)["title"] == TITLE


def test_deleted_or_empty_folders_disappear_and_cleanup_preserves_images(albums):
    _app, _client, db, sync, catalog, _source, folders, items, _sig, _calls = albums
    work_id = album(catalog)["id"]
    del folders["work"]
    result = sync.sync(["eagle"])["eagle"]
    assert result["status"] == "complete", result
    assert catalog.list(CatalogQuery.create(media="album", q=TITLE))["total_estimate"] == 0
    library = EagleLibraryService(db)
    preview = library.preview_cleanup()
    assert preview["count"] == 1
    cleanup = library.cleanup(preview["token"])
    assert cleanup["deleted_items"] == 1
    assert catalog.list(CatalogQuery.create(media="image"))["total_estimate"] == 102
    with pytest.raises(LookupError):
        catalog.get(work_id)


def test_interrupted_scan_does_not_remove_existing_albums(albums, monkeypatch):
    _app, _client, db, sync, catalog, _source, folders, _items, _sig, _calls = albums
    work_id = album(catalog)["id"]
    monkeypatch.setattr(eagle_sync, "EAGLE_SYNC_PAGE_SIZE", 50)
    real_page = eagle_sync.get_eagle_catalog_page
    def interrupted(offset=0, limit=50):
        if offset == 50:
            raise RuntimeError("Interrupted fixture")
        return real_page(offset=offset, limit=limit)
    monkeypatch.setattr(eagle_sync, "get_eagle_catalog_page", interrupted)
    folders["work"]["name"] = "稍後更新"
    assert sync.sync(["eagle"])["eagle"]["status"] == "failed"
    assert catalog.get(work_id)["title"] == TITLE
    monkeypatch.setattr(eagle_sync, "get_eagle_catalog_page", real_page)
    result = sync.sync(["eagle"])["eagle"]
    assert result["status"] == "complete" and result["resumed"]
    assert catalog.get(work_id)["title"] == "稍後更新"


def test_folder_change_during_scan_does_not_commit_album_updates(albums, monkeypatch):
    _app, _client, _db, sync, catalog, _source, folders, _items, _sig, _calls = albums
    real_page = eagle_sync.get_eagle_catalog_page
    def changed(**values):
        folders["work"]["name"] = "掃描中更名"
        return real_page(**values)
    monkeypatch.setattr(eagle_sync, "get_eagle_catalog_page", changed)
    assert sync.sync(["eagle"])["eagle"]["status"] == "failed"
    assert album(catalog)["title"] == TITLE


def test_albums_respect_sources_and_library_protection(albums):
    _app, client, _db, _sync, catalog, source, _folders, _items, _sig, _calls = albums
    client.set_cookie(COOKIE_NAME, "local")
    page = client.get("/navigator/?media=album", follow_redirects=True)
    assert not BeautifulSoup(page.data, "html.parser").select('[data-card-type="album"]')
    source["identity"] = "foreign-library"
    assert not catalog.list(CatalogQuery.create(media="album"))["items"]


def test_moving_images_between_folders_refreshes_counts_and_removes_empty_albums(albums):
    _app, _client, _db, sync, catalog, _source, _folders, items, _sig, _calls = albums
    work_id = album(catalog)["id"]
    for row in items:
        if row["media_type"] == "image" and row["folders"] == ["work"]:
            row["folders"] = ["child"]
    result = sync.sync(["eagle"])["eagle"]
    assert result["status"] == "complete", result
    assert result["album_count"] == 1
    with pytest.raises(LookupError):
        catalog.get(work_id)
    remaining = catalog.list(CatalogQuery.create(media="album"))["items"]
    assert len(remaining) == 1 and remaining[0]["title"] == "另一章"
    origin = catalog.get(remaining[0]["id"])["origins"][0]
    assert json.loads(origin["metadata_json"])["image_count"] == 101
    assert catalog.list(CatalogQuery.create(media="image"))["total_estimate"] == 102


def test_full_rescan_repairs_album_search_documents(albums):
    _app, _client, db, sync, catalog, *_rest = albums
    work_id = album(catalog)["id"]
    with db.write_transaction() as conn:
        conn.execute(text("DELETE FROM catalog_fts WHERE catalog_item_id=:id"), {"id": work_id})
    result = sync.sync(["eagle"], full_rescan=True)["eagle"]
    assert result["status"] == "complete", result
    assert album(catalog)["id"] == work_id
    with db.engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM catalog_fts WHERE catalog_item_id=:id"), {"id": work_id}).scalar() == 1


def test_folder_snapshot_keeps_names_descriptions_and_parent_identity(monkeypatch):
    from src.file_handler import eagle_integration
    monkeypatch.setattr(eagle_integration, "_get_eagle_library_info", lambda: {
        "status": "success", "data": {"id": "tree", "folders": [{
            "id": "parent", "name": "分類", "children": [{"id": "work", "name": TITLE, "description": "說明"}],
        }]},
    })
    signature, folders = eagle_integration.get_eagle_catalog_folders(detailed=True)
    assert signature == hashlib.sha256(b"tree").hexdigest()
    assert folders["work"] == {"name": TITLE, "parent_id": "parent", "description": "說明"}
    assert eagle_integration.get_eagle_catalog_folders()[1]["work"] == TITLE
