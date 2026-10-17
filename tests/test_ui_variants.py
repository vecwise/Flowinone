"""UI selection must not change the underlying Catalog or write any data."""

from urllib.parse import parse_qs, urlsplit

import pytest
from bs4 import BeautifulSoup

from run import create_app
from src.flowinone.web.ui_variants import UI_KEYS, is_ui_url, ui_href


@pytest.fixture
def ui_app(tmp_path, monkeypatch):
    monkeypatch.setattr("src.flowinone.web.common.compute_feature_flags", lambda: {
        "eagle": False, "chrome": False, "youtube": False, "db": False,
    })
    return create_app({
        "TESTING": True, "FLOWINONE_RESOURCE_DB_PATH": str(tmp_path / "ui.db"),
        "FLOWINONE_RESOURCE_LINK_THUMBNAILS": False, "FLOWINONE_CATALOG_SYNC_INLINE": False,
    })


@pytest.mark.parametrize("variant", sorted(UI_KEYS))
def test_every_variant_renders_all_three_families_and_version_comparison(ui_app, variant):
    client = ui_app.test_client()
    resource = client.post("/api/resources", json={
        "url": "https://example.com/ui", "title": "UI example", "enqueue": False,
    }).get_json()["resource"]
    for path, family in (("/navigator/?scope=all", "browse"), (resource["detail_url"], "detail"),
                         (resource["detail_url"] + "versions/", "detail"), ("/resources/", "manage"),
                         ("/catalog/duplicate-review/", "manage"), ("/route-guide/", "manage")):
        response = client.get(path + ("&" if "?" in path else "?") + f"ui={variant}")
        assert response.status_code == 200
        page = BeautifulSoup(response.data, "html.parser")
        assert page.html["data-ui"] == variant
        assert page.select_one(f'main[data-page-family="{family}"]')
        assert page.select_one(f'[data-ui-select] option[value="{variant}"][selected]')
        assert all(len(select.select('option')) == 4 for select in page.select('[data-ui-select]'))
        assert f"ui={variant}" in page.select_one('.nav-links a')["href"]
        assert page.select_one('script[src="/static/js/ui_switcher.js"]')
        if family == "browse":
            assert page.select_one('[data-gallery-detail] [data-ui-select]')


def test_lab_has_twelve_fair_comparison_links_and_preserves_original_page(ui_app):
    client = ui_app.test_client()
    resource = client.post("/api/resources", json={
        "url": "https://example.com/lab", "title": "Lab sample", "enqueue": False,
    }).get_json()["resource"]
    original = "/navigator/?source=local&source=bookmarks&tags=設計&tags=UX&seed=123#item-one"
    page = BeautifulSoup(client.get("/ui-lab/", query_string={"return_to": original}).data, "html.parser")
    assert page.select_one('main[data-page-family="manage"]')
    assert len(page.select('[data-ui-candidate]')) == 4
    assert len(page.select('[data-ui-scenario]')) == 12
    for family in ("browse", "detail", "manage"):
        targets = [urlsplit(link["href"]) for link in page.select(f'[data-ui-scenario="{family}"]')]
        assert len({target.path for target in targets}) == 1
        assert {parse_qs(target.query)["ui"][0] for target in targets} == UI_KEYS
        if family == "detail":
            assert targets[0].path == resource["detail_url"]
    for link in page.select('.ui-lab-try-current'):
        destination = urlsplit(link["href"])
        assert destination.path == "/navigator/"
        assert destination.fragment == "item-one"
        assert parse_qs(destination.query)["source"] == ["local", "bookmarks"]
        assert parse_qs(destination.query)["tags"] == ["設計", "UX"]
        assert parse_qs(destination.query)["seed"] == ["123"]
    home = BeautifulSoup(client.get("/", follow_redirects=True).data, "html.parser")
    assert home.select_one('.nav-links a[data-ui-lab-entry]')
    empty_client = ui_app.test_client()
    for unsafe in ("//evil.example/", "https://evil.example/", "/api/catalog/items", "/serve_image/a.jpg"):
        rejected = BeautifulSoup(empty_client.get("/ui-lab/", query_string={"return_to": unsafe}).data, "html.parser")
        assert rejected.select_one('[data-ui-lab-return]')["href"] == "/navigator/?scope=all"


def test_lab_empty_data_reports_missing_detail_and_invalid_variant_uses_baseline(ui_app):
    client = ui_app.test_client()
    page = BeautifulSoup(client.get("/ui-lab/?ui=unknown").data, "html.parser")
    assert page.html["data-ui"] == "baseline"
    assert "目前沒有可用詳情樣本" in page.get_text()
    assert len(page.select('a[data-ui-scenario="detail"]')) == 4
    assert "準備詳情樣本" in page.select_one('a[data-ui-scenario="detail"]').get_text()


def test_ui_does_not_change_catalog_query_or_api_and_survives_redirects(ui_app):
    client = ui_app.test_client()
    baseline = BeautifulSoup(client.get("/navigator/?scope=all&tags=A&tags=B").data, "html.parser")
    candidate = BeautifulSoup(client.get("/navigator/?scope=all&tags=A&tags=B&ui=workbench").data, "html.parser")
    for attribute in ("data-navigator-query", "data-navigator-query-payload"):
        assert baseline.main[attribute] == candidate.main[attribute]
    response = client.get("/?ui=reader")
    assert parse_qs(urlsplit(response.location).query) == {"scope": ["all"], "ui": ["reader"]}
    legacy = client.get("/slide/nested/folder/?src=internal&tag=A&tag=B&ui=curator")
    assert parse_qs(urlsplit(legacy.location).query) == {
        "src": ["internal"], "tag": ["A", "B"], "view": ["reader"], "ui": ["curator"],
    }
    page = BeautifulSoup(client.get("/resources/?ui=reader").data, "html.parser")
    token = page.select_one('input[name="csrf_token"]')["value"]
    created = client.post("/resources/", data={
        "url": "https://example.com/created", "enqueue": "0", "csrf_token": token, "ui": "reader",
    })
    assert created.status_code == 302
    assert parse_qs(urlsplit(created.location).query)["ui"] == ["reader"]
    # Different explicit URLs are independent; no shared preference cookie is set.
    assert "flowinone-ui" not in created.headers.get("Set-Cookie", "")
    assert BeautifulSoup(client.get("/resources/").data, "html.parser").html["data-ui"] == "baseline"


def test_ui_url_boundaries_preserve_repeated_queries_nested_return_and_assets(ui_app):
    with ui_app.test_request_context("/navigator/?ui=reader"):
        target = ui_href("/image/a.jpg?src=internal&tag=A&tag=B&return_to=%2Fnavigator%2F%3Ftags%3DX%26tags%3DY%23item-x#image")
        parsed = urlsplit(target)
        query = parse_qs(parsed.query)
        assert query["src"] == ["internal"] and query["tag"] == ["A", "B"]
        assert parsed.fragment == "image"
        nested = urlsplit(query["return_to"][0])
        assert nested.fragment == "item-x"
        assert parse_qs(nested.query) == {"tags": ["X", "Y"], "ui": ["reader"]}
        for path in ("https://example.com/", "//example.com/", "/api/catalog/items", "/static/favicon.svg",
                     "/serve_image/a.jpg", "/resources/one/asset/cover", "javascript:alert(1)", "#related",
                     "http://[", "https://localhost/navigator/", "http:/navigator/"):
            assert not is_ui_url(path)
            assert ui_href(path) == path


@pytest.mark.parametrize("variant", sorted(UI_KEYS))
@pytest.mark.parametrize("path,loader", [
    ("/both/board/?src=internal", "local.get_folder_images"),
    ("/chrome/bookmark_bar/", "chrome.get_chrome_bookmarks"),
    ("/EAGLE_tag/design/?offset=2&limit=2", "eagle.get_eagle_images_by_tag"),
])
def test_variants_cover_source_owned_browse_and_pagination(ui_app, monkeypatch, variant, path, loader):
    monkeypatch.setattr("src.flowinone.web.common.compute_feature_flags", lambda: {
        "eagle": True, "chrome": True, "youtube": True, "db": True,
    })
    monkeypatch.setattr(f"src.flowinone.web.{loader}", lambda *args, **kwargs: (
        {"name": "Source", "pagination": {"limit": 2, "total": 8, "next_offset": 4}},
        [{"id": "example", "name": "Source example", "media_type": "bookmark",
          "url": "https://example.com/source", "description": "Source-owned content"}],
    ))
    response = ui_app.test_client().get(path + ("&" if "?" in path else "?") + f"ui={variant}")
    assert response.status_code == 200
    page = BeautifulSoup(response.data, "html.parser")
    assert page.html["data-ui"] == variant
    assert page.select_one('main[data-page-family="browse"]')
    assert page.select_one('.gallery-card a[href="https://example.com/source"]')
    if "EAGLE" in path:
        assert parse_qs(urlsplit(page.select_one('[data-browse-link]')["href"]).query)["offset"] == ["4"]
