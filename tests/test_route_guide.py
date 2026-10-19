"""The route guide stays read-only and uses source-owned, current examples."""

from urllib.parse import parse_qs, unquote, urlsplit

import pytest
from bs4 import BeautifulSoup
from sqlalchemy import event

from run import create_app
from src.flowinone import config
from src.flowinone.catalog.browse import CatalogService
from src.flowinone.catalog.query import CatalogQuery
from src.flowinone.catalog.sync import CatalogSyncService
from src.flowinone.resource_library.database import get_resource_database
from src.flowinone.web import route_guide


@pytest.fixture
def guide(tmp_path, monkeypatch):
    flags = {"eagle": False, "chrome": False, "youtube": False, "db": False}
    monkeypatch.setattr("src.flowinone.web.common.compute_feature_flags", lambda: flags)
    monkeypatch.setattr(config, "DB_route_external", str(tmp_path / "external"))
    monkeypatch.setattr(config, "DB_route_internal", str(tmp_path / "internal"))
    app = create_app({
        "TESTING": True, "FLOWINONE_RESOURCE_DB_PATH": str(tmp_path / "guide.db"),
        "FLOWINONE_RESOURCE_LINK_THUMBNAILS": False, "FLOWINONE_CATALOG_SYNC_INLINE": False,
    })
    database = get_resource_database(tmp_path / "guide.db", migrate=True)
    return app, app.test_client(), database, flags


def test_home_exposes_guide_and_empty_guide_never_writes_or_contacts_eagle(guide, monkeypatch):
    app, client, database, _ = guide

    def unexpected_request(*args, **kwargs):
        pytest.fail("An offline directory must not request Eagle data")

    monkeypatch.setattr(route_guide.eagle_client, "library_info", unexpected_request)
    monkeypatch.setattr(route_guide.eagle_client, "get_smart_folders", unexpected_request)
    monkeypatch.setattr("src.flowinone.catalog.blueprint.is_eagle_available", lambda: False)
    home = BeautifulSoup(client.get("/", follow_redirects=True).data, "html.parser")
    assert home.select_one('.app-settings a[href="/route-guide/"]')
    statements = []

    def record(_connection, _cursor, statement, *_args):
        statements.append(statement.lstrip().split()[0].upper())

    event.listen(database.engine, "before_cursor_execute", record)
    try:
        response = client.get("/route-guide/")
    finally:
        event.remove(database.engine, "before_cursor_execute", record)
    assert response.status_code == 200
    assert not {"INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP"}.intersection(statements)
    page = BeautifulSoup(response.data, "html.parser")
    assert page.select_one('main[data-page-family="manage"]')
    assert "3 種模板" in page.get_text()
    assert page.select_one("#routes-compare") is None
    assert page.select_one('.app-settings a[aria-current="page"]')["href"] == "/route-guide/"
    assert page.select_one('main form[method="post"]') is None
    assert "缺少樣本" in page.select_one('[data-route-endpoint="media.view_image"]').get_text()
    assert "來源未就緒" in page.select_one('[data-route-endpoint="eagle.eagle_stream"]').get_text()
    assert page.select_one('[data-route-endpoint="debug.debug_print"] a')["href"] == "/debug/"
    assert client.get("/debug/").status_code == 404
    represented = {row["data-route-endpoint"] for row in page.select("[data-route-endpoint]")}
    non_pages = {"static", "media.serve_image_by_full_path", "media.serve_video_by_full_path",
                 "resource_library.resource_asset", "catalog.catalog_video_playback"}
    html_endpoints = {rule.endpoint for rule in app.url_map.iter_rules()
                      if "GET" in rule.methods and not rule.rule.startswith("/api/")
                      and rule.endpoint not in non_pages}
    assert html_endpoints <= represented


def test_examples_preserve_local_source_and_encoded_paths_and_existing_resource(guide, tmp_path):
    _, client, database, flags = guide
    flags["db"] = True
    folder = tmp_path / "internal" / "#中文 folder"
    folder.mkdir(parents=True)
    (folder / "image one.png").write_bytes(b"example")
    (folder / "video one.mp4").write_bytes(b"example")
    sync = CatalogSyncService(database)
    with database.engine.begin() as connection:
        for kind, name in (("image", "image one.png"), ("video", "video one.mp4")):
            relative = f"#中文 folder/{name}"
            sync._upsert(connection, identity_key=f"local:{kind}", source_kind="local",
                         source_key=kind, source_path=relative, item_type=kind, title=name,
                         detail_uri=f"/{kind}/unused?src=internal")
        sync._upsert(connection, identity_key="local:missing", source_kind="local",
                     source_key="missing", source_path="absent.png", item_type="image",
                     title="Stale path", detail_uri="/image/absent.png?src=internal",
                     seen_at="2099-01-01T00:00:00+00:00")
    resource = client.post("/api/resources", json={
        "url": "https://example.com/route-guide", "enqueue": False,
    }).get_json()["resource"]
    saved = CatalogService(database).save_search("Test view", CatalogQuery.create(scope="all").public_dict())
    page = BeautifulSoup(client.get("/route-guide/").data, "html.parser")

    def target(endpoint):
        return urlsplit(page.select_one(f'[data-route-endpoint="{endpoint}"] a')["href"])

    for kind, name in (("image", "image one.png"), ("video", "video one.mp4")):
        url = target(f"media.view_{kind}")
        assert unquote(url.path) == f"/{kind}/#中文 folder/{name}"
        assert parse_qs(url.query) == {"src": ["internal"]}
    folder_url = target("local.view_both")
    assert unquote(folder_url.path) == "/both/#中文 folder/"
    assert parse_qs(folder_url.query) == {"src": ["internal"]}
    assert target("resource_library.resource_detail").path == resource["detail_url"]
    assert target("resource_library.resource_versions").path == resource["detail_url"] + "versions/"
    version_row = page.select_one('[data-route-endpoint="resource_library.resource_versions"]')
    assert version_row.find_parent("section")["id"] == "routes-detail"
    assert version_row.select_one('.route-guide-template code').get_text() == "detail.html"
    duplicate_row = page.select_one('[data-route-endpoint="catalog.duplicate_review_page"]')
    assert duplicate_row.find_parent("section")["id"] == "routes-manage"
    assert duplicate_row.select_one('.route-guide-template code').get_text() == "manage.html"
    assert target("catalog.navigator_saved_view").path == f'/navigator/views/{saved["id"]}/'
    for row in page.select('[data-route-method="POST"]'):
        assert row.select_one("a")["href"] == "/library/#local-maintenance"


def test_eagle_metadata_provides_real_folder_ids_without_loading_galleries(guide, monkeypatch):
    _, client, _, flags = guide
    flags["eagle"] = True
    monkeypatch.setattr(route_guide.eagle_client, "library_info", lambda: {
        "status": "success", "data": {"folders": [{"id": "folder-actual"}]},
    })
    monkeypatch.setattr(route_guide.eagle_client, "get_smart_folders", lambda **_: {
        "status": "success", "data": {"data": [{"id": "smart-actual"}]},
    })
    page = BeautifulSoup(client.get("/route-guide/").data, "html.parser")
    assert page.select_one('[data-route-endpoint="eagle.view_eagle_folder"] a')["href"] == "/EAGLE_folder/folder-actual/"
    assert page.select_one('[data-route-endpoint="eagle.view_eagle_smart_folder"] a')["href"] == "/EAGLE_smart_folder/smart-actual/"


def test_enabled_debug_page_is_reported_as_registered(guide):
    _, _, database, _ = guide
    app = create_app({"TESTING": True, "FLOWINONE_RESOURCE_DB_PATH": str(database.path),
                      "FLOWINONE_CATALOG_SYNC_INLINE": False, "FLOWINONE_DEV_TOOLS": True})
    client = app.test_client()
    page = BeautifulSoup(client.get("/route-guide/").data, "html.parser")
    debug_row = page.select_one('[data-route-endpoint="debug.debug_print"]')
    assert "可測試" in debug_row.get_text()
    assert debug_row.select_one("a").get_text() == "開啟測試"
    assert client.get(debug_row.select_one("a")["href"]).status_code == 200
