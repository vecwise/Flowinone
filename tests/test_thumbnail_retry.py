"""UI retry endpoints operate on live Chrome bookmarks and retain queue claims."""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from flask import Flask

import routes
from src.file_handler import chrome_bookmarks
from src.file_handler.thumbnails.store import ThumbnailStore
from src.flowinone.web import chrome as chrome_routes, common


@pytest.fixture
def retry_app(monkeypatch, tmp_path):
    store = ThumbnailStore(str(tmp_path/'cache.db'), str(tmp_path/'thumbs'))
    records = []
    def bookmark(name, *, provider=None, status='missing', live=True):
        url = f'https://example.com/{name}'
        mid = store.register_bookmark(url, name, enqueue_missing=False).media_id
        if provider:
            image = tmp_path / f'{name}.webp'
            image.write_bytes(b'cached image')
            store.record_thumbnail(mid, str(image), provider=provider, source_url=None,
                width=640, height=420, status=status)
        if status in {'failed', 'retry'}:
            store.enqueue_job(mid)
            with store.connect() as conn:
                conn.execute("UPDATE media_items SET thumbnail_status=?,last_error='old error' WHERE id=?", (status, mid))
                conn.execute("UPDATE thumbnail_jobs SET status='paused',attempt_count=4,next_attempt_at='2099-01-01T00:00:00+00:00' WHERE media_id=?", (mid,))
                conn.commit()
        if live:
            records.append({'type': 'url', 'name': name, 'url': url})
        return mid
    ids = {
        'ready': bookmark('ready', provider='metadata', status='ready'),
        'failed': bookmark('failed', provider='site-tile', status='failed'),
        'retry': bookmark('retry', provider='metadata', status='retry'),
        'missing': bookmark('missing'),
        'orphan': bookmark('orphan', status='failed', live=False),
    }
    records += [{'type': 'url', 'name': 'new', 'url': 'https://example.com/new'},
                {'type': 'url', 'name': 'duplicate', 'url': 'https://example.com/new#section'},
                {'type': 'url', 'name': 'settings', 'url': 'chrome://settings/'}]
    bookmark_path = tmp_path/'Bookmarks'
    bookmark_path.write_text(json.dumps({'roots': {'bookmark_bar': {'type': 'folder', 'name': 'Saved', 'children': records}}}))
    monkeypatch.setattr(chrome_bookmarks, 'CHROME_BOOKMARK_PATH', str(bookmark_path))
    monkeypatch.setattr(chrome_routes, 'get_thumbnail_store', lambda: store)
    monkeypatch.setattr(common, 'compute_feature_flags', lambda: {'chrome': True, 'youtube': True, 'eagle': False, 'db': False})
    app = Flask(__name__, template_folder=str(Path(__file__).parents[1]/'templates'))
    app.config['TESTING'] = True
    routes.register_routes(app)
    return app.test_client(), store, ids


def test_all_retry_includes_unregistered_deduplicates_and_excludes_removed_bookmarks(retry_app, monkeypatch):
    client, store, ids = retry_app
    def no_network(*args, **kwargs):
        raise AssertionError('The retry request must only queue, not fetch websites')
    monkeypatch.setattr('requests.sessions.Session.get', no_network)
    old_image = store.get_thumbnail_path(ids['retry'])
    response = client.post('/api/bookmark-thumbnails/retry', json={'scope': 'all'})
    assert response.status_code == 200
    payload = response.get_json()
    selected = {item['id'] for item in payload['items']}
    assert len(selected) == 4
    assert selected >= {ids[name] for name in ('failed', 'retry', 'missing')}
    assert not selected & {ids['ready'], ids['orphan']}
    assert set(payload['queued']) == selected
    assert all(item['status'] == 'queued' for item in payload['items'])
    assert store.get_thumbnail_path(ids['retry']) == old_image
    assert Path(old_image).read_bytes() == b'cached image'
    with store.connect() as conn:
        jobs = conn.execute("SELECT attempt_count,next_attempt_at FROM thumbnail_jobs WHERE status='queued'").fetchall()
    assert len(jobs) == 4
    assert all(row['attempt_count'] == 0 and not row['next_attempt_at'].startswith('2099') for row in jobs)


def test_page_retry_is_scoped_and_skips_ready_but_single_force_refreshes_it(retry_app):
    client, store, ids = retry_app
    page = client.post('/api/bookmark-thumbnails/retry', json={'scope': 'page', 'ids': [ids['ready'], ids['failed'], ids['failed']]})
    assert page.status_code == 200
    assert page.get_json()['queued'] == [ids['failed']]
    forced = client.post('/api/bookmark-thumbnails/enqueue', json={'ids': [ids['ready']], 'force': True})
    assert forced.status_code == 200
    assert forced.get_json()['queued'] == [ids['ready']]
    assert store.get_status(ids['ready'])['provider'] == 'metadata'


def test_force_retry_does_not_steal_active_claim_or_reset_queued_job(retry_app):
    client, store, ids = retry_app
    mid = ids['missing']
    store.enqueue_job(mid)
    claim = store.claim_jobs('browser-test', 1, media_ids=[mid], lease_seconds=600)[0]
    for _ in range(2):
        response = client.post('/api/bookmark-thumbnails/enqueue', json={'ids': [mid], 'force': True})
        assert response.get_json()['queued'] == []
        assert response.get_json()['items'][0]['status'] == 'fetching'
    with store.connect() as conn:
        row = conn.execute('SELECT status,lease_owner FROM thumbnail_jobs WHERE media_id=?', (mid,)).fetchone()
        assert tuple(row) == ('processing', 'browser-test')
        conn.execute('UPDATE thumbnail_jobs SET lease_expires_at=? WHERE media_id=?',
            ((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(), mid))
        conn.commit()
    assert client.post('/api/bookmark-thumbnails/enqueue', json={'ids': [mid], 'force': True}).get_json()['queued'] == [mid]
    assert client.post('/api/bookmark-thumbnails/enqueue', json={'ids': [mid], 'force': True}).get_json()['queued'] == []
    assert claim['media_id'] == mid


def test_status_reports_retry_error_and_cache_version_changes_when_image_changes(retry_app):
    client, store, ids = retry_app
    mid = ids['retry']
    first = client.get('/api/bookmark-thumbnails/status', query_string={'ids': mid}).get_json()['items'][0]
    assert first['status'] == 'retry' and first['error'] == 'old error'
    assert first['image_version'] and first['title'] == 'retry'
    path = store.get_thumbnail_path(mid)
    stamp = os.stat(path).st_mtime_ns
    os.utime(path, ns=(stamp + 1000000, stamp + 1000000))
    second = store.get_status(mid)
    assert second['image_version'] != first['image_version']
    assert second['thumbnail_url'] == first['thumbnail_url']


def test_page_retry_handles_more_than_one_status_page(retry_app):
    client, store, _ = retry_app
    ids = [store.register_bookmark(f'https://example.net/{i}', str(i), enqueue_missing=False).media_id for i in range(205)]
    result = client.post('/api/bookmark-thumbnails/retry', json={'scope': 'page', 'ids': ids})
    assert result.status_code == 200
    assert len(result.get_json()['items']) == 205
    assert len(result.get_json()['queued']) == 205


@pytest.mark.parametrize('payload', [{'scope': 'other'}, {'scope': 'all', 'ids': ['a']}, {'scope': 'page', 'ids': 'bad'}])
def test_retry_rejects_invalid_scope_and_payload(retry_app, payload):
    client, _, _ = retry_app
    assert client.post('/api/bookmark-thumbnails/retry', json=payload).status_code == 400
