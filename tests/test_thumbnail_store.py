import os
import sqlite3
from datetime import datetime, timedelta, timezone

from src.file_handler.thumbnails.store import ThumbnailStore


def _old_database(path, media_id, image_path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE media_items (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            original_url TEXT,
            title TEXT,
            media_type TEXT,
            sub_type TEXT,
            metadata TEXT,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE thumbnails (
            media_id TEXT PRIMARY KEY REFERENCES media_items(id) ON DELETE CASCADE,
            local_path TEXT NOT NULL,
            fetched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            source TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO media_items(id, source, original_url, title, media_type, sub_type) VALUES (?, 'bookmark', ?, 'Old', 'bookmark', 'legacy-provider')",
        (media_id, "https://example.com/legacy-video"),
    )
    conn.execute(
        "INSERT INTO thumbnails(media_id, local_path, source) VALUES (?, ?, 'legacy-provider')",
        (media_id, str(image_path)),
    )
    conn.commit()
    conn.close()


def test_migration_preserves_legacy_thumbnail(tmp_path):
    db_path = tmp_path / "cache.db"
    cache_dir = tmp_path / "thumbnails"
    cache_dir.mkdir()
    image_path = cache_dir / "legacy.jpg"
    image_path.write_bytes(b"legacy-thumbnail")
    media_id = "a" * 40
    _old_database(db_path, media_id, image_path)

    store = ThumbnailStore(str(db_path), str(cache_dir))

    assert store.get_thumbnail_path(media_id) == str(image_path)
    assert store.get_status(media_id)["status"] == "ready"
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM thumbnails").fetchone()[0] == 1
        assert {row[1] for row in conn.execute("PRAGMA table_info(media_items)")} >= {
            "canonical_url",
            "thumbnail_status",
            "last_error",
        }


def test_prune_preserves_shared_image_until_last_reference_and_cancels_jobs(tmp_path):
    store=ThumbnailStore(str(tmp_path/'gc.db'),str(tmp_path/'thumbs'))
    first=store.register_bookmark('https://example.com/one','one')
    second=store.register_bookmark('https://example.com/two','two')
    image=tmp_path/'thumbs'/'shared.webp';image.write_bytes(b'image')
    store.record_thumbnail(first.media_id,str(image),provider='metadata',source_url=None,width=1,height=1)
    assert store.reuse_thumbnail(first.media_id,second.media_id)
    assert store.prune_bookmarks({second.media_id})['deleted_media']==1
    assert image.exists()
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) FROM thumbnail_jobs WHERE media_id=?',(first.media_id,)).fetchone()[0]==0
    assert store.prune_bookmarks(set())['deleted_files']==1
    assert not image.exists()


def test_prune_never_deletes_external_files_and_late_fetch_cannot_resurrect(tmp_path):
    store=ThumbnailStore(str(tmp_path/'gc.db'),str(tmp_path/'thumbs'))
    lookup=store.register_bookmark('https://example.com/one','one')
    external=tmp_path/'external.webp';external.write_bytes(b'keep')
    store.record_thumbnail(lookup.media_id,str(external),provider='metadata',source_url=None,width=1,height=1)
    store.prune_bookmarks(set())
    assert external.exists()
    late=tmp_path/'thumbs'/'late.webp';late.write_bytes(b'late')
    store.record_thumbnail(lookup.media_id,str(late),provider='metadata',source_url=None,width=1,height=1)
    assert not late.exists()
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) FROM media_items').fetchone()[0]==0
        assert conn.execute('SELECT count(*) FROM thumbnails').fetchone()[0]==0


def test_prune_keeps_inflight_file_for_a_live_media_item(tmp_path):
    store=ThumbnailStore(str(tmp_path/'gc.db'),str(tmp_path/'thumbs'))
    lookup=store.register_bookmark('https://example.com/live','live')
    image=tmp_path/'thumbs'/(lookup.media_id+'.webp');image.write_bytes(b'in flight')
    assert store.prune_bookmarks({lookup.media_id})['deleted_files']==0
    assert image.exists()


def test_thumbnail_providers_only_include_existing_cached_files(tmp_path):
    store = ThumbnailStore(str(tmp_path / "cache.db"), str(tmp_path / "thumbnails"))
    tile = store.register_bookmark("https://tile.example/article", "Tile")
    image = store.register_bookmark("https://image.example/article", "Image")
    tile_path = tmp_path / "tile.webp"
    image_path = tmp_path / "image.webp"
    tile_path.write_bytes(b"tile")
    image_path.write_bytes(b"image")
    store.record_thumbnail(tile.media_id, str(tile_path), provider="site-tile", source_url=None, width=640, height=400)
    store.record_thumbnail(image.media_id, str(image_path), provider="metadata", source_url=None, width=640, height=400)

    assert store.get_thumbnail_providers([tile.media_id, image.media_id, tile.media_id]) == {
        tile.media_id: "site-tile",
        image.media_id: "metadata",
    }
    image_path.unlink()
    assert store.get_thumbnail_providers([tile.media_id, image.media_id]) == {tile.media_id: "site-tile"}


def test_queue_deduplicates_canonical_urls(tmp_path):
    store = ThumbnailStore(str(tmp_path / "cache.db"), str(tmp_path / "thumbs"))
    first = store.register_bookmark("https://example.com/article?utm_source=one", "First")
    second = store.register_bookmark("https://example.com/article#section", "Second")

    assert first.media_id == second.media_id
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM thumbnail_jobs").fetchone()[0] == 1


def test_retry_schedule_and_fourth_attempt_pause(tmp_path):
    store = ThumbnailStore(str(tmp_path / "cache.db"), str(tmp_path / "thumbs"))
    lookup = store.register_bookmark("https://example.com/article", "Example")

    for attempt in range(1, 5):
        with store.connect() as conn:
            conn.execute(
                "UPDATE thumbnail_jobs SET status='queued', next_attempt_at=? WHERE media_id=?",
                ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), lookup.media_id),
            )
            conn.commit()
        claimed = store.claim_jobs("test-worker", 1)
        assert len(claimed) == 1
        status = store.mark_job_failure(lookup.media_id, "offline")
        assert status == ("paused" if attempt == 4 else "retry")

    with store.connect() as conn:
        row = conn.execute(
            "SELECT status, attempt_count FROM thumbnail_jobs WHERE media_id=?",
            (lookup.media_id,),
        ).fetchone()
    assert row["status"] == "paused"
    assert row["attempt_count"] == 4


def test_expired_processing_lease_is_reclaimed(tmp_path):
    store = ThumbnailStore(str(tmp_path / "cache.db"), str(tmp_path / "thumbs"))
    lookup = store.register_bookmark("https://example.com/article", "Example")
    first_claim = store.claim_jobs("111111:old", 1, lease_seconds=1)
    assert first_claim
    with store.connect() as conn:
        conn.execute(
            "UPDATE thumbnail_jobs SET lease_expires_at=? WHERE media_id=?",
            ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), lookup.media_id),
        )
        conn.commit()

    second_claim = store.claim_jobs("222222:new", 1)
    assert [job["media_id"] for job in second_claim] == [lookup.media_id]


def test_claim_jobs_honors_domain_filter(tmp_path):
    store = ThumbnailStore(str(tmp_path / "cache.db"), str(tmp_path / "thumbs"))
    wanted = store.register_bookmark("https://media.example.com/article", "Wanted")
    store.register_bookmark("https://other.test/article", "Other")

    claimed = store.claim_jobs("test-worker", 4, domain_filter="example.com")
    assert [job["media_id"] for job in claimed] == [wanted.media_id]


def test_runtime_lease_can_be_released_only_by_its_owner(tmp_path):
    store = ThumbnailStore(str(tmp_path / "cache.db"), str(tmp_path / "thumbs"))
    first = f"{os.getpid()}:first"
    second = f"{os.getpid()}:second"

    assert store.acquire_runtime_lease("thumbnail-worker", first)
    assert not store.acquire_runtime_lease("thumbnail-worker", second)
    store.release_runtime_lease("thumbnail-worker", second)
    assert not store.acquire_runtime_lease("thumbnail-worker", second)
    store.release_runtime_lease("thumbnail-worker", first)
    assert store.acquire_runtime_lease("thumbnail-worker", second)
