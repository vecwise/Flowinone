from __future__ import annotations

import pytest

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
