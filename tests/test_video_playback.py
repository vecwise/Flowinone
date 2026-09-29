"""Local and Eagle videos must reach a range-capable, contained stream."""

from __future__ import annotations

from pathlib import Path

from bs4 import BeautifulSoup
from sqlalchemy import text

from run import create_app
from src.file_handler import eagle_integration, fs_media
from src.flowinone import config
from src.flowinone.catalog import sync as catalog_sync
from src.flowinone.catalog.service import CatalogSyncService
from src.flowinone.resource_library.database import get_resource_database
from src.flowinone.web import common


def test_local_and_eagle_video_pages_and_gallery_streams(monkeypatch, tmp_path: Path):
    external = tmp_path / "external"
    internal = tmp_path / "internal"
    eagle = tmp_path / "library.library"
    for root in (external, internal, eagle):
        root.mkdir()
    (external / "external.mp4").write_bytes(b"external-video-data")
    (external / "not-video.txt").write_bytes(b"not-video-data")
    (internal / "internal.mp4").write_bytes(b"internal-video-data")
    eagle_item = eagle / "images" / "eagle-1.info"
    eagle_item.mkdir(parents=True)
    (eagle_item / "clip.mp4").write_bytes(b"eagle-video-data")

    monkeypatch.setattr(config, "DB_route_external", str(external))
    monkeypatch.setattr(config, "DB_route_internal", str(internal))
    monkeypatch.setattr(fs_media, "DB_route_external", str(external))
    monkeypatch.setattr(fs_media, "DB_route_internal", str(internal))
    monkeypatch.setattr(common, "is_eagle_available", lambda: True)
    monkeypatch.setattr(eagle_integration, "_get_eagle_library_path", lambda: str(eagle))
    monkeypatch.setattr(eagle_integration, "_build_eagle_folder_links", lambda _ids: [])
    monkeypatch.setattr(eagle_integration, "_build_eagle_similar_items", lambda *_args: [])
    monkeypatch.setattr(
        eagle_integration.EG,
        "EAGLE_get_item_info",
        lambda _id: {"status": "success", "data": {"name": "clip", "fileName": "clip.mp4", "ext": "mp4"}},
    )

    database = get_resource_database(tmp_path / "catalog.db")
    sync = CatalogSyncService(database)
    with database.engine.begin() as conn:
        local_id = sync._upsert(
            conn, identity_key="local:internal", source_kind="local", source_key="internal",
            item_type="video", title="Internal video", source_path="internal.mp4",
            detail_uri="/video/internal.mp4?src=internal",
        )
        eagle_id = sync._upsert(
            conn, identity_key="eagle:eagle-1", source_kind="eagle", source_key="eagle-1",
            item_type="video", title="Eagle video", detail_uri="/EAGLE_video/eagle-1/",
        )

    app = create_app({
        "TESTING": True,
        "FLOWINONE_RESOURCE_DB_PATH": str(database.path),
        "FLOWINONE_RESOURCE_LINK_THUMBNAILS": False,
        "FLOWINONE_MEDIA_ROOTS": [str(external), str(internal), str(eagle)],
    })
    client = app.test_client()
    for detail_url, expected_bytes in (
        ("/video/external.mp4?src=external", b"external-video-data"),
        ("/video/internal.mp4?src=internal", b"internal-video-data"),
        ("/video/internal.mp4?src=external", b"internal-video-data"),  # old Catalog links
        ("/EAGLE_video/eagle-1/", b"eagle-video-data"),
    ):
        page = client.get(detail_url)
        assert page.status_code == 200
        source = BeautifulSoup(page.data, "html.parser").select_one("video source")
        assert source["src"].startswith("/serve_video/")
        assert source["type"] == "video/mp4"
        stream = client.get(source["src"], headers={"Range": "bytes=0-5"})
        assert stream.status_code == 206
        assert stream.data == expected_bytes[:6]
        assert stream.headers["Content-Range"].startswith("bytes 0-5/")

    for item_id, source in ((local_id, "local"), (eagle_id, "eagle")):
        item = client.get(f"/api/catalog/items/{item_id}?source={source}").get_json()
        assert item["playback_uri"].endswith(f"?source={source}")
        playback = client.get(item["playback_uri"])
        assert playback.status_code == 302
        assert playback.headers["Location"].startswith("/serve_video/")
        assert client.get(playback.headers["Location"], headers={"Range": "bytes=0-3"}).status_code == 206

    assert client.get(f"/serve_video/{str(tmp_path / 'elsewhere.mp4').lstrip('/')}").status_code != 200
    assert client.get(f"/serve_video/{str(external / 'not-video.txt').lstrip('/')}").status_code == 404


def test_catalog_sync_marks_internal_local_videos(monkeypatch, tmp_path: Path):
    external = tmp_path / "external"
    internal = tmp_path / "internal"
    external.mkdir()
    internal.mkdir()
    monkeypatch.setattr(config, "DB_route_external", str(external))
    monkeypatch.setattr(config, "DB_route_internal", str(internal))
    row = {
        "item_id": "local-video", "item_type": "video", "relative_path": "clip.mp4",
        "absolute_path": str(internal / "clip.mp4"), "library_root": str(internal),
        "name": "clip.mp4", "thumbnail_route": "", "tags": [],
    }
    monkeypatch.setattr(catalog_sync, "fetch_items", lambda **_kwargs: {"items": [row], "total": 1})
    database = get_resource_database(tmp_path / "catalog.db")
    with database.engine.begin() as conn:
        assert CatalogSyncService(database)._sync_local(conn) == 1
        detail_uri = conn.execute(text("SELECT detail_uri FROM catalog_origins WHERE source_key='local-video'")).scalar_one()
    assert detail_uri == "/video/clip.mp4?src=internal"
