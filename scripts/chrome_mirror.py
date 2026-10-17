"""Enable/sync the configured Chrome profile and report its local mirror.

Enabling is explicit and destructive: Chrome deletions remove unreferenced
content and cache. This command creates neither backups nor a recycle bin.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from src.flowinone.bookmarks.backfill import read_bookmarks
from src.flowinone.bookmarks.identity import source_details
from src.flowinone.bookmarks.mirror import configure_mirror_storage, mirror_enabled
from src.flowinone.catalog.sync import CatalogSyncService
from src.flowinone.config import CHROME_BOOKMARK_PATH
from src.flowinone.resource_library.database import ResourceDatabase, upgrade_database
from src.flowinone.resource_library.models import utc_now_text
from src.flowinone.resource_library.settings import get_resource_settings
from src.file_handler.thumbnails.store import ThumbnailStore, get_thumbnail_store


def status(database, bookmarks, store=None):
    records, sha = read_bookmarks(bookmarks)
    source = source_details(bookmarks)
    with database.engine.connect() as conn:
        nodes = [dict(x) for x in conn.execute(text('SELECT * FROM chrome_bookmark_nodes WHERE source_id=:id'),source).mappings()]
        bindings = [dict(x) for x in conn.execute(text('SELECT * FROM bookmark_bindings WHERE source_id=:id'),source).mappings()]
        keys = lambda rs:Counter((r['guid'],r['chrome_id'],r.get('url',r.get('observed_url')),r['title'],r['folder_path']) for r in rs)
        counts = {t:conn.exec_driver_sql('SELECT count(*) FROM '+t).scalar() for t in ('catalog_items','resources','bookmark_bindings','chrome_bookmark_nodes','catalog_origins','resource_origins','catalog_item_tags','resource_tags','item_user_state','bookmark_url_history')}
        favorites = conn.execute(text('SELECT count(*) FROM item_user_state WHERE favorite=1')).scalar()
        origins = {x['source_key']:json.loads(x['metadata_json'] or '{}') for x in conn.execute(text("SELECT source_key,metadata_json FROM catalog_origins WHERE source_kind='bookmarks' AND stale=0")).mappings()}
        schema = {t:[dict(x) for x in conn.exec_driver_sql('PRAGMA table_info('+t+')').mappings()] for t in (
            'bookmark_sources','chrome_bookmark_nodes','bookmark_bindings','bookmark_url_history',
            'catalog_items','catalog_origins','item_user_state','catalog_tags','catalog_item_tags',
            'resources','resource_origins','tags','resource_tags','resource_contents','processing_jobs')}
        source_state = dict(conn.execute(text('SELECT * FROM bookmark_sources WHERE id=:id'),source).mappings().one())
        integrity = conn.exec_driver_sql('PRAGMA integrity_check').scalar()
        foreign_keys = [tuple(x) for x in conn.exec_driver_sql('PRAGMA foreign_key_check')]
    store = store or get_thumbnail_store()
    with store.connect() as conn:
        cached = {x['media_id']:dict(x) for x in conn.execute('SELECT * FROM thumbnails')}
        cache_counts = {t:conn.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('media_items','thumbnails','thumbnail_jobs')}
        cache_status = dict(conn.execute('SELECT thumbnail_status,count(*) FROM media_items GROUP BY 1').fetchall())
        cache_integrity = conn.execute('PRAGMA integrity_check').fetchone()[0]
        cache_foreign_keys = [tuple(x) for x in conn.execute('PRAGMA foreign_key_check')]
        schema.update({'cache.'+t:[dict(x) for x in conn.execute('PRAGMA table_info('+t+')')] for t in ('media_items','thumbnails','thumbnail_jobs')})
    by_guid = {b['guid']:b for b in bindings}
    coverage = Counter()
    providers = Counter()
    for n in nodes:
        if n['unsupported_reason']:
            coverage['unsupported'] += 1
            continue
        b = by_guid[n['guid']]
        route = origins.get('chrome-bookmark:'+b['id'],{}).get('thumbnail_ref','')
        match = re.fullmatch(r'/api/bookmark-thumbnails/([0-9a-f]{40})/image',route)
        image = cached.get(match[1]) if match else None
        if image and Path(image['local_path']).is_file():
            coverage['with_file'] += 1
            coverage['site_tile' if image['source']=='site-tile' else 'non_tile_image'] += 1
            providers[image['source']] += 1
        else:
            coverage['missing_file'] += 1
    hin = source_state['scope_folder_guid']
    return dict(captured_at=utc_now_text(),source=source_state,chrome_sha256=sha,chrome_nodes=len(records),db_nodes=len(nodes),
        exact_node_match=keys(records)==keys(nodes),http_nodes=sum(n['unsupported_reason'] is None for n in nodes),
        hin_nodes=sum(hin in (r.get('folder_guids') or []) for r in records),
        unique_chrome_urls=len({r['url'] for r in records}),unique_bookmark_catalog_rows=len({n['catalog_item_id'] for n in nodes if n['catalog_item_id']}),
        unique_bookmark_resource_rows=len({n['resource_id'] for n in nodes if n['resource_id']}),
        binding_status=dict(Counter(b['status'] for b in bindings)),conflicts=dict(Counter(b['conflict_reason'] for b in bindings if b['conflict_reason'])),
        db_counts=counts,favorite_rows=favorites,thumbnail_coverage=dict(coverage),thumbnail_providers=dict(providers),
        cache_counts=cache_counts,cache_status=cache_status,cache_file_bytes=sum(p.stat().st_size for p in Path(store.cache_dir).iterdir() if p.is_file()),
        integrity=integrity,foreign_key_errors=foreign_keys,cache_integrity=cache_integrity,cache_foreign_key_errors=cache_foreign_keys,schema=schema)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=('enable','sync','status'))
    parser.add_argument('--database',type=Path,default=get_resource_settings().database_path)
    parser.add_argument('--bookmarks',type=Path,default=Path(CHROME_BOOKMARK_PATH))
    parser.add_argument('--compact',action='store_true',help='VACUUM both DBs; stop Web/Worker first')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--cache-database',type=Path)
    parser.add_argument('--cache-dir',type=Path)
    args=parser.parse_args()
    if bool(args.cache_database)!=bool(args.cache_dir):
        raise ValueError('Use --cache-database and --cache-dir together')
    if args.database.resolve()!=get_resource_settings().database_path.resolve() and not args.cache_database:
        raise ValueError('A separate library needs explicit --cache-database and --cache-dir; do not prune the live project cache')
    # Validate before an enable can migrate or change source policy.
    records, sha = read_bookmarks(args.bookmarks)
    if args.operation=='enable':
        upgrade_database(args.database)
    database=ResourceDatabase(args.database,migrate=False)
    store=ThumbnailStore(str(args.cache_database),str(args.cache_dir)) if args.cache_database else get_thumbnail_store()
    try:
        if args.operation=='enable':
            configure_mirror_storage(args.database,Path(store.db_path))
        source=source_details(args.bookmarks)
        if args.operation=='enable':
            with database.write_transaction() as conn:
                if not conn.execute(text('SELECT 1 FROM bookmark_sources WHERE id=:id'),source).scalar():
                    conn.execute(text("""INSERT INTO bookmark_sources(id,profile_path,storage_scope,bookmark_path,
                        scope_folder_guid,scope_folder_path,baseline_sha256,created_at,sync_mode)
                        VALUES(:id,:profile_path,:storage_scope,:bookmark_path,'','Entire profile',:sha,:now,'mirror')"""),
                        {**source,'sha':sha,'now':utc_now_text()})
                else:
                    conn.execute(text("UPDATE bookmark_sources SET sync_mode='mirror' WHERE id=:id"),source)
        if args.operation in ('enable','sync'):
            with database.engine.connect() as conn:
                if not mirror_enabled(conn,str(args.bookmarks)):
                    raise ValueError('Enable this Chrome profile before using mirror sync')
            from src.file_handler import chrome_bookmarks
            chrome_bookmarks.CHROME_BOOKMARK_PATH=str(args.bookmarks.expanduser().resolve())
            result=CatalogSyncService(database,thumbnail_store=store).sync(('bookmarks',))
            if result['bookmarks']['status']!='complete':
                raise RuntimeError(result['bookmarks'].get('error') or str(result))
        else:
            result=None
        report=status(database,args.bookmarks,store=store)
        report['sync_result']=result
        if not report['exact_node_match'] or report['integrity']!='ok' or report['cache_integrity']!='ok' or report['foreign_key_errors'] or report['cache_foreign_key_errors']:
            raise RuntimeError('Mirror verification failed')
    finally:
        database.dispose()
    if args.compact:
        report['compaction']={}
        for path in (args.database,Path(store.db_path)):
            before=path.stat().st_size
            with sqlite3.connect(path) as conn:
                conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
                if path.resolve()==args.database.resolve():
                    for table in ('catalog_fts','resource_fts'):
                        conn.execute(f"INSERT INTO {table}({table}) VALUES('optimize')")
                    conn.commit()
                conn.execute('VACUUM')
                conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            report['compaction'][path.name]={'before_bytes':before,'after_bytes':path.stat().st_size}
    output=json.dumps(report,ensure_ascii=False,indent=2)+'\n'
    if args.output:
        if args.output.resolve() in {args.database.resolve(),args.bookmarks.resolve(),Path(store.db_path).resolve()}:
            raise ValueError('Output must not overwrite an input')
        args.output.write_text(output,encoding='utf-8')
    print(output)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
