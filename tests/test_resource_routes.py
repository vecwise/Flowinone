import io
import tempfile
from pathlib import Path
from urllib.parse import unquote_plus

import pytest
from bs4 import BeautifulSoup
from flask import Flask

from routes import register_routes
from src.flowinone.resource_library.database import get_resource_database
from src.flowinone.resource_library.jobs import JobQueue


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
    resource_search = BeautifulSoup(resource_page.data, "html.parser").select_one(
        "form.resource-filter"
    )
    assert resource_search["action"] == "/navigator/"
    assert resource_search.select_one('input[name="source"]')["value"] == "resources"
    assert resource_search.select_one('select[name="type"]') is not None

    created = client.post(
        "/api/resources",
        json={"url": "https://example.com/route", "title": "Route Resource", "enqueue": False},
    )
    assert created.status_code == 201
    resource = created.get_json()["resource"]
    assert client.get(resource["detail_url"]).status_code == 200
    assert b"Route Resource" in client.get("/resources/").data
    old_search = client.get("/resources/?q=Route&source_type=web_page")
    assert old_search.status_code == 302
    assert old_search.headers["Location"] == "/navigator/?scope=all&source=resources&q=Route&type=web_page"
    assert b"Route Resource" in client.get(old_search.headers["Location"]).data

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


def test_selected_resources_queue_bounded_on_demand_enrichment(resource_app):
    client = resource_app.test_client()
    ids = [
        client.post("/api/resources", json={"url": f"https://example.com/pilot-{index}", "enqueue": False}).get_json()["resource"]["id"]
        for index in range(2)
    ]
    page = BeautifulSoup(client.get("/resources/").data, "html.parser")
    form = page.select_one('form[action^="/resources/enrich-selection"]')
    assert form is not None
    assert {field["value"] for field in form.select('input[name="resource_id"]')} == set(ids)
    token = form.select_one('input[name="csrf_token"]')["value"]
    empty = client.post(form["action"], data={"csrf_token": token}, follow_redirects=True)
    assert "請選擇 1–50 筆資源" in empty.get_data(as_text=True)

    response = client.post(
        form["action"], data={"csrf_token": token, "resource_id": ids}, follow_redirects=True
    )
    assert "已排入 2 筆資源的擷取工作" in response.get_data(as_text=True)
    queue = JobQueue(get_resource_database(Path(resource_app.config["FLOWINONE_RESOURCE_DB_PATH"])))
    for resource_id in ids:
        assert {job["job_type"] for job in queue.list_for_resource(resource_id)} == {
            "fetch_metadata", "download_thumbnail", "extract_content"
        }


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


def test_bookmark_upload_pages_share_import_and_cleanup(resource_app, monkeypatch, tmp_path):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    payload = b'{"roots":{"bookmark_bar":{"type":"folder","children":[{"type":"url","name":"Shared upload","url":"https://example.com/shared-upload"}]}}}'
    client = resource_app.test_client()
    index = BeautifulSoup(client.get("/resources/").data, "html.parser")
    csrf_token = index.select_one('form[action="/resources/import/upload"] input[name="csrf_token"]')["value"]

    page = client.post(
        "/resources/import/upload",
        data={"csrf_token": csrf_token, "enqueue": "0", "file": (io.BytesIO(payload), "bookmarks.json")},
        content_type="multipart/form-data",
    )
    assert page.status_code == 302
    assert "匯入完成：新增 1" in unquote_plus(page.headers["Location"])

    api = client.post(
        "/api/imports/chrome",
        data={"file": (io.BytesIO(payload), "bookmarks.json")},
        content_type="multipart/form-data",
    )
    assert api.status_code == 200
    assert api.get_json()["duplicates"] == 1

    invalid = client.post(
        "/api/imports/chrome",
        data={"file": (io.BytesIO(b"invalid JSON"), "bookmarks.json")},
        content_type="multipart/form-data",
    )
    assert invalid.status_code == 400
    assert list(tmp_path.glob("flowinone-bookmarks-*")) == []
