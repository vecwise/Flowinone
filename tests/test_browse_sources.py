"""Global source selection survives navigation without changing maintenance targets."""

from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import json

from bs4 import BeautifulSoup
import pytest
from werkzeug.datastructures import MultiDict

from run import create_app
from src.flowinone.catalog.browse import CatalogService
from src.flowinone.catalog.sync import CatalogSyncService
from src.flowinone.resource_library.database import get_resource_database
from src.flowinone.web.browse_sources import COOKIE_NAME


@pytest.fixture
def collection(tmp_path, monkeypatch):
    monkeypatch.setattr("src.flowinone.catalog.blueprint.is_eagle_available", lambda: True)
    monkeypatch.setattr("src.flowinone.web.common.compute_feature_flags",
                        lambda: {"eagle": False, "chrome": False, "youtube": False, "db": False})
    app = create_app({"TESTING": True, "FLOWINONE_CATALOG_SYNC_INLINE": False,
                      "FLOWINONE_RESOURCE_DB_PATH": str(tmp_path / "sources.db"),
                      "FLOWINONE_RESOURCE_LINK_THUMBNAILS": False})
    database = get_resource_database(Path(app.config["FLOWINONE_RESOURCE_DB_PATH"]), migrate=True)
    sync = CatalogSyncService(database)
    ids = {}
    with database.engine.begin() as conn:
        for source in ("local", "eagle", "bookmarks", "resources"):
            for index in range(2):
                key = f"{source}-{index}"
                ids[key] = sync._upsert(
                    conn, identity_key=key, source_kind=source, source_key=key,
                    item_type="web_page" if source in {"bookmarks", "resources"} else "image",
                    title=f"Design {key}", detail_uri=f"https://example.com/{key}",
                    original_url=f"https://example.com/{key}",
                    metadata={"folder_path": "Art / Design"} if source == "bookmarks" else {},
                    tags=(("Design", "source"),), captured_at=f"2026-09-0{index + 1}",
                )
    return app.test_client(), CatalogService(database), ids


def page(client, path="/navigator/"):
    response = client.get(path, follow_redirects=True)
    assert response.status_code == 200
    return BeautifulSoup(response.data, "html.parser")


def select(client, sources, return_to="/navigator/", **extra):
    soup = page(client)
    token = soup.select_one('#global-source-form input[name="csrf_token"]')["value"]
    return client.post("/browse-sources/", data=MultiDict([
        ("csrf_token", token), ("return_to", return_to),
        *(("source", source) for source in sources), *extra.items(),
    ]))


def query(soup):
    return json.loads(soup.select_one("[data-navigator-query-payload]")["data-navigator-query-payload"])


def checked(soup):
    return {item["value"] for item in soup.select('#global-source-form input[name="source"][checked]')}


def results(soup):
    return {item["data-navigator-item"] for item in soup.select("[data-navigator-item]")}


def test_source_picker_is_folded_in_filters_without_nested_or_duplicate_forms(collection):
    client, _, _ = collection
    soup = page(client)
    assert checked(soup) == {"bookmarks", "local", "eagle", "resources"}
    assert soup.select_one(".app-header #global-source-form") is None
    assert soup.select_one(".navigator-advanced #global-source-form") is not None
    assert soup.select_one(".navigator-advanced[open]") is None
    assert soup.select_one("form form") is None
    assert len(soup.select('#global-source-form')) == 1
    assert len(soup.select('.navigator-query input[type=hidden][name=source]')) == 4


def test_selection_is_a_persistent_union_and_overrides_stale_navigation(collection):
    client, _, ids = collection
    response = select(client, ["bookmarks", "local"])
    assert response.status_code == 303
    assert "Max-Age=31536000" in response.headers["Set-Cookie"]
    assert "HttpOnly" in response.headers["Set-Cookie"]
    for path in (response.location, "/navigator/", "/navigator/?scope=gallery&source=resources"):
        soup = page(client, path)
        assert checked(soup) == {"bookmarks", "local"}
        assert query(soup)["sources"] == ["local", "bookmarks"]
        assert results(soup) == {ids[f"{source}-{i}"] for source in ("local", "bookmarks") for i in range(2)}


def test_source_change_retains_page_filters_and_invalidates_paging(collection):
    client, _, _ = collection
    response = select(client, ["bookmarks"],
        "/navigator/?q=Design&tags=design&sort=title&view=list&shape=square&limit=1&cursor=obsolete&resume_scroll=700&context=old#item-old")
    params = parse_qs(urlsplit(response.location).query)
    assert params["source"] == ["bookmarks"]
    for name, value in {"q": "Design", "tags": "design", "sort": "title", "view": "list", "shape": "square", "limit": "1"}.items():
        assert params[name] == [value]
    assert not ({"cursor", "resume_scroll", "context"} & params.keys())
    assert not urlsplit(response.location).fragment
    soup = page(client, response.location)
    next_link = soup.select_one(".gallery-pagination a")
    assert parse_qs(urlsplit(next_link["href"]).query)["source"] == ["bookmarks"]
    assert checked(page(client, next_link["href"])) == {"bookmarks"}


def test_reset_and_context_clear_keep_sources_but_remove_local_filters(collection):
    client, _, _ = collection
    select(client, ["bookmarks"])
    soup = page(client, "/navigator/?q=Design&tags=design&type=web_page")
    links = [soup.select_one('.navigator-query-actions a'), soup.select_one('.current-context-controls a')]
    for link in links:
        reset = page(client, link["href"])
        values = query(reset)
        assert values["sources"] == ["bookmarks"]
        assert not values["q"] and not values["tags"] and not values["type"]


def test_search_and_saved_views_inherit_global_sources(collection):
    client, service, _ = collection
    saved = service.save_search("Design URLs", {"sources": ["resources"], "tags": ["design"]})
    select(client, ["bookmarks"])
    management = page(client, "/library/")
    assert checked(management) == {"bookmarks"}
    assert management.select_one('.search-container input[name=source]')["value"] == "bookmarks"
    assert checked(page(client, management.select_one('.app-brand')["href"])) == {"bookmarks"}
    soup = page(client, f"/navigator/views/{saved['id']}/")
    assert query(soup)["sources"] == ["bookmarks"]
    assert query(soup)["tags"] == ["design"]
    assert soup.select_one('.current-context strong').get_text() == "Design URLs"
    assert all(parse_qs(urlsplit(link["href"]).query)["source"] == ["bookmarks"]
               for link in soup.select('.navigator-topic-list a.navigator-chip'))


def test_folder_navigation_does_not_silently_enable_bookmarks(collection):
    client, _, ids = collection
    select(client, ["local"])
    soup = page(client)
    folder = soup.select_one('.navigator-folder-card')
    assert parse_qs(urlsplit(folder["href"]).query)["source"] == ["local"]
    empty = page(client, folder["href"])
    assert not results(empty)
    assert checked(empty) == {"local"}
    assert "目前來源未包含書籤" in empty.get_text()
    response = select(client, ["local"], folder["href"], source_action="add_bookmarks")
    restored = page(client, response.location)
    assert checked(restored) == {"local", "bookmarks"}
    assert results(restored) == {ids["bookmarks-0"], ids["bookmarks-1"]}


def test_all_sources_is_a_separate_action(collection):
    client, _, ids = collection
    select(client, ["bookmarks"])
    response = select(client, ["bookmarks"], source_action="all")
    assert len(checked(page(client, response.location))) == 4
    assert results(page(client)) == set(ids.values())


def test_read_apis_and_recommendations_respect_selected_origins(collection):
    client, _, ids = collection
    select(client, ["bookmarks"])
    payload = client.get("/api/catalog/items?source=local").get_json()
    assert {item["id"] for item in payload["items"]} == {ids["bookmarks-0"], ids["bookmarks-1"]}
    related = client.get(f"/api/catalog/items/{ids['bookmarks-0']}/related?source=local").get_json()
    assert {item["id"] for item in related["items"]} == {ids["bookmarks-1"]}
    assert all(item["launch_source"] == "bookmarks" for item in related["items"])
    assert client.get(f"/api/catalog/items/{ids['local-0']}").status_code == 404


@pytest.mark.parametrize("sources", [[], ["bogus"], ["local", "bogus"]])
def test_invalid_selection_does_not_replace_preference(collection, sources):
    client, _, _ = collection
    select(client, ["bookmarks"])
    assert select(client, sources).status_code == 400
    assert checked(page(client)) == {"bookmarks"}


@pytest.mark.parametrize("return_to", ["https://example.com/", "//example.com/", "/api/catalog/items", "/missing/"])
def test_preference_post_rejects_external_or_non_page_redirects(collection, return_to):
    client, _, _ = collection
    response = select(client, ["local"], return_to)
    assert urlsplit(response.location).path == "/navigator/"
    assert not urlsplit(response.location).netloc


def test_selection_uses_existing_csrf_boundary(collection):
    client, _, _ = collection
    assert client.post("/browse-sources/", data={"source": "local"}).status_code == 403
    assert client.get_cookie(COOKIE_NAME) is None
