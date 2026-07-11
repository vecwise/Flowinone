"""Integration coverage for the state-based Entry system."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from flask import Flask

from routes import register_routes


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def entry_app(tmp_path):
    app = Flask(
        "flowinone-entry-test",
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


def test_root_redirects_to_build_and_build_page_renders(entry_app):
    client = entry_app.test_client()
    root = client.get("/", follow_redirects=False)
    assert root.status_code == 302
    assert root.headers["Location"].endswith("/build/")
    page = client.get("/build/")
    assert page.status_code == 200
    assert "回到下一個有意義的行動".encode() in page.data
    assert b"Resource Inbox" not in page.data


def test_project_entry_continue_and_state_restore(entry_app):
    client = entry_app.test_client()
    project_response = client.post(
        "/api/projects",
        json={"name": "Dashboard work", "goal": "Ship the entry system", "next_action": "Write tests"},
    )
    assert project_response.status_code == 201
    project = project_response.get_json()

    entry_response = client.post(
        "/api/entries",
        json={
            "name": "Continue dashboard",
            "mode": "build",
            "kind": "coding_task",
            "status": "active",
            "project_id": project["id"],
            "next_action": "Verify the BUILD dashboard",
            "target": {"type": "flowinone_view", "uri": "/resources/"},
            "state": {
                "query": {"text": "knowledge"},
                "filters": [{"field": "reading_state", "op": "eq", "value": "unread"}],
            },
        },
    )
    assert entry_response.status_code == 201
    entry = entry_response.get_json()

    dashboard = client.get("/api/dashboard/build")
    assert dashboard.status_code == 200
    assert dashboard.get_json()["continue_entry"]["id"] == entry["id"]
    assert dashboard.get_json()["current_project"]["id"] == project["id"]
    assert client.get(f"/entries/{entry['id']}/").status_code == 200
    assert client.get(f"/projects/{project['id']}/").status_code == 200

    entered = client.post(f"/api/entries/{entry['id']}/enter")
    assert entered.status_code == 200
    destination = entered.get_json()["destination"]
    parsed = urlsplit(destination)
    assert parsed.path == "/resources/"
    assert parse_qs(parsed.query) == {"q": ["knowledge"], "reading_state": ["unread"]}

    form_entered = client.post(f"/entries/{entry['id']}/enter", follow_redirects=False)
    assert form_entered.status_code == 303
    assert "/resources/" in form_entered.headers["Location"]


def test_think_to_build_creates_linked_entries(entry_app):
    client = entry_app.test_client()
    response = client.post(
        "/api/entries/think-to-build",
        json={
            "problem": "How should Flowinone resume a task?",
            "hypothesis": "Prefer an active Entry with a next action.",
            "next_action": "Implement the scoring test",
            "target": {"type": "flowinone_view", "uri": "/build/"},
        },
    )
    assert response.status_code == 201
    payload = response.get_json()
    assert payload["think_entry"]["mode"] == "think"
    assert payload["think_entry"]["status"] == "completed"
    assert payload["build_entry"]["mode"] == "build"
    assert payload["build_entry"]["source_entry_id"] == payload["think_entry"]["id"]


def test_snapshot_and_scan_boundary_validation(entry_app):
    client = entry_app.test_client()
    snapshot = client.post(
        "/entries/snapshot",
        data={
            "name": "Unread systems reading",
            "mode": "learn",
            "target_uri": "/resources/?reading_state=unread",
            "context": json.dumps({"source": {"kind": "resource_view", "id": "unread"}}),
            "state": json.dumps({"query": {"text": "systems"}}),
        },
        follow_redirects=False,
    )
    assert snapshot.status_code == 302
    assert "/entries/" in snapshot.headers["Location"]

    unsafe = client.post(
        "/api/entries",
        json={
            "name": "Unsafe",
            "mode": "learn",
            "target": {"type": "external", "uri": "javascript:alert(1)"},
        },
    )
    assert unsafe.status_code == 400

    unbounded_scan = client.post(
        "/api/entries",
        json={"name": "No stopping", "mode": "scan", "status": "active"},
    )
    assert unbounded_scan.status_code == 400

    bounded_scan = client.post(
        "/api/entries",
        json={
            "name": "Morning scan",
            "mode": "scan",
            "status": "active",
            "context": {"scan": {"time_budget_minutes": 20, "item_limit": 12}},
        },
    )
    assert bounded_scan.status_code == 201
