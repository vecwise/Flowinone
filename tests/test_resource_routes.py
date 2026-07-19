import io
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from flask import Flask

from routes import register_routes


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def resource_app(tmp_path):
    app = Flask(
        "flowinone-resource-test",
        template_folder=str(PROJECT_ROOT / "templates"),
        static_folder=str(PROJECT_ROOT / "static"),
    )
    app.config.update(
        TESTING=True,
        FLOWINONE_RESOURCE_DB_PATH=str(tmp_path / "resource.db"),
        CHROME_BOOKMARK_PATH=str(tmp_path / "Bookmarks"),
        FLOWINONE_RESOURCE_LINK_THUMBNAILS=False,
    )
    register_routes(app)
    return app


def test_resource_pages_and_json_crud(resource_app):
    client = resource_app.test_client()
    resource_page = client.get("/resources/")
    assert resource_page.status_code == 200
    global_search = BeautifulSoup(resource_page.data, "html.parser").select_one(
        "form.search-container"
    )
    assert global_search is not None
    assert global_search.select_one('input[name="scope"]')["value"] == "all"

    created = client.post(
        "/api/resources",
        json={"url": "https://example.com/route", "title": "Route Resource", "enqueue": False},
    )
    assert created.status_code == 201
    resource = created.get_json()["resource"]
    assert client.get(resource["detail_url"]).status_code == 200
    assert b"Route Resource" in client.get("/resources/").data

    patched = client.patch(
        f"/api/resources/{resource['id']}", json={"title": "Renamed Resource"}
    )
    assert patched.status_code == 200
    assert patched.get_json()["title"] == "Renamed Resource"
    tagged = client.post(
        f"/api/resources/{resource['id']}/tags", json={"tags": ["AI", "Architecture"]}
    )
    assert tagged.status_code == 200
    assert "AI" in tagged.get_json()["tag_names"]
    ai_tag = next(tag for tag in tagged.get_json()["tags"] if tag["name"] == "AI")
    removed = client.delete(f"/api/resources/{resource['id']}/tags/{ai_tag['id']}")
    assert removed.status_code == 200
    assert "AI" not in removed.get_json()["tag_names"]
    assert client.post(f"/api/resources/{resource['id']}/status", json={}).status_code == 404
    assert client.post(f"/api/resources/{resource['id']}/promote", json={}).status_code == 404


def test_renderer_root_and_removed_workflow_routes(resource_app):
    client = resource_app.test_client()
    assert client.get("/").headers["Location"].endswith("/navigator/?scope=gallery")
    for path in (
        "/build/",
        "/think/",
        "/learn/",
        "/scan/",
        "/recover/",
        "/write/",
        "/entries/",
        "/projects/",
        "/inspiration/",
        "/notes/",
    ):
        assert client.get(path).status_code == 404


def test_chrome_upload_api_imports_json(resource_app):
    payload = b'''{
      "roots": {
        "bookmark_bar": {
          "type": "folder", "name": "Bookmarks", "children": [
            {"type": "url", "id": "1", "name": "Imported", "url": "https://example.com/imported"}
          ]
        }
      }
    }'''
    client = resource_app.test_client()
    response = client.post(
        "/api/imports/chrome",
        data={"format": "json", "file": (io.BytesIO(payload), "bookmarks.json")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert response.get_json()["created"] == 1
