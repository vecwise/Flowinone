from __future__ import annotations

import pytest
from bs4 import BeautifulSoup
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from run import create_app
from src.flowinone.catalog.browse import CatalogService
from src.flowinone.catalog.sync import CatalogSyncService
from src.flowinone.resource_library.database import get_resource_database


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("src.flowinone.catalog.blueprint.is_eagle_available", lambda: False)
    monkeypatch.setattr(
        "src.flowinone.web.common.compute_feature_flags",
        lambda: {"eagle": False, "chrome": False, "youtube": False, "db": False},
    )
    app = create_app(
        {
            "TESTING": True,
            "FLOWINONE_DEV_TOOLS": True,
            "FLOWINONE_RESOURCE_DB_PATH": str(tmp_path / "flowinone.db"),
            "FLOWINONE_RESOURCE_LINK_THUMBNAILS": False,
            "FLOWINONE_CATALOG_SYNC_INLINE": False,
        }
    )
    return app.test_client()


@pytest.mark.parametrize(
    "path",
    (
        "/api/gallery/items",
        "/api/gallery/sources",
        "/gallery/",
        "/search/",
        "/gallery/lab",
        "/gallery/lab/gsap-filmstrip",
    ),
)
def test_removed_gallery_read_surfaces_are_not_registered(client, path):
    assert client.get(path).status_code == 404


def test_navigator_and_catalog_are_the_only_browser_read_surfaces(client):
    navigator = client.get("/navigator/?scope=gallery&source=local")
    assert navigator.status_code == 200
    assert "我的收藏" in navigator.get_data(as_text=True)

    catalog = client.get("/api/catalog/items?scope=gallery&source=local")
    assert catalog.status_code == 200

    rules = {rule.rule for rule in client.application.url_map.iter_rules()}
    assert "/navigator/" in rules
    assert "/api/catalog/items" in rules
    assert not any(rule.startswith("/api/gallery/") for rule in rules)
    assert not any(rule.startswith("/gallery/lab") for rule in rules)


def test_collection_form_keeps_legacy_filters_in_collapsed_controls(client):
    response = client.get("/navigator/?scope=gallery&source=local&q=design&type=image&tags=reference&limit=12&added_from=2026-09-01")
    assert response.status_code == 200
    soup = BeautifulSoup(response.data, "html.parser")
    form = soup.select_one("form.navigator-query")
    assert form.select_one('input[name="q"]')["value"] == "design"
    assert form.select_one('input[name="source"][checked]')["value"] == "local"
    assert form.select_one('input[name="tags"]')["value"] == "reference"
    assert form.select_one('input[name="limit"]')["value"] == "12"
    assert form.select_one('input[name="added_from"]')["value"] == "2026-09-01"
    assert soup.select_one(".navigator-advanced[open]") is None
    assert "已套用" in soup.select_one(".navigator-advanced summary").get_text()
    expanded = client.get(soup.select_one(".navigator-scope-note a")["href"])
    assert expanded.status_code == 200
    assert BeautifulSoup(expanded.data, "html.parser").select_one('input[name="scope"]')["value"] == "all"
    assert response.data.index(b'id="navigator-theme-heading"') < response.data.index(b'id="navigator-q"')


@pytest.fixture
def topic_catalog(client):
    database = get_resource_database(Path(client.application.config["FLOWINONE_RESOURCE_DB_PATH"]))
    sync = CatalogSyncService(database)
    ids = {}
    with database.engine.begin() as conn:
        for index in range(5):
            ids[f"design-{index}"] = sync._upsert(
                conn, identity_key=f"local:design-{index}", source_kind="local",
                source_key=f"design-{index}", item_type="image",
                title=f"Design reference {index}", detail_uri=f"/image/design-{index}.jpg",
                tags=(("Design", "source"), ("common", "source")),
                captured_at=f"2026-09-0{index + 1}",
            )
        for index in range(2):
            ids[f"photo-{index}"] = sync._upsert(
                conn, identity_key=f"local:photo-{index}", source_kind="local",
                source_key=f"photo-{index}", item_type="image",
                title=f"Photo {index}", detail_uri=f"/image/photo-{index}.jpg",
                tags=(("photo", "source"), ("common", "source")),
            )
        ids["bookmark"] = sync._upsert(
            conn, identity_key="url:design", source_kind="bookmarks",
            source_key="https://example.com/design", item_type="web_page",
            title="Design bookmark", detail_uri="https://example.com/design",
            original_url="https://example.com/design", duration_seconds=30,
            metadata={"folder_path": "Art / Illustration"},
            tags=(("Design", "folder"), ("common", "folder")), captured_at="2026-09-06",
        )
        ids["resource"] = sync._upsert(
            conn, identity_key="url:code", source_kind="resources", source_key="code",
            item_type="article", title="Code resource", detail_uri="/resources/code/",
            tags=(("code", "resource"), ("common", "resource")),
        )
    return database, ids


def _topic_links(soup):
    return soup.select(".navigator-topic-list a.navigator-chip")


def test_home_topics_follow_catalog_and_open_themed_navigator(client, topic_catalog):
    _database, ids = topic_catalog
    home = client.get("/")
    assert home.status_code == 302
    assert home.headers["Location"] == "/navigator/?scope=all"
    soup = BeautifulSoup(client.get("/", follow_redirects=True).data, "html.parser")
    cards = soup.select(".navigator-topic-card")
    assert [card["data-topic"] for card in cards] == ["Design", "photo", "code"]
    assert cards[0].select_one("header small").get_text() == "6 個項目"
    assert len(cards[0].select("[data-topic-item]")) == 3
    assert ids["resource"] in {link["data-topic-item"] for link in soup.select("[data-topic-item]")}
    preview = cards[0].select_one("[data-topic-item]")
    target = urlsplit(preview["href"])
    params = parse_qs(target.query)
    assert params["tags"] == ["design"]
    assert params["scope"] == ["all"]
    assert params["sort"] == ["recently_added"]
    assert target.fragment == f"item-{preview['data-topic-item']}"
    themed = BeautifulSoup(client.get(preview["href"]).data, "html.parser")
    assert themed.select_one(f"#{target.fragment}") is not None
    assert themed.select_one(".navigator-topic-recommendations") is None
    assert themed.select_one('.navigator-topic-list [aria-current="true"]') is not None


def test_topic_links_and_previews_keep_all_navigator_filters(client, topic_catalog):
    database, ids = topic_catalog
    CatalogService(database).record_event(ids["bookmark"], "favorite")
    filters = {
        "scope": "all", "source": "bookmarks", "q": "Design", "type": "web_page",
        "folder": "Art", "favorite": "1", "unviewed": "1", "duration_min": "10",
        "duration_max": "60", "added_from": "2026-09-01", "added_to": "2026-10-01",
        "sort": "title", "limit": "1", "view": "list",
    }
    response = client.get("/navigator/", query_string=filters)
    assert response.status_code == 200
    soup = BeautifulSoup(response.data, "html.parser")
    assert len(_topic_links(soup)) == 1
    card = soup.select_one(".navigator-topic-card")
    assert card["data-topic"] == "Design"
    assert card.select_one("header small").get_text() == "1 個項目"
    assert card.select_one("[data-topic-item]")["data-topic-item"] == ids["bookmark"]
    for link in [*_topic_links(soup), *card.select("a")]:
        params = parse_qs(urlsplit(link["href"]).query)
        assert all(params[key] == [value] for key, value in filters.items() if key not in {"duration_min", "duration_max"})
        assert float(params["duration_min"][0]) == 10
        assert float(params["duration_max"][0]) == 60
        assert params["tags"] == ["design"]
        assert "cursor" not in params


def test_topic_links_keep_offline_source_selection_and_random_seed(client, topic_catalog):
    response = client.get(
        "/navigator/?scope=all&source=local&source=eagle&q=Design&type=image"
        "&sort=random&seed=23&view=list"
    )
    soup = BeautifulSoup(response.data, "html.parser")
    links = [*_topic_links(soup), *soup.select(".navigator-topic-card a")]
    assert links
    for link in links:
        params = parse_qs(urlsplit(link["href"]).query)
        assert params["source"] == ["local", "eagle"]
        assert params["scope"] == ["all"]
        assert params["q"] == ["Design"]
        assert params["type"] == ["image"]
        assert params["sort"] == ["random"]
        assert params["seed"] == ["23"]
        assert params["view"] == ["list"]
    destination = BeautifulSoup(client.get(links[0]["href"]).data, "html.parser")
    assert destination.select_one('input[name="source"][value="eagle"][checked]') is not None


def test_same_topic_keeps_named_context_when_eagle_is_offline(client, topic_catalog):
    database, _ids = topic_catalog
    saved = CatalogService(database).save_search(
        "Design sources", {"scope": "all", "sources": ["local", "eagle"], "tags": ["design"]},
    )
    response = client.get(f"/navigator/views/{saved['id']}/", follow_redirects=True)
    soup = BeautifulSoup(response.data, "html.parser")
    active = soup.select_one('.navigator-topic-list [aria-current="true"]')
    assert active is not None
    assert parse_qs(urlsplit(active["href"]).query)["context"] == [saved["id"]]
    for link in _topic_links(soup):
        if link != active:
            assert "context" not in parse_qs(urlsplit(link["href"]).query)


def test_topic_previews_are_stable_and_do_not_repeat_on_next_batch(client, topic_catalog):
    _database, ids = topic_catalog
    path = "/navigator/?scope=gallery&source=local&limit=1"
    first = BeautifulSoup(client.get(path).data, "html.parser")
    second = BeautifulSoup(client.get(path).data, "html.parser")
    preview_ids = lambda soup: [item["data-topic-item"] for item in soup.select('[data-topic="Design"] [data-topic-item]')]
    assert preview_ids(first) == preview_ids(second) == [ids[f"design-{index}"] for index in (4, 3, 2)]
    assert first.select_one('[data-topic="Design"] header small').get_text() == "5 個項目"
    # A small batch size must still include every linked preview on arrival.
    for link in first.select('[data-topic="Design"] [data-topic-item]'):
        target = urlsplit(link["href"])
        destination = BeautifulSoup(client.get(link["href"]).data, "html.parser")
        assert destination.select_one(f"#{target.fragment}") is not None
    following = BeautifulSoup(client.get(first.select_one(".gallery-pagination a")["href"]).data, "html.parser")
    assert following.select_one(".navigator-topic-recommendations") is None
    assert _topic_links(following)
    assert all("cursor" not in parse_qs(urlsplit(link["href"]).query) for link in _topic_links(following))


def test_topics_omit_hidden_deleted_stale_and_unavailable_origins(client, topic_catalog):
    database, ids = topic_catalog
    service = CatalogService(database)
    service.record_event(ids["photo-0"], "hide")
    with database.engine.begin() as conn:
        conn.exec_driver_sql("UPDATE catalog_items SET is_deleted=1 WHERE id=?", (ids["photo-1"],))
        conn.exec_driver_sql("UPDATE catalog_origins SET stale=1 WHERE catalog_item_id=?", (ids["design-0"],))
        CatalogSyncService(database)._upsert(
            conn, identity_key="eagle:only", source_kind="eagle", source_key="only",
            item_type="image", title="Eagle only", detail_uri="/EAGLE_image/only/",
            tags=(("eagle-only", "source"),),
        )
    CatalogService.mark_browse_data_changed(database)
    soup = BeautifulSoup(client.get("/navigator/?scope=gallery&source=local").data, "html.parser")
    assert [card["data-topic"] for card in soup.select(".navigator-topic-card")] == ["Design"]
    assert soup.select_one('.navigator-topic-card header small').get_text() == "4 個項目"
    excluded = {ids["photo-0"], ids["photo-1"], ids["design-0"]}
    assert not excluded.intersection(item["data-topic-item"] for item in soup.select("[data-topic-item]"))
    unavailable = BeautifulSoup(client.get("/navigator/?scope=gallery&source=eagle").data, "html.parser")
    assert not _topic_links(unavailable)
    assert unavailable.select_one(".navigator-topic-recommendations") is None


@pytest.mark.parametrize("tagged", [False, True])
def test_small_library_can_browse_with_or_without_a_topic(client, tagged):
    empty = BeautifulSoup(client.get("/", follow_redirects=True).data, "html.parser")
    assert empty.select_one(".navigator-topic-recommendations") is None
    database = get_resource_database(Path(client.application.config["FLOWINONE_RESOURCE_DB_PATH"]))
    with database.engine.begin() as conn:
        CatalogSyncService(database)._upsert(
            conn, identity_key="local:one", source_kind="local", source_key="one",
            item_type="image", title="Only item", detail_uri="/image/one.jpg",
            tags=(("single-topic", "source"),) if tagged else (),
        )
    soup = BeautifulSoup(client.get("/navigator/?source=local").data, "html.parser")
    assert soup.select_one(".gallery-card") is not None
    assert bool(soup.select_one(".navigator-topic-recommendations")) is tagged
    if not tagged:
        assert "目前條件下沒有可探索的標籤主題" in soup.get_text()


def test_topic_queries_and_preview_size_are_bounded(client, monkeypatch):
    database = get_resource_database(Path(client.application.config["FLOWINONE_RESOURCE_DB_PATH"]))
    with database.engine.begin() as conn:
        for index in range(8):
            CatalogSyncService(database)._upsert(
                conn, identity_key=f"local:topic-{index}", source_kind="local", source_key=str(index),
                item_type="image", title=f"Topic {index}", detail_uri=f"/image/{index}.jpg",
                tags=((f"topic-{index}", "source"),),
            )
    queries = []
    row_queries = []
    original = CatalogService.list

    def record_queries(self, query, **kwargs):
        if not kwargs.get("include_facets", True):
            queries.append(query)
            row_queries.append(kwargs.get("include_items", True))
        return original(self, query, **kwargs)

    monkeypatch.setattr(CatalogService, "list", record_queries)
    soup = BeautifulSoup(client.get("/navigator/?source=local").data, "html.parser")
    assert len(queries) == len(_topic_links(soup)) == 6
    assert row_queries == [True, True, True, False, False, False]
    assert len(soup.select(".navigator-topic-card")) == 3
    assert all(query.limit <= 3 and not query.cursor for query in queries)
    assert all(len(card.select("[data-topic-item]")) <= 3 for card in soup.select(".navigator-topic-card"))


@pytest.mark.parametrize("selected_topic", [False, True])
def test_topic_chips_do_not_query_preview_rows(client, topic_catalog, monkeypatch, selected_topic):
    first = BeautifulSoup(client.get("/navigator/?source=local&limit=1").data, "html.parser")
    path = _topic_links(first)[0]["href"] if selected_topic else first.select_one(".gallery-pagination a")["href"]
    original = CatalogService.list
    preview_queries = []

    def record_queries(self, query, **kwargs):
        if not kwargs.get("include_facets", True):
            preview_queries.append(kwargs.get("include_items", True))
        return original(self, query, **kwargs)

    monkeypatch.setattr(CatalogService, "list", record_queries)
    soup = BeautifulSoup(client.get(path).data, "html.parser")
    assert _topic_links(soup)
    assert soup.select_one(".navigator-topic-recommendations") is None
    assert preview_queries and not any(preview_queries)
