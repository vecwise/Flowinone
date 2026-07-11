from __future__ import annotations

from unittest.mock import Mock

import src.eagle_api as eagle
from src.eagle_api.client import EagleClient


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
