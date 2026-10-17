"""Authoritative, profile-scoped Chrome projection and post-commit cache GC.

Only a validated complete snapshot can remove sources. Content survives while
another bookmark or independent origin references it. URL collisions are held;
this module never merges or splits existing content rows.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import text

from .identity import origin_key, reconcile_bound_bookmarks, stable_id, write_origins
from src.flowinone.resource_library.canonical import classify_resource, normalize_resource_url, normalize_tag
from src.flowinone.resource_library.models import Resource, utc_now_text


def mirror_enabled(conn, source_path: str | None = None) -> bool:
    if not conn.execute(text("SELECT 1 FROM pragma_table_info('bookmark_sources') WHERE name='sync_mode'")).scalar():
        return False
    sql = "SELECT 1 FROM bookmark_sources WHERE sync_mode='mirror'"
    params = {}
    if source_path:
        sql += " AND bookmark_path=:path"
        params['path'] = str(Path(source_path).expanduser().resolve())
    return bool(conn.execute(text(sql + " LIMIT 1"), params).scalar())


def _rows(conn, sql, params=None):
    return [dict(r) for r in conn.execute(text(sql), params or {}).mappings()]


def _canonical(url):
    try:
        return normalize_resource_url(url)
    except ValueError:
        return None


def _drop_binding(conn, binding):
    key = origin_key(binding['id'])
    conn.execute(text("DELETE FROM catalog_origins WHERE source_kind='bookmarks' AND source_key=:key"), {'key':key})
    conn.execute(text("DELETE FROM resource_origins WHERE source='chrome_json' AND source_key=:key"), {'key':key})
    conn.execute(text("DELETE FROM bookmark_url_history WHERE binding_id=:id"), binding)
    conn.execute(text("DELETE FROM bookmark_bindings WHERE id=:id"), binding)


def _remove_unreferenced(conn, catalog_ids, resource_ids):
    """Resources projections aren't independent origins of their own import."""
    deleted_resources, deleted_catalog = [], []
    for rid in sorted(resource_ids):
        if conn.execute(text("SELECT 1 FROM bookmark_bindings WHERE resource_id=:id LIMIT 1"), {'id':rid}).scalar():
            continue
        if conn.execute(text("SELECT 1 FROM resource_origins WHERE resource_id=:id LIMIT 1"), {'id':rid}).scalar():
            continue
        projections = _rows(conn, "SELECT catalog_item_id FROM catalog_origins WHERE source_kind='resources' AND source_key=:id", {'id':rid})
        independent = False
        for p in projections:
            if conn.execute(text("""SELECT 1 FROM catalog_origins WHERE catalog_item_id=:cid AND stale=0
                AND NOT(source_kind='resources' AND source_key=:rid) LIMIT 1"""), {'cid':p['catalog_item_id'],'rid':rid}).scalar():
                independent = True
        if independent:
            continue
        conn.execute(text("DELETE FROM catalog_origins WHERE source_kind='resources' AND source_key=:id"), {'id':rid})
        conn.execute(text("DELETE FROM resource_fts WHERE resource_id=:id"), {'id':rid})
        conn.execute(text("DELETE FROM resources WHERE id=:id"), {'id':rid})
        deleted_resources.append(rid)
        catalog_ids.update(p['catalog_item_id'] for p in projections)
    for cid in sorted(catalog_ids):
        if conn.execute(text("SELECT 1 FROM bookmark_bindings WHERE catalog_item_id=:id LIMIT 1"), {'id':cid}).scalar():
            continue
        if conn.execute(text("SELECT 1 FROM catalog_origins WHERE catalog_item_id=:id AND stale=0 LIMIT 1"), {'id':cid}).scalar():
            continue
        conn.execute(text("DELETE FROM catalog_fts WHERE catalog_item_id=:id"), {'id':cid})
        conn.execute(text("DELETE FROM catalog_items WHERE id=:id"), {'id':cid})
        deleted_catalog.append(cid)
    return deleted_catalog, deleted_resources


def _create_pair(conn, record, canonical, now):
    hashed = hashlib.sha256(canonical.encode()).hexdigest()
    rid = conn.execute(text("SELECT id FROM resources WHERE url_hash=:hash"), {'hash':hashed}).scalar()
    if not rid:
        rid = uuid.uuid4().hex
        kind, platform = classify_resource(canonical)
        conn.execute(Resource.__table__.insert().values(id=rid,original_url=record['url'],canonical_url=canonical,
            url_hash=hashed,title=record['title'],source_type=kind,source_platform=platform,
            domain=urlsplit(canonical).hostname,captured_at=record.get('captured_at') or now))
        conn.execute(text("INSERT INTO resource_fts(resource_id,title) VALUES(:id,:title)"), {'id':rid,'title':record['title']})
    cid = conn.execute(text("SELECT id FROM catalog_items WHERE identity_key=:key"), {'key':'url:'+hashed}).scalar()
    if not cid:
        cid = conn.execute(text("SELECT catalog_item_id FROM catalog_origins WHERE source_kind='resources' AND source_key=:id"), {'id':rid}).scalar()
    if not cid:
        cid = uuid.uuid4().hex
        kind, _ = classify_resource(canonical)
        conn.execute(text("""INSERT INTO catalog_items(id,identity_key,item_type,title,original_url,primary_detail_uri,indexed_at)
            VALUES(:id,:key,:kind,:title,:url,:url,:now)"""),
            {'id':cid,'key':'url:'+hashed,'kind':kind,'title':record['title'],'url':canonical,'now':now})
    conn.execute(text("""INSERT INTO catalog_origins(id,catalog_item_id,source_kind,source_key,detail_uri,original_url,last_seen_at)
        VALUES(:id,:cid,'resources',:rid,:detail,:url,:now)
        ON CONFLICT(source_kind,source_key) DO NOTHING"""),
        {'id':stable_id(rid+':catalog-resource'),'cid':cid,'rid':rid,'detail':f'/resources/{rid}/','url':canonical,'now':now})
    conn.execute(text("UPDATE catalog_items SET is_deleted=0,availability='available' WHERE id=:id AND is_deleted=1"), {'id':cid})
    return cid, rid


def _refresh_titles_tags(conn, bindings, records, now):
    by_guid = {r['guid']:r for r in records}
    grouped = defaultdict(list)
    for b in bindings:
        grouped[(b['catalog_item_id'],b['resource_id'])].append(by_guid[b['guid']])
    for (cid,rid), nodes in grouped.items():
        independent = conn.execute(text("""SELECT 1 FROM catalog_origins WHERE catalog_item_id=:id AND stale=0
            AND source_kind NOT IN ('bookmarks','resources') LIMIT 1"""), {'id':cid}).scalar()
        if not independent:
            conn.execute(text("UPDATE catalog_items SET title=:title,indexed_at=:now WHERE id=:id AND title IS NOT :title"), {'title':nodes[0]['title'],'now':now,'id':cid})
            conn.execute(text("UPDATE resources SET title=:title WHERE id=:id AND title IS NOT :title"), {'title':nodes[0]['title'],'id':rid})
            conn.execute(text("UPDATE resource_fts SET title=:title WHERE resource_id=:id AND title IS NOT :title"), {'title':nodes[0]['title'],'id':rid})
        # Folder tags reflect all active placements, including other sources.
        folders = [r['folder_path'] for r in _rows(conn, "SELECT folder_path FROM bookmark_bindings WHERE catalog_item_id=:id", {'id':cid})]
        conn.execute(text("DELETE FROM catalog_item_tags WHERE catalog_item_id=:id AND source='folder'"), {'id':cid})
        for label in sorted({p.strip() for folder in folders for p in folder.split(' / ') if p.strip()}):
            from src.flowinone.catalog.query import _normalize_tag
            normalized = _normalize_tag(label)
            tid = stable_id('catalog-folder:'+normalized)
            conn.execute(text("INSERT INTO catalog_tags(id,name,normalized_name,created_at) VALUES(:id,:name,:norm,:now) ON CONFLICT(normalized_name) DO NOTHING"),
                         {'id':tid,'name':label[:160],'norm':normalized,'now':now})
            tid = conn.execute(text("SELECT id FROM catalog_tags WHERE normalized_name=:name"), {'name':normalized}).scalar()
            conn.execute(text("INSERT OR IGNORE INTO catalog_item_tags(catalog_item_id,tag_id,source,created_at) VALUES(:cid,:tid,'folder',:now)"), {'cid':cid,'tid':tid,'now':now})
        conn.execute(text("DELETE FROM resource_tags WHERE resource_id=:id AND source='chrome_folder'"), {'id':rid})
        for leaf in sorted({folder.split(' / ')[-1].strip() for folder in folders if folder.strip()}):
            normalized = normalize_tag(leaf)
            if not normalized:
                continue
            tid = stable_id('resource-folder:'+normalized)
            conn.execute(text("INSERT INTO tags(id,name,normalized_name,created_at) VALUES(:id,:name,:norm,:now) ON CONFLICT(normalized_name) DO NOTHING"),
                         {'id':tid,'name':leaf[:120],'norm':normalized,'now':now})
            tid = conn.execute(text("SELECT id FROM tags WHERE normalized_name=:name"), {'name':normalized}).scalar()
            conn.execute(text("INSERT OR IGNORE INTO resource_tags(resource_id,tag_id,source,created_at) VALUES(:rid,:tid,'chrome_folder',:now)"), {'rid':rid,'tid':tid,'now':now})
        item = conn.execute(text("SELECT title,description FROM catalog_items WHERE id=:id"), {'id':cid}).mappings().one()
        names = conn.execute(text("SELECT t.name FROM catalog_item_tags it JOIN catalog_tags t ON t.id=it.tag_id WHERE it.catalog_item_id=:id"), {'id':cid}).scalars().all()
        old_fts = conn.execute(text("SELECT title,description,tags,extracted_text FROM catalog_fts WHERE catalog_item_id=:id"), {'id':cid}).mappings().first()
        if old_fts and (old_fts['title'],old_fts['description'] or '',old_fts['tags'] or '') == (item['title'],item['description'] or '', ' '.join(names)):
            continue
        extracted = old_fts['extracted_text'] or '' if old_fts else ''
        conn.execute(text("DELETE FROM catalog_fts WHERE catalog_item_id=:id"), {'id':cid})
        conn.execute(text("INSERT INTO catalog_fts(catalog_item_id,title,description,tags,extracted_text) VALUES(:id,:title,:desc,:tags,:extracted)"),
                     {'id':cid,'title':item['title'],'desc':item['description'] or '', 'tags':' '.join(names),'extracted':extracted})


def reconcile_mirror(conn, records, source):
    from .backfill import read_bookmarks
    complete, sha = read_bookmarks(Path(source['bookmark_path']))
    def signature(items):
        return Counter((r.get('guid'),r.get('chrome_id'),r['url'],r['title'],r['folder_path'],str(r.get('date_added') or '')) for r in items)
    if signature(complete) != signature(records):
        raise ValueError('Chrome mirror needs the unchanged, complete source snapshot')
    for r in records:
        try:
            valid = str(uuid.UUID(r.get('guid',''))) == r['guid'].lower()
        except (ValueError,AttributeError):
            valid = False
        if not valid or not r.get('chrome_id'):
            raise ValueError('Chrome mirror needs valid GUID/id identities')
    if len({r['guid'] for r in records}) != len(records) or len({r['chrome_id'] for r in records}) != len(records):
        raise ValueError('Duplicate Chrome GUID/id; no deletions applied')
    now = utc_now_text()
    previous = _rows(conn, "SELECT * FROM bookmark_bindings WHERE source_id=:id", source)
    by_guid = {r['guid']:r for r in records}
    candidate_c = {b['catalog_item_id'] for b in previous}
    candidate_r = {b['resource_id'] for b in previous}
    adopting = not source.get('last_synced_at')
    legacy = _rows(conn, "SELECT * FROM catalog_origins WHERE source_kind='bookmarks' AND source_key NOT LIKE 'chrome-bookmark:%'") if adopting else []
    candidate_c.update(o['catalog_item_id'] for o in legacy)
    for o in legacy:
        candidate_r.update(conn.execute(text("SELECT source_key FROM catalog_origins WHERE catalog_item_id=:id AND source_kind='resources'"), {'id':o['catalog_item_id']}).scalars())
        conn.execute(text("DELETE FROM catalog_origins WHERE id=:id"), o)
    # Legacy chrome_json placements were superseded by GUID origins. Manual,
    # Netscape and all other independent import sources remain intact.
    if adopting:
        for rid in candidate_r:
            conn.execute(text("DELETE FROM resource_origins WHERE resource_id=:id AND source='chrome_json' AND COALESCE(source_key,'') NOT LIKE 'chrome-bookmark:%'"), {'id':rid})
    removed = []
    for b in previous:
        r = by_guid.get(b['guid'])
        if r is None or _canonical(r['url']) is None:
            _drop_binding(conn,b)
            removed.append(b['guid'])
    bound = {b['guid']:b for b in _rows(conn,"SELECT * FROM bookmark_bindings WHERE source_id=:id",source)}
    created = 0
    for r in records:
        canonical = _canonical(r['url'])
        if canonical is None or r['guid'] in bound:
            continue
        cid, rid = _create_pair(conn,r,canonical,now)
        b = dict(id=stable_id(source['id']+':guid:'+r['guid']),source_id=source['id'],guid=r['guid'],chrome_id=r['chrome_id'],
                 date_added=str(r.get('date_added') or ''),catalog_item_id=cid,resource_id=rid,applied_url=r['url'],canonical_url=canonical,
                 observed_url=r['url'],folder_path=r['folder_path'],title=r['title'],last_seen_at=now)
        conn.execute(text("""INSERT INTO bookmark_bindings(id,source_id,guid,chrome_id,date_added,catalog_item_id,resource_id,
            applied_url,canonical_url,observed_url,folder_path,title,status,last_seen_at)
            VALUES(:id,:source_id,:guid,:chrome_id,:date_added,:catalog_item_id,:resource_id,:applied_url,:canonical_url,
            :observed_url,:folder_path,:title,'matched',:last_seen_at)"""), b)
        write_origins(conn,b,now=now)
        bound[r['guid']] = b
        created += 1
    deleted_c, deleted_r = _remove_unreferenced(conn,candidate_c,candidate_r)
    reconciled = reconcile_bound_bookmarks(conn,records,source['bookmark_path'],_mirror_dispatch=False)
    bindings = _rows(conn,"SELECT * FROM bookmark_bindings WHERE source_id=:id",source)
    _refresh_titles_tags(conn,bindings,records,now)
    conn.execute(text("DELETE FROM chrome_bookmark_nodes WHERE source_id=:id"), source)
    by_guid_binding = {b['guid']:b for b in bindings}
    for r in records:
        b = by_guid_binding.get(r['guid'])
        conn.execute(text("""INSERT INTO chrome_bookmark_nodes(source_id,guid,chrome_id,date_added,url,title,folder_path,
            folder_guids_json,catalog_item_id,resource_id,unsupported_reason,observed_at)
            VALUES(:source,:guid,:chrome_id,:date,:url,:title,:folder,:guids,:cid,:rid,:unsupported,:now)"""),
            {'source':source['id'],'guid':r['guid'],'chrome_id':r['chrome_id'],'date':str(r.get('date_added') or ''),
             'url':r['url'],'title':r['title'],'folder':r['folder_path'],'guids':json.dumps(r.get('folder_guids') or []),
             'cid':b['catalog_item_id'] if b else None,'rid':b['resource_id'] if b else None,
             'unsupported':None if b else 'not_public_http_scheme','now':now})
        if b:
            conn.execute(text("UPDATE catalog_origins SET detail_uri=:url,original_url=:url WHERE source_kind='bookmarks' AND source_key=:key"), {'url':r['url'],'key':origin_key(b['id'])})
    _, final_sha = read_bookmarks(Path(source['bookmark_path']))
    if final_sha != sha:
        raise ValueError('Chrome snapshot changed during synchronization; retry the complete source')
    conn.execute(text("UPDATE bookmark_sources SET last_snapshot_sha256=:sha,last_synced_at=:now WHERE id=:id"), {'id':source['id'],'sha':sha,'now':now})
    return {**reconciled,'handled':set(range(len(records))),'nodes':len(records),'new_bindings':created,
            'deleted_bindings':len(removed),'deleted_catalog':len(deleted_c),'deleted_resources':len(deleted_r)}


def maintain_mirror_cache(database, store=None, *, deleted_content=False):
    """Run after the library transaction commits; safe to repeat after a crash."""
    with database.engine.connect() as conn:
        if not mirror_enabled(conn):
            return {}
        nodes = _rows(conn,"SELECT n.* FROM chrome_bookmark_nodes n JOIN bookmark_sources s ON s.id=n.source_id WHERE s.sync_mode='mirror'")
    if store is None:
        from src.file_handler.thumbnails.store import get_thumbnail_store
        store = get_thumbnail_store()
    lookups = {}
    old_by_catalog = {}
    with database.engine.connect() as conn:
        old_by_catalog = dict(conn.execute(text("SELECT id,thumbnail_ref FROM catalog_items")).all())
    for n in nodes:
        if n['unsupported_reason']:
            continue
        lookup = store.register_bookmark(n['url'],n['title'],{'folder_path':n['folder_path']},only_if_new=True)
        previous_ref = old_by_catalog.get(n['catalog_item_id']) or ''
        match = re.fullmatch(r'/api/bookmark-thumbnails/([0-9a-f]{40})/image',previous_ref)
        if match:
            store.reuse_thumbnail(match[1],lookup.media_id)
        lookups[n['guid']] = lookup
    with database.write_transaction() as conn:
        for n in nodes:
            lookup = lookups.get(n['guid'])
            if not lookup:
                continue
            route = store.thumbnail_route(lookup.media_id)
            conn.execute(text("UPDATE catalog_items SET thumbnail_ref=:route WHERE id=:id"), {'route':route,'id':n['catalog_item_id']})
            conn.execute(text("UPDATE resources SET thumbnail_media_id=:media WHERE id=:id"), {'media':lookup.media_id,'id':n['resource_id']})
            key = origin_key(stable_id(n['source_id']+':guid:'+n['guid']))
            raw = conn.execute(text("SELECT metadata_json FROM catalog_origins WHERE source_kind='bookmarks' AND source_key=:key"), {'key':key}).scalar()
            if raw is not None:
                metadata = json.loads(raw or '{}');metadata['thumbnail_ref'] = route
                conn.execute(text("UPDATE catalog_origins SET metadata_json=:meta WHERE source_kind='bookmarks' AND source_key=:key"), {'meta':json.dumps(metadata,ensure_ascii=False),'key':key})
        # Re-read references after remapping, so obsolete URL keys aren't kept.
        keep = set(conn.execute(text("SELECT thumbnail_media_id FROM resources WHERE thumbnail_media_id IS NOT NULL")).scalars())
        refs = list(conn.execute(text("SELECT thumbnail_ref FROM catalog_items WHERE thumbnail_ref IS NOT NULL")).scalars())
        refs += [json.loads(o['metadata_json'] or '{}').get('thumbnail_ref','') for o in _rows(conn,"SELECT metadata_json FROM catalog_origins WHERE stale=0")]
        keep_paths = set()
        for r in _rows(conn,"SELECT thumbnail_path,favicon_path,screenshot_path FROM resources"):
            keep_paths.update(v for v in r.values() if v)
    for ref in refs:
        m = re.fullmatch(r'/api/bookmark-thumbnails/([0-9a-f]{40})/image',ref or '')
        if m:
            keep.add(m[1])
    keep.update(l.media_id for l in lookups.values())
    result = store.prune_bookmarks(keep,keep_paths=keep_paths)
    result.update(prune_resource_files(database))
    result['storage_reclaim'] = reclaim_mirror_storage(database.path,Path(store.db_path),optimize=deleted_content)
    return result


def configure_mirror_storage(database_path, cache_path):
    """One-time setup during explicit enable, with Web/Worker stopped."""
    for path in (database_path,cache_path):
        with sqlite3.connect(path) as conn:
            if conn.execute('PRAGMA auto_vacuum').fetchone()[0] != 2:
                conn.execute('PRAGMA auto_vacuum=INCREMENTAL')
                conn.execute('VACUUM')
                conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')


def reclaim_mirror_storage(database_path, cache_path, *, optimize=False):
    result = {}
    for path in (database_path,cache_path):
        try:
            with sqlite3.connect(path,timeout=1) as conn:
                if optimize and path==database_path:
                    for table in ('catalog_fts','resource_fts'):
                        conn.execute(f"INSERT INTO {table}({table}) VALUES('optimize')")
                    conn.commit()
                enabled = conn.execute('PRAGMA auto_vacuum').fetchone()[0] == 2
                if enabled:
                    free = conn.execute('PRAGMA freelist_count').fetchone()[0]
                    if free:
                        # sqlite3 on supported Python versions can step this
                        # pragma only once per execute. Drain it in one writer
                        # transaction rather than fsync once for every page.
                        conn.execute('BEGIN IMMEDIATE')
                        while free:
                            conn.execute('PRAGMA incremental_vacuum')
                            remaining = conn.execute('PRAGMA freelist_count').fetchone()[0]
                            if remaining >= free:
                                break
                            free = remaining
                        conn.commit()
                checkpoint = conn.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
                result[Path(path).name] = {'incremental_vacuum':enabled,'checkpoint_busy':bool(checkpoint[0])}
        except sqlite3.OperationalError as exc:
            if 'locked' not in str(exc).lower() and 'busy' not in str(exc).lower():
                raise
            result[Path(path).name] = {'deferred':True}
    return result


def prune_resource_files(database):
    """Reclaim managed files for deleted resources; never follow external paths."""
    from src.flowinone.resource_library.settings import get_resource_settings
    settings = get_resource_settings()
    configured = database.path == settings.database_path
    content = settings.content_dir if configured else database.path.parent/'content'
    assets = (settings.data_dir if configured else database.path.parent)/'assets'/'thumbnails'
    with database.engine.connect() as conn:
        alive = set(conn.execute(text('SELECT id FROM resources')).scalars())
        referenced = {str(Path(p).resolve()) for p in conn.execute(text('SELECT content_path FROM resource_contents')).scalars() if p}
        for row in _rows(conn,'SELECT thumbnail_path,favicon_path,screenshot_path FROM resources'):
            referenced.update(str(Path(p).resolve()) for p in row.values() if p)
    removed, freed = 0, 0
    for root in (content,assets):
        root = root.resolve()
        if not root.is_dir():
            continue
        for path in root.rglob('*'):
            resolved = path.resolve()
            if not path.is_file() or root not in resolved.parents or str(resolved) in referenced:
                continue
            owner = path.stem if root==assets.resolve() else path.parent.name
            if not re.fullmatch(r'[0-9a-f]{32}',owner) or owner in alive:
                continue
            size = path.stat().st_size
            path.unlink();removed += 1;freed += size
    return {'deleted_resource_files':removed,'freed_resource_file_bytes':freed}
