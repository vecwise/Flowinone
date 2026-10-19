"""Media categories filter the entire query; thumbnail shapes never filter data."""

from urllib.parse import parse_qs, urlsplit

import pytest
from bs4 import BeautifulSoup
from sqlalchemy import text

from run import create_app
from src.flowinone.catalog.browse import CatalogService
from src.flowinone.catalog.media import media_category, media_category_sql
from src.flowinone.catalog.query import CatalogQuery
from src.flowinone.catalog.sync import CatalogSyncService
from src.flowinone.resource_library.database import get_resource_database
from src.flowinone.web.ui import render_source_browse


@pytest.fixture
def media_rules(tmp_path, monkeypatch):
    from src.file_handler.thumbnails import extensions

    provider = tmp_path / 'media_provider.py'
    provider.write_text('''
def media_url_rules():
    return {
        "video": (("video.example", "/%/video/%"), ("clips.example", "/video.%")),
        "album": (("gallery.example", "/photos-%aid-%.html%"),
                  ("gallery.example", "/albums-index%aid-%.html%"),
                  ("photos.example", "/zh-hant/article/%")),
    }
''', encoding='utf-8')
    monkeypatch.setenv(extensions.LOCAL_PROVIDER_ENV, str(provider))
    return provider


@pytest.fixture
def media_app(tmp_path, monkeypatch, media_rules):
    monkeypatch.setattr('src.flowinone.web.common.compute_feature_flags', lambda: {
        'eagle': False, 'chrome': False, 'youtube': False, 'db': False,
    })
    app = create_app({
        'TESTING': True, 'FLOWINONE_RESOURCE_DB_PATH': str(tmp_path / 'media.db'),
        'FLOWINONE_CATALOG_SYNC_INLINE': False, 'FLOWINONE_RESOURCE_LINK_THUMBNAILS': False,
    })
    database = get_resource_database(tmp_path / 'media.db')
    sync = CatalogSyncService(database)
    with database.write_transaction() as conn:
        for index, (kind, url) in enumerate([
            ('video', ''), ('web_page', 'https://www.youtube.com/watch?v=demo'),
            ('web_page', 'https://video.example/demo/video/example'),
            ('image', ''), ('image', ''),
            ('web_page', 'https://gallery.example/photos-slide-aid-123.html'),
            ('article', 'https://photos.example/zh-hant/article/123/'),
            ('web_page', 'https://gallery.example/albums.html'),
            ('article', 'https://example.com/reference'),
        ]):
            sync._upsert(conn, identity_key=f'test:{index}', source_kind='local', source_key=str(index),
                         item_type=kind, title=f'Reference {index}', original_url=url or None,
                         detail_uri=f'/image/reference-{index}.jpg?src=internal', tags=(('design', 'manual'),))
    return app, CatalogService(database)


@pytest.mark.parametrize('kind,url,expected', [
    ('image', '', 'image'), ('video', '', 'video'), ('comic', '', 'album'),
    ('article', 'https://gallery.example/photos-slide-aid-123.html', 'album'),
    ('bookmark', 'https://gallery.example/albums-index-page-1-aid-123.html', 'album'),
    ('web_page', 'http://photos.example/zh-hant/article/123/', 'album'),
    ('web_page', 'https://clips.example/video.demo/example', 'video'),
    ('web_page', 'https://video.example/demo/video/example', 'video'),
    ('article', 'https://www.youtube.com/watch?feature=share&v=demo', 'video'),
    ('web_page', 'https://youtu.be/demo?t=1', 'video'),
    ('web_page', 'https://www.youtube.com/shorts/demo', 'video'),
    ('web_page', 'https://www.youtube.com/watch?list=demo', 'page'),
    ('web_page', 'https://gallery.example/albums.html', 'page'),
    ('web_page', 'https://photos.example/zh-hant/topic/example/', 'page'),
    ('web_page', 'https://video.example/', 'page'),
    ('web_page', 'https://clips.example/', 'page'),
    ('web_page', 'https://gallery.example.evil.example/photos-slide-aid-123.html', 'page'),
    ('web_page', 'https://example.com/https://gallery.example/photos-slide-aid-123.html', 'page'),
    ('web_page', 'https://www.youtube.com@evil.example/watch?v=demo', 'page'),
])
def test_url_rules_agree_between_sql_and_cards(media_app, kind, url, expected):
    _, service = media_app
    with service.database.engine.connect() as conn:
        result = conn.execute(text(f'SELECT {media_category_sql()} FROM (SELECT :kind AS item_type, :url AS original_url) i'),
                              {'kind': kind, 'url': url}).scalar()
    assert result == media_category(kind, url) == expected


def test_without_local_rules_only_generic_categories_apply():
    import sqlite3

    with sqlite3.connect(':memory:') as conn:
        for kind, url, expected in [
            ('web_page', 'https://video.example/demo/video/example', 'page'),
            ('article', 'https://gallery.example/photos-slide-aid-123.html', 'page'),
            ('web_page', 'https://www.youtube.com/watch?v=demo', 'video'),
            ('comic', 'https://example.com/gallery', 'album'),
        ]:
            result = conn.execute(f'SELECT {media_category_sql()} FROM (SELECT ? AS item_type, ? AS original_url) i',
                                  (kind, url)).fetchone()[0]
            assert result == media_category(kind, url) == expected


def test_local_image_rules_escape_quotes_in_sql_and_keep_host_boundaries(monkeypatch):
    import sqlite3
    from types import SimpleNamespace
    from src.file_handler.thumbnails import extensions

    monkeypatch.setattr(extensions, 'load_local_provider', lambda: SimpleNamespace(
        media_url_rules=lambda: {'image': (("images.example", "/it's_%"),)}))
    with sqlite3.connect(':memory:') as conn:
        for url, expected in [("https://images.example/it's_demo", 'image'),
                              ("https://images.example.evil.example/it's_demo", 'page'),
                              ("https://images.example@evil.example/it's_demo", 'page')]:
            result = conn.execute(f'SELECT {media_category_sql()} FROM (SELECT ? AS item_type, ? AS original_url) i',
                                  ('web_page', url)).fetchone()[0]
            assert result == media_category('web_page', url) == expected


def test_media_pagination_related_and_cached_counts_use_the_full_filter(media_app):
    _, service = media_app
    all_ids = [item['id'] for item in service.list(CatalogQuery.create())['items']]
    for media, count in [('video', 3), ('image', 2), ('album', 2), ('page', 2)]:
        base = dict(q='Reference', sources=['local'], tags=['design'], sort='title', media=media, limit=1)
        first = service.list(CatalogQuery.create(**base))
        assert first['total_estimate'] == count
        found = []
        page = first
        while True:
            found.extend(page['items'])
            assert all(item['media_category'] == media for item in page['items'])
            if not page['next_cursor']:
                break
            page = service.list(CatalogQuery.create(**{**base, 'cursor': page['next_cursor']}))
        assert len({item['id'] for item in found}) == count
        assert service.matching_ids(CatalogQuery.create(**base), all_ids) == {item['id'] for item in found}
    assert CatalogQuery.create(media='invalid').public_dict() == CatalogQuery.create().public_dict()
    assert CatalogQuery.create(media='video').signature() != CatalogQuery.create(media='image').signature()


def test_api_saved_conditions_and_preview_recommendations_keep_media(media_app):
    app, service = media_app
    client = app.test_client()
    payload = client.get('/api/catalog/items?media=album&source=local').get_json()
    assert payload['total_estimate'] == 2
    assert payload['query']['media'] == 'album'
    saved = service.save_search('圖集', CatalogQuery.create(media='album', sources=['local']).public_dict())
    response = client.get(f"/navigator/views/{saved['id']}/?shape=portrait&ui=curator")
    assert parse_qs(urlsplit(response.location).query)['media'] == ['album']
    assert parse_qs(urlsplit(response.location).query)['shape'] == ['portrait']
    related = client.get(f"/api/catalog/items/{payload['items'][0]['id']}/related?media=album&source=local")
    assert related.status_code == 200
    expected = {item['id'] for item in payload['items']}
    assert all(item['id'] in expected for item in related.get_json()['items'])


@pytest.mark.parametrize('ui', ['baseline', 'curator', 'workbench'])
def test_shared_browse_ui_keeps_filters_paging_and_shape_without_filtering_data(media_app, ui):
    app, _ = media_app
    client = app.test_client()
    path = f'/navigator/?q=Reference&source=local&tags=design&media=video&shape=portrait&limit=1&ui={ui}'
    page = BeautifulSoup(client.get(path).data, 'html.parser')
    assert page.select_one('input[name="media"][value="video"][checked]')
    assert page.select_one('[data-gallery-shape="portrait"][data-media-scope="video"]')
    assert page.select_one('[data-media-category="video"]')
    assert page.select_one('.navigator-advanced #navigator-type')
    next_url = page.select_one('[data-browse-link]')['href']
    params = parse_qs(urlsplit(next_url).query)
    for key, value in [('media', 'video'), ('shape', 'portrait'), ('source', 'local'), ('tags', 'design'), ('ui', ui)]:
        assert params[key] == [value]
    second = BeautifulSoup(client.get(next_url).data, 'html.parser')
    last = BeautifulSoup(client.get(second.select_one('[data-browse-link]')['href']).data, 'html.parser')
    assert last.select_one('#navigator-results-heading').get_text(strip=True) == '3 個項目'
    assert len(last.select('.gallery-card')) == 1
    landscape = BeautifulSoup(client.get(path.replace('shape=portrait', 'shape=landscape')).data, 'html.parser')
    assert [card['id'] for card in landscape.select('.gallery-card')] == [card['id'] for card in page.select('.gallery-card')]
    empty = BeautifulSoup(client.get(f'/navigator/?q=nomatches&media=album&ui={ui}').data, 'html.parser')
    assert empty.select_one('input[value="album"][checked]')
    assert empty.select_one('.gallery-empty a')


def test_source_folders_share_shape_rules_and_legacy_type_links_still_choose_media_ratio(media_app):
    app, _ = media_app
    for kinds, scope in [(['image', 'image'], 'image'), (['video'], 'video'),
                         (['image', 'video'], ''), (['folder', 'image'], '')]:
        with app.test_request_context('/both/example/?shape=square'):
            html, status = render_source_browse(metadata={'name': 'Source examples'}, data=[
                {'id': str(index), 'name': kind, 'media_type': kind} for index, kind in enumerate(kinds)
            ])
            page = BeautifulSoup(html, 'html.parser')
            assert status == 200
            assert page.select_one('[data-gallery-shape="square"]')['data-media-scope'] == scope
    page = BeautifulSoup(app.test_client().get('/navigator/?type=image&source=local').data, 'html.parser')
    assert page.select_one('[data-media-scope="image"]')
