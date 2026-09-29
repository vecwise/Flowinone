"""Gallery cards show source-specific clues without duplicating folder metadata."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from bs4 import BeautifulSoup
from flask import Flask
from sqlalchemy import text

from routes import register_routes
from src.file_handler import chrome_bookmarks
from src.flowinone.catalog import sync as catalog_sync
from src.flowinone.catalog.service import CatalogService, CatalogSyncService
from src.flowinone.resource_library.database import get_resource_database


def test_gallery_cards_show_bookmark_identity_and_keep_images_visual(tmp_path: Path):
    database = get_resource_database(tmp_path / "catalog.db")
    sync = CatalogSyncService(database)
    with database.engine.begin() as conn:
        bookmark_id = sync._upsert(
            conn, identity_key="url:reference", source_kind="bookmarks",
            source_key="https://www.example.org/reference\nDesign / Typography",
            item_type="bookmark", title="Type reference", description="A concise typography reference.",
            original_url="https://www.example.org/reference", detail_uri="https://www.example.org/reference",
            tags=(("Design", "folder"), ("Typography", "folder"), ("inspiration", "manual")),
            metadata={"folder_path": "Design / Typography"},
        )
        sync._upsert(
            conn, identity_key="local:portrait", source_kind="local", source_key="portrait",
            item_type="image", title="Portrait study", description="Light and shadow study.",
            source_path="Boards/portrait.jpg", detail_uri="/image/Boards/portrait.jpg?src=external",
            tags=(("portrait", "manual"), ("lighting", "manual"), ("reference", "manual")),
        )
        sync._upsert(
            conn, identity_key="url:legacy", source_kind="bookmarks", source_key="https://legacy.example/x",
            item_type="bookmark", title="Legacy bookmark", description="Design / Typography",
            original_url="https://legacy.example/x", detail_uri="https://legacy.example/x",
            metadata={"folder_path": "Design / Typography"},
        )
    CatalogService(database).record_event(bookmark_id, "open")
    app = Flask(
        "gallery-card-clues",
        template_folder=str(Path(__file__).parents[1] / "templates"),
        static_folder=str(Path(__file__).parents[1] / "static"),
    )
    app.config.update(TESTING=True, FLOWINONE_RESOURCE_DB_PATH=str(database.path), FLOWINONE_RESOURCE_LINK_THUMBNAILS=False)
    register_routes(app)
    page = app.test_client().get("/navigator/?scope=gallery&source=bookmarks&source=local")
    assert page.status_code == 200
    soup = BeautifulSoup(page.data, "html.parser")
    cards = {card.select_one("h3").get_text(strip=True): card for card in soup.select(".gallery-card")}

    bookmark = cards["Type reference"]
    assert bookmark.select_one(".gallery-site-domain").get_text(strip=True) == "example.org"
    assert bookmark.select_one(".gallery-site-mark").get_text(strip=True) == "EX"
    assert bookmark.select_one(".gallery-card-description").get_text(strip=True) == "A concise typography reference."
    assert bookmark.select_one(".gallery-card-context").get_text(strip=True) == "收藏於 Typography"
    assert [tag.get_text(strip=True) for tag in bookmark.select(".gallery-tags span")] == ["inspiration"]
    assert bookmark.select_one(".gallery-card-open")["href"] == "https://www.example.org/reference"
    assert bookmark.select_one(".gallery-card-open")["target"] == "_blank"
    assert "data-gallery-detail-open" not in bookmark.select_one(".gallery-card-open").attrs
    assert "開啟 1 次" in bookmark.get_text()
    assert bookmark.select_one(".navigator-favorite") is not None

    image = cards["Portrait study"]
    assert image.select_one(".gallery-card-media img") is not None
    assert image.select_one(".gallery-card-collection").get_text(strip=True) == "Boards"
    assert image.select_one(".gallery-card-description").get_text(strip=True) == "Light and shadow study."
    assert len(image.select(".gallery-tags span")) == 2
    assert image.select_one(".gallery-card-open") is None

    assert cards["Legacy bookmark"].select_one(".gallery-card-description") is None


def test_chrome_bookmark_descriptions_are_used_when_available(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(chrome_bookmarks, "_load_chrome_bookmarks", lambda: {
        "roots": {"bookmark_bar": {"type": "folder", "name": "Saved", "children": [
            {"type": "url", "url": "https://example.org", "name": "Example", "meta_info": {"description": "A useful site"}},
        ]}},
    })
    records = list(chrome_bookmarks.iter_chrome_bookmark_records())
    assert records[0]["description"] == "A useful site"
    monkeypatch.setattr(catalog_sync, "iter_chrome_bookmark_records", lambda: iter(records))
    monkeypatch.setattr(catalog_sync, "lookup_thumbnail_for_bookmark", lambda *_args, **_kwargs: SimpleNamespace(route=None, sub_type=None))
    database = get_resource_database(tmp_path / "bookmarks.db")
    assert CatalogSyncService(database).sync(("bookmarks",))["bookmarks"]["status"] == "complete"
    with database.engine.connect() as conn:
        assert conn.execute(text("SELECT description FROM catalog_items")).scalar_one() == "A useful site"
