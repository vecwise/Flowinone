"""Gallery cards show source-specific clues without duplicating folder metadata."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from bs4 import BeautifulSoup
from flask import Flask
from sqlalchemy import text

from routes import register_routes
from src.file_handler import chrome_bookmarks
from src.file_handler.thumbnails.store import ThumbnailStore
from src.flowinone.catalog import presentation
from src.flowinone.catalog import sync as catalog_sync
from src.flowinone.catalog.service import CatalogService, CatalogSyncService
from src.flowinone.resource_library.database import get_resource_database


def test_gallery_cards_show_bookmark_identity_and_keep_images_visual(monkeypatch, tmp_path: Path):
    database = get_resource_database(tmp_path / "catalog.db")
    sync = CatalogSyncService(database)
    thumbnail_store = ThumbnailStore(str(tmp_path/'cache.db'), str(tmp_path/'thumbs'))
    site_tile_id = thumbnail_store.register_bookmark('https://tile.example/article', 'Site tile').media_id
    real_image_id = thumbnail_store.register_bookmark('https://photo.example/article', 'Real preview').media_id
    for mid, provider in ((site_tile_id, 'site-tile'), (real_image_id, 'metadata')):
        image = tmp_path / (mid + '.webp')
        image.write_bytes(b'cached image')
        thumbnail_store.record_thumbnail(mid, str(image), provider=provider, source_url=None, width=640, height=420)
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
            conn, identity_key="url:tile", source_kind="bookmarks",
            source_key="https://tile.example/article", item_type="bookmark", title="Site tile",
            original_url="https://tile.example/article", detail_uri="https://tile.example/article",
            metadata={"thumbnail_ref": f"/api/bookmark-thumbnails/{site_tile_id}/image"},
        )
        sync._upsert(
            conn, identity_key="url:photo", source_kind="bookmarks",
            source_key="https://photo.example/article", item_type="bookmark", title="Real preview",
            original_url="https://photo.example/article", detail_uri="https://photo.example/article",
            metadata={"thumbnail_ref": f"/api/bookmark-thumbnails/{real_image_id}/image"},
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
    monkeypatch.setattr(
        presentation, "get_thumbnail_store",
        lambda: thumbnail_store,
    )
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

    # All secondary copy and maintenance actions are reachable but initially folded.
    for card in cards.values():
        more = card.select_one('details.gallery-card-more')
        assert more is not None and 'open' not in more.attrs
        assert not card.select_one('.gallery-card-body .gallery-card-description')
        assert not card.select_one('.gallery-card-body .thumbnail-card-tools')
        assert card.select_one('.gallery-card-body h3 a[title]')
        for secondary in card.select('.gallery-card-description, .navigator-match-reason, .gallery-tags, [data-thumbnail-retry]'):
            assert secondary.find_parent('details') is more
    assert 'open' not in soup.select_one('.navigator-theme-entry').attrs
    assert 'open' not in soup.select_one('[data-thumbnail-tools]').attrs

    bookmark = cards["Type reference"]
    assert bookmark.select_one(".gallery-card-media.is-site-fallback") is not None
    assert bookmark.select_one(".gallery-fallback-copy strong").get_text(strip=True) == "example.org"
    assert bookmark.select_one(".gallery-fallback-mark").get_text(strip=True) == "EX"
    assert bookmark.select_one(".gallery-card-media img") is None
    assert bookmark.select_one(".gallery-card-description").get_text(strip=True) == "A concise typography reference."
    assert bookmark.select_one(".gallery-card-context").get_text(strip=True) == "資料夾：Typography"
    assert [tag.get_text(strip=True) for tag in bookmark.select(".gallery-tags span")] == ["inspiration"]
    assert bookmark.select_one(".gallery-card-open")["href"] == "https://www.example.org/reference"
    assert bookmark.select_one(".gallery-card-open")["target"] == "_blank"
    assert "data-gallery-detail-open" not in bookmark.select_one(".gallery-card-open").attrs
    assert "開啟 1 次" in bookmark.get_text()
    assert bookmark.select_one(".navigator-favorite").get_text(strip=True) == "加入最愛"
    assert bookmark.select_one(".navigator-favorite")["aria-label"] == "加入我的最愛：Type reference"
    assert bookmark.select_one('[data-thumbnail-retry]') is not None
    assert bookmark['data-thumbnail-status'] == 'missing'

    assert cards["Site tile"].select_one(".gallery-card-media.is-site-fallback") is not None
    assert cards["Site tile"].select_one(".gallery-card-media img") is None
    assert cards["Real preview"].select_one(".gallery-card-media.is-site-fallback") is None
    assert cards["Real preview"].select_one(".gallery-card-media img")["src"].endswith(f"/{real_image_id}/image")

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
