from __future__ import annotations

import threading

from run import create_app
from src.flowinone.workers import WorkerRuntime


def _test_config(tmp_path):
    return {
        "TESTING": True,
        "FLOWINONE_RESOURCE_DB_PATH": str(tmp_path / "flowinone.db"),
        "CHROME_BOOKMARK_PATH": str(tmp_path / "Bookmarks"),
        "FLOWINONE_RESOURCE_LINK_THUMBNAILS": False,
    }


def test_application_factory_registers_source_blueprints_without_workers(tmp_path):
    before = {thread.name for thread in threading.enumerate()}
    app = create_app(_test_config(tmp_path))
    after = {thread.name for thread in threading.enumerate()}

    endpoints = {rule.endpoint: rule.rule for rule in app.url_map.iter_rules()}
    assert endpoints["local.view_item_db"] == "/item_db"
    assert endpoints["chrome.view_chrome_folder"] == "/chrome/<path:folder_path>/"
    assert endpoints["eagle.view_eagle_image"] == "/EAGLE_image/<item_id>/"
    assert endpoints["media.view_image"] == "/image/<path:image_path>"
    assert not {name for name in after - before if name.startswith("flowinone-")}


def test_api_contract_rejects_coercion_unknown_fields_and_bad_query(tmp_path):
    client = create_app(_test_config(tmp_path)).test_client()

    coerced = client.post(
        "/api/resources",
        json={"url": "https://example.com", "enqueue": "false"},
    )
    assert coerced.status_code == 400
    assert coerced.get_json()["error"] == "invalid_request"
    assert coerced.get_json()["details"][0]["field"] == "enqueue"

    unknown = client.post(
        "/api/catalog/sync",
        json={"sources": ["local"], "surprise": True},
    )
    assert unknown.status_code == 400
    assert unknown.get_json()["details"][0]["field"] == "surprise"

    invalid_query = client.get("/api/resources?per_page=1000")
    assert invalid_query.status_code == 400
    assert invalid_query.is_json
    assert invalid_query.get_json()["details"][0]["field"] == "per_page"

    malformed = client.post(
        "/api/catalog/sync", data="{", content_type="application/json"
    )
    assert malformed.status_code == 400
    assert malformed.get_json()["details"][0]["type"] == "json_invalid"


def test_api_http_errors_use_the_error_contract(tmp_path):
    response = create_app(_test_config(tmp_path)).test_client().get("/api/not-found")
    assert response.status_code == 404
    assert response.get_json() == {
        "error": "The requested URL was not found on the server. If you entered the URL manually please check your spelling and try again.",
        "details": [],
    }


def test_json_api_success_responses_satisfy_declared_contracts(tmp_path):
    client = create_app(_test_config(tmp_path)).test_client()

    created = client.post(
        "/api/resources",
        json={
            "url": "https://example.com/schema-contract",
            "title": "Schema contract",
            "enqueue": False,
        },
    )
    assert created.status_code == 201
    resource_id = created.get_json()["resource"]["id"]

    assert client.get("/api/resources").status_code == 200
    assert client.get(f"/api/resources/{resource_id}").status_code == 200
    assert client.get(f"/api/resources/{resource_id}/similar").status_code == 200
    assert client.post(
        f"/api/resources/{resource_id}/enrich",
        json={"include_ai": False, "force": False},
    ).status_code == 202

    catalog = client.get("/api/catalog/items?source=resources")
    assert catalog.status_code == 200
    item_id = catalog.get_json()["items"][0]["id"]
    assert client.get(f"/api/catalog/items/{item_id}").status_code == 200
    assert client.post(
        f"/api/catalog/items/{item_id}/events", json={"event_type": "view"}
    ).status_code == 200
    assert client.get(f"/api/catalog/items/{item_id}/related").status_code == 200
    assert client.get("/api/catalog/facets").status_code == 200
    assert client.get("/api/catalog/sync/status").status_code == 200

    session = client.post(
        "/api/catalog/sessions",
        json={"query": {"scope": "all", "sources": ["resources"]}},
    )
    assert session.status_code == 201
    assert client.patch(
        f"/api/catalog/sessions/{session.get_json()['id']}",
        json={
            "query": {"scope": "all", "sources": ["resources"]},
            "scroll_position": 120,
        },
    ).status_code == 200
    assert client.get("/api/catalog/sessions").status_code == 200

    person = client.post("/api/people", json={"display_name": "Designer"})
    assert person.status_code == 201
    assert client.patch(
        f"/api/people/{person.get_json()['id']}",
        json={"display_name": "Researcher"},
    ).status_code == 200

    assert client.get("/api/gallery/items?source=resources").status_code == 400
    assert client.get("/api/gallery/items?source=local").status_code == 200
    assert client.get("/api/gallery/sources").status_code == 200
    assert client.get("/api/bookmark-thumbnails/status").status_code == 200
    assert client.post(
        "/api/bookmark-thumbnails/enqueue", json={"ids": [], "force": False}
    ).status_code == 200


class _FakeWorker:
    def __init__(self):
        self.started = threading.Event()
        self.stopped = threading.Event()

    def run_forever(self):
        self.started.set()
        self.stopped.wait(timeout=2)

    def stop(self):
        self.stopped.set()


def test_dedicated_worker_runtime_owns_and_stops_both_workers():
    thumbnail = _FakeWorker()
    resource = _FakeWorker()
    runtime = WorkerRuntime(
        thumbnail_worker=thumbnail,  # type: ignore[arg-type]
        resource_worker=resource,  # type: ignore[arg-type]
    )
    thread = threading.Thread(target=runtime.run_forever)
    thread.start()
    assert thumbnail.started.wait(timeout=1)
    assert resource.started.wait(timeout=1)

    runtime.stop()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert thumbnail.stopped.is_set()
    assert resource.stopped.is_set()
