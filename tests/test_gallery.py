"""Gallery domain coverage: flat sources, query state, cursor, and HTML."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from flask import Flask

from routes import register_routes
from src.flowinone.gallery.models import GalleryItem, GalleryQuery
from src.flowinone.gallery.service import GalleryService, InvalidGalleryCursor


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def sample_service() -> GalleryService:
    return GalleryService(
        {
            "local": lambda: [
                GalleryItem(
                    id="local:image-1",
                    source="local",
                    media_type="image",
                    title="Travel reference",
                    thumbnail_url="/static/default_thumbnail.svg",
                    tags=["travel", "reference"],
                    relative_path="travel.jpg",
                    created_at="2026-07-11T10:00:00Z",
                )
            ],
            "eagle": lambda: [
                GalleryItem(
                    id="eagle:video-1",
                    source="eagle",
                    media_type="video",
                    title="Motion study",
                    thumbnail_url="/static/default_video_thumbnail.svg",
                    tags=["motion"],
                )
            ],
            "bookmarks": lambda: [
                GalleryItem(
                    id="bookmarks:bookmark-1",
                    source="bookmarks",
                    media_type="bookmark",
                    title="Visual archive",
                    thumbnail_url="/static/default_thumbnail.svg",
                    original_url="https://example.com/visual",
                )
            ],
        }
    )


@pytest.fixture
def gallery_app(tmp_path):
    app = Flask(
        "flowinone-gallery-test",
        template_folder=str(PROJECT_ROOT / "templates"),
        static_folder=str(PROJECT_ROOT / "static"),
    )
    app.config.update(
        TESTING=True,
        FLOWINONE_RESOURCE_DB_PATH=str(tmp_path / "resource.db"),
        FLOWINONE_RESOURCE_LINK_THUMBNAILS=False,
        FLOWINONE_GALLERY_SERVICE_FACTORY=sample_service,
    )
    register_routes(app)
    return app


def test_gallery_query_combines_sources_without_folder_nodes():
    page = sample_service().list_items(
        GalleryQuery.create(sources=["local", "eagle", "bookmarks"], sort="title")
    )
    assert page.total == 3
    assert {item.source for item in page.items} == {"local", "eagle", "bookmarks"}
    assert {item.media_type for item in page.items} == {"image", "video", "bookmark"}
    assert all(item.media_type != "folder" for item in page.items)


def test_gallery_search_filter_and_cursor_signature():
    service = sample_service()
    first_query = GalleryQuery.create(sources=["local", "eagle"], limit=1, sort="title")
    first = service.list_items(first_query)
    assert len(first.items) == 1
    assert first.next_cursor
    second = service.list_items(
        GalleryQuery.create(
            sources=["local", "eagle"], limit=1, sort="title", cursor=first.next_cursor
        )
    )
    assert second.items[0].id != first.items[0].id
    with pytest.raises(InvalidGalleryCursor):
        service.list_items(
            GalleryQuery.create(sources=["local"], limit=1, sort="title", cursor=first.next_cursor)
        )
    searched = service.list_items(
        GalleryQuery.create(q="travel", sources=["local", "eagle"], sort="relevance")
    )
    assert [item.id for item in searched.items] == ["local:image-1"]


def test_gallery_html_and_api_keep_source_checkboxes_on_one_page(gallery_app):
    client = gallery_app.test_client()
    page = client.get("/gallery/?source=local&source=bookmarks&type=image")
    assert page.status_code == 200
    assert page.data.count(b'type="checkbox" name="source"') == 3
    assert "資料夾只保留為描述或 tag".encode() in page.data

    response = client.get("/api/gallery/items?source=local&source=eagle&limit=1")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["total_estimate"] == 2
    assert payload["query"]["sources"] == ["local", "eagle"]
    assert "absolute_path" not in payload["items"][0]

    random_redirect = client.get("/gallery/?source=local&sort=random", follow_redirects=False)
    assert random_redirect.status_code == 302
    parsed = urlsplit(random_redirect.headers["Location"])
    assert parse_qs(parsed.query)["seed"][0].isdigit()
