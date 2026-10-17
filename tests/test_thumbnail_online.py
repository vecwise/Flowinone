"""Opt-in smoke test for a user-configured public metadata page."""
import os

import pytest

from src.file_handler.thumbnails.store import ThumbnailStore
from src.file_handler.thumbnails.worker import ThumbnailWorker


@pytest.mark.online
def test_public_metadata_thumbnail(tmp_path):
    url = os.environ.get("FLOWINONE_THUMBNAIL_SMOKE_URL")
    if not url:
        pytest.skip("Set FLOWINONE_THUMBNAIL_SMOKE_URL to a public page with an explicit cover")
    store = ThumbnailStore(str(tmp_path / "cache.db"), str(tmp_path / "thumbs"))
    lookup = store.register_bookmark(url, "Public metadata smoke")
    assert ThumbnailWorker(store=store).run_until_idle(max_jobs=1) == 1
    status = store.get_status(lookup.media_id)
    assert status["status"] == "ready"
    assert status["provider"] == "metadata"
