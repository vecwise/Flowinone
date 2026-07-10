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
        "INSERT INTO media_items(id, source, original_url, title, media_type, sub_type) VALUES (?, 'bookmark', ?, 'Old', 'bookmark', 'youtube')",
        (media_id, "https://www.youtube.com/watch?v=abcdefghijk"),
    )
    conn.execute(
        "INSERT INTO thumbnails(media_id, local_path, source) VALUES (?, ?, 'youtube')",
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


def test_queue_deduplicates_canonical_youtube_urls(tmp_path):
    store = ThumbnailStore(str(tmp_path / "cache.db"), str(tmp_path / "thumbs"))
    first = store.register_bookmark("https://youtu.be/abcdefghijk?si=one", "First")
    second = store.register_bookmark("https://youtube.com/shorts/abcdefghijk", "Second")

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
