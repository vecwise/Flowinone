"""Closed-loop Knowledge OS coverage for context, decisions, WRITE, and outputs."""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

from routes import register_routes


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def knowledge_app(tmp_path):
    app = Flask(
        "flowinone-knowledge-test",
        template_folder=str(PROJECT_ROOT / "templates"),
        static_folder=str(PROJECT_ROOT / "static"),
    )
    app.config.update(
        TESTING=True,
        FLOWINONE_RESOURCE_DB_PATH=str(tmp_path / "resource.db"),
        FLOWINONE_RESOURCE_LINK_THUMBNAILS=False,
    )
    register_routes(app)
    return app


def test_resource_context_retrieval_decision_and_output_asset(knowledge_app):
    client = knowledge_app.test_client()
    project = client.post(
        "/api/projects", json={"name": "Knowledge OS", "goal": "Close the resource loop"}
    ).get_json()
    resource = client.post(
        "/api/resources",
        json={"url": "https://example.com/knowledge", "title": "Knowledge workflow", "enqueue": False},
    ).get_json()["resource"]

    context = client.post(
        f"/api/resources/{resource['id']}/context",
        json={"project_id": project["id"], "modes": ["build", "write"], "relevance": 0.9},
    )
    assert context.status_code == 200
    assert {row["mode"] for row in context.get_json()["modes"]} == {"build", "write"}

    retrieved = client.get(f"/api/retrieval?project_id={project['id']}&mode=build")
    assert [item["id"] for item in retrieved.get_json()["items"]] == [resource["id"]]

    decision = client.post(
        f"/api/projects/{project['id']}/decisions",
        json={
            "question": "Which boundary should Gallery use?",
            "decision": "Keep Gallery independent from notes",
            "rationale": "Browsing and knowledge workflows have different state.",
        },
    )
    assert decision.status_code == 201
    decision_id = decision.get_json()["id"]

    asset = client.post(
        "/api/output-assets",
        json={
            "title": "Gallery boundary ADR",
            "asset_type": "adr",
            "project_id": project["id"],
            "sources": [
                {"source_type": "resource", "source_id": resource["id"]},
                {"source_type": "decision", "source_id": decision_id},
            ],
        },
    )
    assert asset.status_code == 201
    assert len(asset.get_json()["sources"]) == 2
    assert client.get(f"/api/output-assets?project_id={project['id']}").get_json()["items"][0]["title"] == "Gallery boundary ADR"


def test_write_mode_accepts_entries_and_renders(knowledge_app):
    client = knowledge_app.test_client()
    created = client.post(
        "/api/entries",
        json={"name": "Draft spec", "mode": "write", "next_action": "Write acceptance criteria"},
    )
    assert created.status_code == 201
    assert created.get_json()["mode"] == "write"
    page = client.get("/write/")
    assert page.status_code == 200
    assert "把已理解的內容變成可交付資產".encode() in page.data
    assert b"Draft spec" in page.data
