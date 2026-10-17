"""Queue explicit thumbnail backfills through the normal worker and pipeline."""
from __future__ import annotations

from urllib.parse import urlsplit

from .store import PRIORITY_MISSING, get_thumbnail_store
from .worker import ThumbnailWorker


def run_thumbnail_batch(*, store=None, media_ids=None, domain=None, limit=None,
                        force=False, retry_failed=False, max_workers=4):
    store = store or get_thumbnail_store()
    requested = set(media_ids) if media_ids is not None else None
    with store.connect() as connection:
        rows = connection.execute(
            "SELECT id,canonical_url,original_url FROM media_items WHERE source='bookmark' ORDER BY updated_at DESC"
        ).fetchall()
    selected, queued = [], 0
    normalized = (domain or "").lower().strip().lstrip(".")
    for row in rows:
        media_id = str(row["id"])
        if requested is not None and media_id not in requested:
            continue
        parsed = urlsplit(row["canonical_url"] or row["original_url"] or "")
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or not host:
            continue
        if normalized and host != normalized and not host.endswith("." + normalized):
            continue
        status = store.get_status(media_id)
        usable = status.get("thumbnail_url") and status.get("provider") != "site-tile"
        if usable and not force:
            continue
        selected.append(media_id)
        queued += store.enqueue_job(media_id, priority=PRIORITY_MISSING, force=force or retry_failed)
        if limit is not None and len(selected) >= limit:
            break
    processed = ThumbnailWorker(store=store, domain_filter=normalized, media_ids=selected,
                                max_workers=max_workers).run_until_idle(max_jobs=limit)
    return {"considered": len(selected), "queued": queued, "processed": processed,
            "force": force, "retry_failed": retry_failed, "domain": normalized or None,
            "items": [store.get_status(media_id) for media_id in selected]}
