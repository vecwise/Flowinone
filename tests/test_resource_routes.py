import io
from pathlib import Path

import pytest
from flask import Flask

from routes import register_routes
from src.flowinone.resource_library.curation import CollectionService, DraftNoteService
from src.flowinone.resource_library.database import get_resource_database
from src.flowinone.resource_library.service import ResourceService


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
    assert client.get("/resources/").status_code == 200
    created = client.post(
        "/api/resources",
        json={"url": "https://example.com/route", "title": "Route Resource", "enqueue": False},
    )
    assert created.status_code == 201
    resource = created.get_json()["resource"]
    assert client.get(resource["detail_url"]).status_code == 200
    assert b"Route Resource" in client.get("/resources/").data

    patched = client.patch(
        f"/api/resources/{resource['id']}", json={"reading_state": "reading", "priority": 5}
    )
    assert patched.status_code == 200
    assert patched.get_json()["reading_state"] == "reading"
    tagged = client.post(
        f"/api/resources/{resource['id']}/tags", json={"tags": ["AI", "Architecture"]}
    )
    assert tagged.status_code == 200
    assert "AI" in tagged.get_json()["tag_names"]
    ai_tag = next(tag for tag in tagged.get_json()["tags"] if tag["name"] == "AI")
    removed = client.delete(f"/api/resources/{resource['id']}/tags/{ai_tag['id']}")
    assert removed.status_code == 200
    assert "AI" not in removed.get_json()["tag_names"]
    archived = client.post(
        f"/api/resources/{resource['id']}/status", json={"status": "archived"}
    )
    assert archived.status_code == 200
    assert archived.get_json()["disposition"] == "archived"
    promoted = client.post(f"/api/resources/{resource['id']}/promote", json={})
    assert promoted.status_code == 200
    assert promoted.get_json()["note"]["note_type"] == "literature"


def test_collection_note_pages_render_mixed_source_flow(resource_app):
    database = get_resource_database(Path(resource_app.config["FLOWINONE_RESOURCE_DB_PATH"]))
    resource = ResourceService(database, link_thumbnail_cache=False).create_url(
        "https://example.com/curation-route", title="Curation", enqueue=False
    )["resource"]
    collections = CollectionService(database)
    collection = collections.create("Route collection")
    collection = collections.add_item(
        collection["id"], source_kind="resource", source_id=resource["id"]
    )
    note = DraftNoteService(database).create_from_collection(collection)

    client = resource_app.test_client()
    collection_index = client.get("/inspiration/")
    assert collection_index.status_code == 200
    assert "靈感 Collections".encode() in collection_index.data
    collection_page = client.get(f"/inspiration/{collection['id']}/")
    assert collection_page.status_code == 200
    assert b"Route collection" in collection_page.data
    note_page = client.get(f"/notes/{note['id']}/")
    assert note_page.status_code == 200
    assert b"Curation" in note_page.data


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
