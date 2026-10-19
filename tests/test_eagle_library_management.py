"""Primary-library safety and cleanup use synthetic Eagle data only."""

import sqlite3

import pytest
from sqlalchemy import text

from src.eagle_api.models import EaglePage
from src.flowinone.app import create_app
from src.flowinone.catalog import eagle_library, eagle_sync
from src.flowinone.catalog.browse import CatalogService
from src.flowinone.catalog.eagle_library import EagleLibraryError, EagleLibraryService
from src.flowinone.catalog.query import CatalogQuery
from src.flowinone.catalog.sync import CatalogSyncService
from src.flowinone.catalog.watch import CatalogSourceWatcher
from src.flowinone.resource_library.database import get_resource_database


@pytest.fixture
def library(monkeypatch, tmp_path):
    db = get_resource_database(tmp_path / 'primary.db')
    source = {'identity': 'A', 'name': 'Main A', 'path': '/main.library', 'version': '1'}
    items = [{'id': 'keep', 'name': 'Keep', 'media_type': 'image', 'tags': ['art']},
             {'id': 'test', 'name': 'Test residue', 'media_type': 'image', 'tags': ['test-only']}]
    def current(**_):
        if source.get('offline'):
            raise RuntimeError('offline')
        return dict(source)
    def page(offset=0, limit=1000):
        return EaglePage(items=list(items[offset:offset+limit]), total=len(items), offset=offset, limit=limit)
    for module in (eagle_library, eagle_sync):
        monkeypatch.setattr(module, 'get_eagle_catalog_source', current)
        monkeypatch.setattr(module, 'get_eagle_catalog_page', page)
    service = EagleLibraryService(db)
    service.pin('A')
    sync = CatalogSyncService(db)
    assert sync.sync(['eagle'])['eagle']['count'] == 2
    catalog = CatalogService(db)
    with db.write_transaction() as conn:
        sync._upsert(conn, identity_key='local:one', source_kind='local', source_key='local-one',
                     title='Local one', item_type='image')
    return db, service, sync, catalog, source, items


def test_switch_hides_lists_search_facets_details_and_preserves_state(library):
    db, service, sync, catalog, source, items = library
    query = CatalogQuery.create(sources=['eagle'])
    original = catalog.list(query)
    item_id = next(row['id'] for row in original['items'] if row['title'] == 'Keep')
    catalog.record_event(item_id, 'favorite')
    source['identity'] = 'B'
    items[:] = [{'id': 'keep', 'name': 'Conflicting B', 'media_type': 'image'}]
    assert not catalog.list(query)['items']
    assert catalog.list(query)['total_estimate'] == 0
    assert not catalog.list(CatalogQuery.create(q='Keep'))['items']
    facets = catalog.facets(query)
    assert facets['sources'].get('eagle', 0) == 0
    assert not any(tag['name'] == 'test-only' for tag in facets['tags'])
    assert [row['title'] for row in catalog.list(CatalogQuery.create())['items']] == ['Local one']
    assert catalog.get_origin(item_id, 'eagle') is None
    assert not catalog.get_origins_for_items([item_id])
    with pytest.raises(LookupError):
        catalog.get(item_id)
    result = sync.sync(['eagle'])['eagle']
    assert result['status'] == 'failed'
    with db.engine.connect() as conn:
        assert conn.execute(text("SELECT title,is_deleted FROM catalog_items WHERE id=:id"), {'id': item_id}).one() == ('Keep', 0)
        assert conn.execute(text("SELECT COUNT(*) FROM catalog_origins WHERE source_kind='eagle' AND stale=0")).scalar() == 2
        assert conn.execute(text("SELECT status FROM catalog_sync_state WHERE source_kind='eagle'")).scalar() == 'complete'
    with pytest.raises(EagleLibraryError):
        service.preview_cleanup()
    with pytest.raises(EagleLibraryError):
        service.pin('B')
    source['identity'] = 'A'
    assert catalog.get(item_id)['favorite']
    assert catalog.list(query)['total_estimate'] == 2


def test_offline_hides_without_mutating(library):
    db, service, sync, catalog, source, items = library
    source['offline'] = True
    assert service.status()['state'] == 'unavailable'
    assert not catalog.list(CatalogQuery.create(sources=['eagle']))['items']
    with pytest.raises(RuntimeError):
        service.preview_cleanup()
    with db.engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM catalog_origins WHERE source_kind='eagle'")).scalar() == 2


def test_cleanup_backup_cascade_and_no_other_source_damage(library):
    db, service, sync, catalog, source, items = library
    test_id = next(row['id'] for row in catalog.list(CatalogQuery.create())['items'] if row['title'] == 'Test residue')
    catalog.record_event(test_id, 'favorite')
    items.pop()
    assert sync.sync(['eagle'])['eagle']['deleted'] == 1
    preview = service.preview_cleanup()
    assert preview['count'] == 1
    with db.engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM catalog_items WHERE id=:id"), {'id': test_id}).scalar() == 1
    result = service.cleanup(preview['token'])
    assert result['deleted_items'] == result['deleted_origins'] == 1
    with sqlite3.connect(result['backup_path']) as backup:
        assert backup.execute('PRAGMA integrity_check').fetchone() == ('ok',)
        assert backup.execute('SELECT COUNT(*) FROM catalog_items WHERE id=?', (test_id,)).fetchone() == (1,)
    with db.engine.connect() as conn:
        for table, key in [('catalog_items', 'id'), ('catalog_origins', 'catalog_item_id'),
                           ('item_user_state', 'catalog_item_id'), ('catalog_events', 'catalog_item_id'),
                           ('catalog_fts', 'catalog_item_id')]:
            assert not conn.execute(text(f'SELECT 1 FROM {table} WHERE {key}=:id'), {'id': test_id}).first()
        assert not conn.execute(text('PRAGMA foreign_key_check')).all()
    assert {row['title'] for row in catalog.list(CatalogQuery.create())['items']} == {'Keep', 'Local one'}
    assert service.preview_cleanup()['count'] == 0
    assert service.cleanup(service.preview_cleanup()['token'])['backup_path'] is None


def test_cleanup_rejects_changed_preview_or_different_library(library):
    db, service, sync, catalog, source, items = library
    items.pop()
    preview = service.preview_cleanup()
    # A restored asset invalidates the approved deletion set.
    items.append({'id': 'test', 'name': 'Restored', 'media_type': 'image'})
    with pytest.raises(EagleLibraryError, match='重新預覽'):
        service.cleanup(preview['token'])
    source['identity'] = 'B'
    with pytest.raises(EagleLibraryError, match='不是主要庫'):
        service.cleanup(preview['token'])
    with db.engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM catalog_origins WHERE source_kind='eagle'")).scalar() == 2


def test_cleanup_preserves_shared_origins_and_their_user_state(library):
    db, service, sync, catalog, source, items = library
    with db.write_transaction() as conn:
        item_id = sync._upsert(conn, identity_key='eagle:test', source_kind='resources',
                              source_key='independent-resource', item_type='image', title='Independent',
                              tags=[('personal', 'user')])
    catalog.record_event(item_id, 'favorite')
    items.pop()
    result = service.cleanup(service.preview_cleanup()['token'])
    assert result['deleted_origins'] == 1
    assert result['deleted_items'] == 0
    assert catalog.get(item_id)['favorite']
    assert catalog.get(item_id)['tags'] == ['personal']
    with db.engine.connect() as conn:
        assert not conn.execute(text("SELECT 1 FROM catalog_tags WHERE name='test-only'")).first()
        assert conn.execute(text("SELECT tags FROM catalog_fts WHERE catalog_item_id=:id"), {'id': item_id}).scalar() == 'personal'


def test_incomplete_or_duplicate_snapshot_never_deletes(library, monkeypatch):
    db, service, sync, catalog, source, items = library
    monkeypatch.setattr(eagle_library, 'get_eagle_catalog_page', lambda **_: EaglePage(
        items=[], offset=0, limit=1000, total=2))
    with pytest.raises(EagleLibraryError):
        service.preview_cleanup()
    items.append(dict(items[0]))
    monkeypatch.setattr(eagle_library, 'get_eagle_catalog_page', lambda **_: EaglePage(
        items=items, offset=0, limit=1000, total=len(items)))
    with pytest.raises(EagleLibraryError):
        service.preview_cleanup()


def test_switch_during_page_cannot_write_foreign_records(library, monkeypatch):
    db, service, sync, catalog, source, items = library
    def page(**_):
        source['identity'] = 'B'
        return EaglePage(items=[{'id': 'foreign', 'name': 'B', 'media_type': 'image'}],
                         offset=0, limit=500, total=1)
    monkeypatch.setattr(eagle_sync, 'get_eagle_catalog_page', page)
    assert sync.sync(['eagle'])['eagle']['status'] == 'failed'
    with db.engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM catalog_origins WHERE source_kind='eagle' AND stale=0")).scalar() == 2
        assert not conn.execute(text("SELECT 1 FROM catalog_items WHERE identity_key='eagle:foreign'")).first()


def test_watcher_does_not_queue_foreign_library(library):
    db, service, sync, catalog, source, items = library
    watcher = CatalogSourceWatcher(db, signatures={'eagle': lambda: source['identity']}, debounce_seconds=0)
    watcher.set_enabled(True)
    watcher.process_once()
    source['identity'] = 'B'
    assert watcher.process_once()['sources']['eagle']['state'] == 'unavailable'
    with db.engine.connect() as conn:
        assert conn.execute(text('SELECT COUNT(*) FROM processing_jobs')).scalar() == 0
    source['identity'] = 'A'
    recovered = watcher.process_once()['sources']['eagle']
    assert recovered['state'] == 'watching'
    assert not recovered.get('error')


def test_management_routes_and_direct_source_protection(library, monkeypatch):
    db, service, sync, catalog, source, items = library
    app = create_app({'TESTING': True, 'FLOWINONE_RESOURCE_DB_PATH': str(db.path)})
    client = app.test_client()
    page = client.get('/library/')
    assert page.status_code == 200
    assert '預覽 Eagle 殘留資料' in page.get_data(as_text=True)
    assert client.get('/api/catalog/eagle/library').json['state'] == 'active'
    assert client.post('/api/catalog/eagle/cleanup', json={'token': 'invalid'}).status_code == 400
    items.pop()
    preview = client.post('/api/catalog/eagle/cleanup/preview', json={})
    assert preview.status_code == 200
    source['identity'] = 'B'
    assert client.post('/api/catalog/eagle/cleanup', json={'token': preview.json['token']}).status_code == 409
    assert client.post('/api/catalog/sync', json={'sources': ['eagle']}).status_code == 409
    assert client.get('/api/catalog/items?source=eagle').json['items'] == []
    assert client.get('/EAGLE_image/keep/').status_code == 409
    assert client.get('/api/EAGLE_stream/').status_code == 409
    source['identity'] = 'A'
    assert client.post('/api/catalog/eagle/cleanup', json={'token': preview.json['token']}).status_code == 200
