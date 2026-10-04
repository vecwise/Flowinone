"""Compatibility contracts for the four shared UI page families."""

from urllib.parse import parse_qs, urlsplit

import pytest
from bs4 import BeautifulSoup

from run import create_app
from src.flowinone.web import local


@pytest.fixture
def ui_client(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.flowinone.web.common.compute_feature_flags",
        lambda: {"eagle": False, "chrome": False, "youtube": False, "db": True},
    )
    app = create_app({
        "TESTING": True,
        "FLOWINONE_RESOURCE_DB_PATH": str(tmp_path / "ui.db"),
        "FLOWINONE_RESOURCE_LINK_THUMBNAILS": False,
        "FLOWINONE_CATALOG_SYNC_INLINE": False,
    })
    return app.test_client()


@pytest.mark.parametrize("route,view", [("grid", "grid"), ("slide", "reader")])
def test_legacy_collection_views_preserve_path_and_query(ui_client, route, view):
    response = ui_client.get(f"/{route}/nested/folder/?src=internal&tag=A&tag=B")
    assert response.status_code == 302
    target = urlsplit(response.headers["Location"])
    assert target.path == "/both/nested/folder/"
    assert parse_qs(target.query) == {"src": ["internal"], "tag": ["A", "B"], "view": [view]}


@pytest.mark.parametrize("route,function,metric", [
    ("update_db", "update_item_database", "seen"),
    ("update_thumbnails", "update_missing_thumbnails", "updated"),
    ("clear_thumbnails", "clear_thumbnails", "cleared"),
])
def test_maintenance_results_keep_metrics_errors_and_json(ui_client, monkeypatch, route, function, metric):
    result = {"base_dir": "/library", "db_path": "/library/item.db", metric: 123,
              "errors": [("problem.jpg", "cannot read file")]}
    monkeypatch.setattr(local, function, lambda *args, **kwargs: result)
    page = BeautifulSoup(ui_client.get("/library/").data, "html.parser")
    token = page.select_one('input[name="csrf_token"]')["value"]
    response = ui_client.post(f"/{route}", data={"csrf_token": token})
    assert response.status_code == 200
    rendered = BeautifulSoup(response.data, "html.parser")
    assert rendered.select_one("#main-content") is not None
    assert "123" in rendered.get_text()
    assert "problem.jpg" in rendered.get_text()
    assert "cannot read file" in rendered.select_one('[role="alert"]').get_text()
    assert ui_client.post(f"/{route}?format=json", data={"csrf_token": token}).get_json()[metric] == 123


@pytest.mark.parametrize("route,loader,source", [
    ("/both/board/?src=internal&view=reader", "local.get_folder_images", "local"),
    ("/chrome/bookmark_bar/", "chrome.get_chrome_bookmarks", "bookmarks"),
    ("/EAGLE_tag/design/?offset=2&limit=2&view=list", "eagle.get_eagle_images_by_tag", "eagle"),
    ("/EAGLE_smart_folder/smart/", "eagle.get_eagle_images_by_smart_folder_id", "eagle"),
])
def test_source_browse_shares_cards_without_losing_source_links(ui_client, monkeypatch, route, loader, source):
    monkeypatch.setattr("src.flowinone.web.common.compute_feature_flags", lambda: {
        "eagle": True, "chrome": True, "youtube": True, "db": True,
    })
    calls = []

    def records(*args, **kwargs):
        calls.append((args, kwargs))
        return ({"name": "Design", "pagination": {
            "limit": 2, "total": 8, "previous_offset": 0, "next_offset": 4,
        }}, [{"id": "one", "name": "Example", "media_type": "bookmark",
              "url": "https://example.com/article", "description": "Useful text"}])

    monkeypatch.setattr(f"src.flowinone.web.{loader}", records)
    response = ui_client.get(route)
    assert response.status_code == 200
    page = BeautifulSoup(response.data, "html.parser")
    assert page.select_one('[data-page-family="browse"]')
    card = page.select_one(".gallery-card")
    assert card["data-card-source"] == source
    assert card.select_one('a[href="https://example.com/article"]')["target"] == "_blank"
    assert "Useful text" in card.get_text()
    if source == "local":
        assert calls[0][0] == ("board", "internal")
        assert page.select_one("[data-gallery-view]")["data-gallery-view"] == "list"
    if "EAGLE_tag" in route:
        assert calls[0][1] == {"offset": 2, "limit": 2}
        next_link = page.select('[data-browse-link]')[-1]["href"]
        assert parse_qs(urlsplit(next_link).query) == {"offset": ["4"], "limit": ["2"], "view": ["list"]}


def test_eagle_stream_and_tag_directory_use_shared_browse(ui_client, monkeypatch):
    monkeypatch.setattr("src.flowinone.web.common.compute_feature_flags", lambda: {"eagle": True})
    monkeypatch.setattr("src.flowinone.web.eagle.get_eagle_stream_items", lambda **kwargs: [
        {"id": "image-one", "name": "Image", "media_type": "image"},
        {"id": "video-two", "name": "Video", "media_type": "video"},
    ])
    page = BeautifulSoup(ui_client.get("/EAGLE_stream/?limit=2&view=list").data, "html.parser")
    assert len(page.select(".gallery-card")) == 2
    assert page.select_one('a[href^="/EAGLE_image/image-one/"]')
    assert page.select_one('a[href^="/EAGLE_video/video-two/"]')
    assert "offset=2" in page.select_one('[data-browse-link]')["href"]
    assert ui_client.get("/EAGLE_stream/?limit=invalid").status_code == 400
    monkeypatch.setattr("src.flowinone.web.eagle.get_eagle_tags", lambda: (
        {"name": "Eagle Tags"}, [{"name": "design", "count": 7}],
    ))
    tags = BeautifulSoup(ui_client.get("/EAGLE_tags/").data, "html.parser")
    assert tags.select_one('.gallery-card a[href="/EAGLE_tag/design/"]')
    assert "7 個項目" in tags.get_text()


def test_management_moves_controls_and_preserves_legacy_return_context(ui_client):
    browse = BeautifulSoup(ui_client.get("/navigator/?source=local").data, "html.parser")
    assert browse.select_one("#catalog-incremental-sync") is None
    assert browse.select_one("[data-gallery-detail-content] .ui-detail-main")
    legacy = ui_client.get("/navigator/?manage=1&source=local&tags=A&tags=B&view=list")
    assert legacy.status_code == 302
    destination = urlsplit(legacy.headers["Location"])
    assert destination.path == "/library/"
    assert destination.fragment == "collection-maintenance"
    return_to = parse_qs(destination.query)["return_to"][0]
    assert parse_qs(urlsplit(return_to).query) == {"source": ["local"], "tags": ["A", "B"], "view": ["list"]}
    management = BeautifulSoup(ui_client.get(legacy.headers["Location"]).data, "html.parser")
    assert management.select_one('[data-page-family="manage"]')
    assert management.select_one("#catalog-incremental-sync")
    assert management.select_one("#catalog-similarity-rebuild")
    assert management.find("a", href=return_to)


def test_resource_detail_and_comparison_share_page_families(ui_client):
    created = ui_client.post("/api/resources", json={
        "url": "https://example.com/shared-detail", "title": "Shared detail", "enqueue": False,
    }).get_json()["resource"]
    detail = BeautifulSoup(ui_client.get(created["detail_url"]).data, "html.parser")
    assert detail.select_one('[data-page-family="detail"] .ui-detail-main')
    assert detail.select_one('.ui-detail-related')
    assert detail.select_one('a[href="https://example.com/shared-detail"]')
    versions = BeautifulSoup(ui_client.get(created["detail_url"] + "versions/").data, "html.parser")
    assert versions.select_one('[data-page-family="compare"]')
    assert versions.select_one('#version-from')
    duplicates = BeautifulSoup(ui_client.get("/catalog/duplicate-review/").data, "html.parser")
    assert duplicates.select_one('[data-page-family="compare"][data-duplicate-review]')
    assert duplicates.select_one('a[href="/library/#catalog-similarity-rebuild"]')


def test_navigation_exposes_all_enabled_source_views(ui_client, monkeypatch):
    monkeypatch.setattr("src.flowinone.web.common.compute_feature_flags", lambda: {
        "eagle": True, "chrome": True, "youtube": True, "db": True,
    })
    page = BeautifulSoup(ui_client.get("/library/").data, "html.parser")
    assert [link.get_text(strip=True) for link in page.select('.nav-links a')] == ["我的收藏", "管理收藏"]
    assert page.select_one('.nav-links a[aria-current="page"]')["href"] == "/library/"
    expected = {"/folders/", "/chrome/", "/chrome_youtube/", "/EAGLE_folders/", "/EAGLE_tags/",
                "/EAGLE_smart_folders/", "/EAGLE_stream/", "/resources/", "/item_db", "/catalog/duplicate-review/"}
    assert expected <= {link.get("href") for link in page.select('main a[href]')}
    assert ui_client.get("/library/?return_to=//example.com/").status_code == 200
    assert not BeautifulSoup(ui_client.get("/library/?return_to=//example.com/").data, "html.parser").find('a', href='//example.com/')


def test_media_detail_returns_to_source_filters_and_rejects_external_return(ui_client, monkeypatch):
    monkeypatch.setattr("src.flowinone.web.media.get_image_details", lambda *args: (
        {"name": "Image", "category": "local"},
        {"name": "Image", "source_url": "/static/default_thumbnail.svg", "parent_url": "/both/board/?src=internal"},
    ))
    return_to = "/both/board/?src=internal&view=list&tag=design"
    page = BeautifulSoup(ui_client.get("/image/a.jpg", query_string={"return_to": return_to}).data, "html.parser")
    assert page.select_one('[data-page-family="detail"]')
    assert page.find('a', href=return_to)
    rejected = BeautifulSoup(ui_client.get("/image/a.jpg?return_to=https://example.com/").data, "html.parser")
    assert rejected.find('a', href='/both/board/?src=internal')
    assert rejected.find('a', href='https://example.com/') is None


def test_source_failure_keeps_navigation_and_retry_on_shared_browse(ui_client, monkeypatch):
    from src.file_handler import ExternalServiceError

    monkeypatch.setattr("src.flowinone.web.common.compute_feature_flags", lambda: {"eagle": True})

    def unavailable(*args, **kwargs):
        raise ExternalServiceError("Eagle unavailable")

    monkeypatch.setattr("src.flowinone.web.eagle.get_eagle_stream_items", unavailable)
    response = ui_client.get("/EAGLE_stream/?offset=24&limit=24&view=list")
    assert response.status_code == 500
    page = BeautifulSoup(response.data, "html.parser")
    assert page.select_one('[data-page-family="browse"] [role="alert"]')
    assert page.find('a', href='/EAGLE_stream/?offset=24&limit=24&view=list').get_text() == '重試載入'
    assert page.find('a', href='/library/')
    monkeypatch.setattr("src.flowinone.web.eagle.get_eagle_images_by_tag", unavailable)
    assert ui_client.get("/EAGLE_tag/design/").status_code == 500


def test_resource_tools_and_versions_keep_return_context(ui_client):
    resource = ui_client.post("/api/resources", json={
        "url": "https://example.com/context", "enqueue": False,
    }).get_json()["resource"]
    return_to = "/navigator/?scope=all&source=resources&tags=design&view=list"
    page = BeautifulSoup(ui_client.get(resource["detail_url"], query_string={"return_to": return_to}).data, "html.parser")
    versions_link = page.find('a', string='查看差異')["href"]
    assert parse_qs(urlsplit(versions_link).query)["return_to"] == [return_to]
    versions = BeautifulSoup(ui_client.get(versions_link).data, "html.parser")
    assert versions.select_one('form input[name="return_to"]')["value"] == return_to
    assert parse_qs(urlsplit(versions.find('a', string='返回資源')["href"]).query)["return_to"] == [return_to]
    form = page.select_one(f'form[action="/resources/{resource["id"]}/tags"]')
    response = ui_client.post(form["action"], data={
        "csrf_token": form.select_one('[name="csrf_token"]')["value"], "tags": "design", "return_to": return_to,
    })
    assert parse_qs(urlsplit(response.headers["Location"]).query)["return_to"] == [return_to]
