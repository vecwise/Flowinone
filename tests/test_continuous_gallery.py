"""Original images and inline video must retain source containment and byte ranges."""
from urllib.parse import parse_qs, urlsplit
import shutil

import pytest
from bs4 import BeautifulSoup
from PIL import Image

from run import create_app
from src.file_handler import fs_media
from src.flowinone import config
from src.flowinone.catalog.sync import CatalogSyncService
from src.flowinone.resource_library.database import get_resource_database


@pytest.fixture
def continuous_client(tmp_path, monkeypatch):
    media = tmp_path / "media"
    media.mkdir()
    Image.new("RGB", (72, 240), "navy").save(media / "01.png")
    Image.new("RGB", (12, 12), "white").save(media / "thumb.png")
    (media / "02.mp4").write_bytes(b"range-capable-video")
    for module in (config, fs_media):
        monkeypatch.setattr(module, "DB_route_external", str(media))
        monkeypatch.setattr(module, "DB_route_internal", str(media))
    monkeypatch.setattr("src.flowinone.web.common.compute_feature_flags", lambda: {"db": True})
    app = create_app({"TESTING": True, "FLOWINONE_RESOURCE_DB_PATH": str(tmp_path / "catalog.db"),
                      "FLOWINONE_CATALOG_SYNC_INLINE": False, "FLOWINONE_RESOURCE_LINK_THUMBNAILS": False})
    db = get_resource_database(tmp_path / "catalog.db")
    with db.write_transaction() as conn:
        for kind, name in (("image", "01.png"), ("video", "02.mp4")):
            CatalogSyncService(db)._upsert(conn, identity_key=name, source_kind="local", source_key=name,
                item_type=kind, title=name, source_path=name, detail_uri=f"/{kind}/{name}?src=internal",
                thumbnail_ref=f"/serve_image/{str(media / 'thumb.png').lstrip('/')}")
    return app.test_client(), media


def test_catalog_original_media_are_distinct_from_thumbnails_and_range_capable(continuous_client):
    client, media = continuous_client
    soup = BeautifulSoup(client.get('/navigator/?source=local&view=continuous&names=show&sort=page_order').data, 'html.parser')
    assert soup.select_one('[data-gallery-view="list"][data-gallery-names="show"]')
    assert soup.select_one('[data-gallery-view-option="list"]').get_text() == '連續'
    image = soup.select_one('[data-continuous-media] img')
    video = soup.select_one('[data-continuous-media] video')
    assert image['data-media-src'] != soup.select_one('.gallery-card-media img')['src']
    assert video.has_attr('controls') and video['preload'] == 'none'
    for element, name in ((image, '01.png'), (video, '02.mp4')):
        redirect = client.get(element['data-media-src'])
        assert redirect.status_code == 302
        target = redirect.location
        assert target.startswith('/serve_') and not urlsplit(target).netloc
        response = client.get(target, headers={'Range': 'bytes=0-7'})
        assert response.status_code == 206
        assert response.data == (media / name).read_bytes()[:8]
    assert client.get(image['data-media-src'].replace('/image?', '/video?')).status_code == 404


def test_direct_folder_mixes_full_images_and_video_and_rejects_path_escape(continuous_client, tmp_path):
    client, media = continuous_client
    (media / 'board').mkdir()
    for name in ('01.png', '02.mp4'):
        shutil.copyfile(media / name, media / 'board' / name)
    soup = BeautifulSoup(client.get('/both/board/?src=internal&view=list').data, 'html.parser')
    for element, prefix in ((soup.select_one('[data-continuous-media] img'), '/serve_image/'),
                            (soup.select_one('[data-continuous-media] video'), '/serve_video/')):
        response = client.get(element['data-media-src'])
        assert response.status_code == 302 and response.location.startswith(prefix)
        assert client.get(response.location).status_code == 200
    Image.new('RGB', (10, 10)).save(tmp_path / 'outside.png')
    assert client.get('/browse-media/local/image/../outside.png?src=internal').status_code == 404
    assert client.get('/browse-media/local/image/01.png?src=invalid').status_code == 404
    assert client.get('/browse-media/resources/image/01.png').status_code == 404


def test_next_batch_preserves_presentation_and_media_order(continuous_client):
    client, _ = continuous_client
    soup = BeautifulSoup(client.get('/navigator/?source=local&view=list&names=hide&sort=page_order&limit=1').data, 'html.parser')
    next_url = soup.select_one('[data-browse-next]')['href']
    params = parse_qs(urlsplit(next_url).query)
    assert params['names'] == ['hide'] and params['view'] == ['list']
    next_page = BeautifulSoup(client.get(next_url).data, 'html.parser')
    assert next_page.select_one('[data-continuous-media] video')
    assert next_page.select_one('[data-browse-next]') is None
