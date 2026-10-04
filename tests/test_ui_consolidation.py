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
