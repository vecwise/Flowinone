from __future__ import annotations

from unittest.mock import Mock

from flask import Flask, jsonify

import routes
from src.flowinone.web.eagle import attach_pagination_urls
import src.eagle_api as eagle
from src.eagle_api.client import EagleAdminClient, EagleClient
from src.eagle_api.models import EagleCapabilities
from src.file_handler import eagle_integration
from src.file_handler.models import MediaEntry


def _response(payload):
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = payload
    return response


def test_client_uses_v2_base_url_and_post_arrays():
    session = Mock()
    session.request.return_value = _response({
        "status": "success",
        "data": {"data": [], "total": 0, "offset": 0, "limit": 50},
    })
    client = EagleClient(session=session, token="secret")

    client.get_items(tags=["design"], folders=["F1"], limit=25)

    session.request.assert_called_once_with(
        "POST",
        "http://localhost:41595/api/v2/item/get",
        params={"token": "secret"},
        timeout=10.0,
        json={"tags": ["design"], "folders": ["F1"], "limit": 25},
    )


def test_all_pages_collects_v2_pagination():
    session = Mock()
    session.request.side_effect = [
        _response({"status": "success", "data": {"data": [{"id": "1"}, {"id": "2"}], "total": 3, "offset": 0, "limit": 2}}),
        _response({"status": "success", "data": {"data": [{"id": "3"}], "total": 3, "offset": 2, "limit": 2}}),
    ]
    client = EagleClient(session=session)

    result = client.all_pages("tag/get", page_size=2)

    assert result["status"] == "success"
    assert [row["id"] for row in result["data"]["data"]] == ["1", "2", "3"]
    assert session.request.call_count == 2


def test_facade_flattens_paginated_item_results(monkeypatch):
    monkeypatch.setattr(
        eagle.default_client,
        "query_items",
        lambda *args, **kwargs: {
            "status": "success",
            "data": {"data": [{"id": "A"}], "total": 8, "offset": 0, "limit": 1},
        },
    )

    result = eagle.EAGLE_query_items('cat OR "orange dog"', limit=1)

    assert result["data"] == [{"id": "A"}]
    assert result["pagination"] == {"total": 8, "offset": 0, "limit": 1}


def test_existing_helpers_emit_v2_item_shapes(monkeypatch):
    calls = []

    def fake_add_item(**payload):
        calls.append(payload)
        return {"status": "success", "data": {"id": "new"}}

    monkeypatch.setattr(eagle.default_client, "add_item", fake_add_item)

    eagle.EAGLE_add_image_from_url("https://example.com/a.jpg", "F1", tags=["one"])
    eagle.EAGLE_add_bookmark("https://example.com", "Example")

    assert calls[0]["folders"] == ["F1"]
    assert calls[0]["url"].endswith("a.jpg")
    assert calls[1]["bookmarkURL"] == "https://example.com"


def test_legacy_json_add_translates_folder_id(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        eagle.default_client,
        "add_item",
        lambda **payload: captured.update(payload) or {"status": "success"},
    )

    eagle.EAGLE_add_img_from_json({"url": "https://example.com/a.jpg", "folderId": "F1"})

    assert captured == {"url": "https://example.com/a.jpg", "folders": ["F1"]}


def test_ai_status_rejects_unknown_endpoint():
    client = EagleClient(session=Mock())
    try:
        client.ai_status("deleteEverything")
    except ValueError as exc:
        assert "Unknown AI Search" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("unknown endpoint should be rejected")


def test_admin_operations_require_the_opt_in_admin_client():
    assert not hasattr(EagleClient, "switch_library")
    assert not hasattr(EagleClient, "merge_tags")
    assert hasattr(EagleAdminClient, "switch_library")
    assert hasattr(EagleAdminClient, "merge_tags")


def test_capabilities_combines_eagle_and_optional_ai_state():
    session = Mock()
    session.request.side_effect = [
        _response({"status": "success", "data": {"version": "4.0.0", "buildVersion": "build22"}}),
        _response({"status": "success", "data": {"progress": 0.75}}),
        _response({"status": "success", "data": {"data": [], "total": 0, "offset": 0, "limit": 1}}),
        _response({"status": "success", "data": True}),
        _response({"status": "success", "data": True}),
        _response({"status": "success", "data": False}),
        _response({"status": "success", "data": True}),
    ]

    capabilities = EagleClient(session=session).capabilities()

    assert capabilities.available is True
    assert capabilities.supports_smart_folders is True
    assert capabilities.supports_comments is True
    assert capabilities.ai_installed is True
    assert capabilities.ai_ready is True
    assert capabilities.ai_starting is False
    assert capabilities.ai_syncing is True
    assert capabilities.ai_sync_progress == 0.75


def test_ai_similar_results_use_the_v2_results_envelope(monkeypatch):
    monkeypatch.setattr(
        eagle_integration,
        "get_eagle_capabilities",
        lambda: EagleCapabilities(available=True, ai_ready=True),
    )
    monkeypatch.setattr(
        eagle_integration.EG,
        "EAGLE_ai_search_similar",
        lambda *args, **kwargs: {
            "status": "success",
            "data": {"results": [{"item": {"id": "match", "name": "Match", "ext": "jpg"}, "score": 0.875}]},
        },
    )
    monkeypatch.setattr(
        eagle_integration,
        "_format_eagle_items",
        lambda items, **kwargs: [
            MediaEntry(id=item["id"], name=item["name"], url="/image", thumbnail_route="/thumb", item_path=None, media_type="image", ext=item["ext"])
            for item in items
        ],
    )

    matches = eagle_integration._build_eagle_similar_items("current", [], [], limit=6)

    assert [match.id for match in matches] == ["match"]
    assert matches[0].description == "AI similarity 88%"


def test_eagle_sorting_does_not_mutate_or_reorder_when_preserving_api_order():
    items = [
        {"id": "new", "name": "Zebra", "modificationTime": 20},
        {"id": "old", "name": "Apple", "modificationTime": 10},
    ]

    preserved = eagle_integration._sort_eagle_items(items, None)
    newest = eagle_integration._sort_eagle_items(items, "modificationTime", reverse=True)

    assert [item["id"] for item in preserved] == ["new", "old"]
    assert [item["id"] for item in newest] == ["new", "old"]
    assert items[0]["name"] == "Zebra"


def test_eagle_pagination_urls_preserve_existing_query_arguments():
    app = Flask(__name__)

    @app.get("/search")
    def search_page():
        metadata = {
            "pagination": {
                "offset": 120,
                "limit": 120,
                "total": 400,
                "previous_offset": 0,
                "next_offset": 240,
            }
        }
        attach_pagination_urls(metadata)
        return jsonify(metadata)

    response = app.test_client().get("/search?query=orange+cat&offset=120&limit=120")
    pagination = response.get_json()["pagination"]

    assert pagination["previous_url"] == "/search?query=orange+cat&offset=0&limit=120"
    assert pagination["next_url"] == "/search?query=orange+cat&offset=240&limit=120"
