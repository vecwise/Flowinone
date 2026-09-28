from __future__ import annotations

import pytest
from bs4 import BeautifulSoup
from urllib.parse import parse_qs, urlsplit

from run import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "FLOWINONE_DEV_TOOLS": True,
            "FLOWINONE_RESOURCE_DB_PATH": str(tmp_path / "flowinone.db"),
            "FLOWINONE_RESOURCE_LINK_THUMBNAILS": False,
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
    assert b"Cross-source navigator" in navigator.data

    catalog = client.get("/api/catalog/items?scope=gallery&source=local")
    assert catalog.status_code == 200

    rules = {rule.rule for rule in client.application.url_map.iter_rules()}
    assert "/navigator/" in rules
    assert "/api/catalog/items" in rules
    assert not any(rule.startswith("/api/gallery/") for rule in rules)
    assert not any(rule.startswith("/gallery/lab") for rule in rules)


def test_gallery_quick_filters_preserve_context_and_show_results_first(client):
    response = client.get("/navigator/?scope=gallery&source=local&q=design&type=image")
    assert response.status_code == 200
    soup = BeautifulSoup(response.data, "html.parser")
    video = next(
        link for link in soup.select('.navigator-chip-group[aria-label="類型"] a')
        if link.get_text(strip=True) == "影片"
    )
    query = parse_qs(urlsplit(video["href"]).query)
    assert query["q"] == ["design"]
    assert query["source"] == ["local"]
    assert query["type"] == ["video"]
    assert soup.select_one(".navigator-advanced[open]") is not None
    assert response.data.index(b'id="navigator-results-heading"') < response.data.index(
        b'id="navigator-saved-searches-heading"'
    )
