"""Opt-in public smoke tests for the built-in YouTube provider."""

import pytest

from src.file_handler.thumbnails.store import ThumbnailStore
from src.file_handler.thumbnails.worker import ThumbnailWorker


@pytest.mark.online
def test_youtube_watch_and_shorts(tmp_path):
    bookmarks = [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "YouTube watch smoke"),
        ("https://www.youtube.com/shorts/jNQXAC9IVRw", "YouTube Shorts smoke"),
    ]
    store = ThumbnailStore(str(tmp_path / "cache.db"), str(tmp_path / "thumbs"))
    media_ids = [store.register_bookmark(url, title).media_id for url, title in bookmarks]

    processed = ThumbnailWorker(store=store).run_until_idle(max_jobs=len(media_ids))

    assert processed == len(media_ids)
    for media_id in media_ids:
        status = store.get_status(media_id)
        assert status["status"] == "ready"
        assert status["provider"] == "youtube"
        assert status["thumbnail_url"]
