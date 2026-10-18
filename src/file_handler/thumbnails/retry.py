"""Explicit UI retries, scoped to current bookmarks and the durable queue."""
from urllib.parse import urlsplit

from .store import PRIORITY_MISSING
from .urls import canonicalize_url


def resolve_bookmark_ids(store, records):
    """Reuse legacy/canonical IDs; register only never-seen HTTP(S) bookmarks."""
    resolved, missing = [], []
    with store.connect() as conn:
        for record in records:
            url = record['url']
            parsed = urlsplit(url)
            if parsed.scheme.lower() not in {'http', 'https'} or not parsed.hostname:
                continue
            media_id, is_new = store._select_media_id(conn, url, canonicalize_url(url))
            if is_new:
                missing.append(record)
            else:
                resolved.append(media_id)
    for record in missing:
        resolved.append(store.register_bookmark(record['url'], record.get('title') or record['url'],
            {'folder_path': record.get('folder_path') or ''}, enqueue_missing=False).media_id)
    return list(dict.fromkeys(resolved))


def retry_unsuccessful(store, media_ids):
    """Queue one immediate attempt, retaining good images and active claims."""
    ids = list(dict.fromkeys(media_ids))
    selected, queued = [], []
    with store.connect() as conn:
        bookmark_ids = {row[0] for row in conn.execute("SELECT id FROM media_items WHERE source='bookmark'")}
    for offset in range(0, len(ids), 200):
        for item in store.get_statuses(ids[offset:offset + 200]):
            if item['id'] not in bookmark_ids:
                continue
            usable = item.get('thumbnail_url') and item.get('provider') != 'site-tile'
            if usable and item['status'] not in {'missing', 'failed', 'retry', 'queued', 'fetching'}:
                continue
            selected.append(item['id'])
            if store.enqueue_job(item['id'], force=True, priority=PRIORITY_MISSING):
                queued.append(item['id'])
    items = []
    for offset in range(0, len(selected), 200):
        items.extend(store.get_statuses(selected[offset:offset + 200]))
    return {'queued': queued, 'items': items}
