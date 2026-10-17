"""Chrome read failures must not stop queued thumbnail work or future scans."""

import json
import logging
import threading
from types import SimpleNamespace

import pytest

from src.file_handler import chrome_bookmarks
from src.file_handler.thumbnails import worker as worker_module
from src.file_handler.thumbnails.store import ThumbnailStore


@pytest.fixture
def chrome_worker(tmp_path, monkeypatch):
    path = tmp_path / "AccountBookmarks"
    path.write_text(json.dumps({"roots": {"bookmark_bar": {
        "type": "folder", "name": "Bookmarks bar", "children": [
            {"type": "url", "name": "Article", "url": "https://example.com/article"},
        ],
    }}}), encoding="utf-8")
    monkeypatch.setattr(chrome_bookmarks, "CHROME_BOOKMARK_PATH", str(path))
    monkeypatch.setattr(worker_module, "CHROME_BOOKMARK_PATH", str(path))
    store = ThumbnailStore(str(tmp_path / "cache.db"), str(tmp_path / "thumbs"))
    return worker_module.ThumbnailWorker(store=store, max_workers=1), path


@pytest.mark.parametrize("error", [
    PermissionError(1, "Operation not permitted"),
    FileNotFoundError(2, "No such file or directory"),
    json.JSONDecodeError("partial Chrome write", "", 0),
], ids=["permission-denied", "removed-after-stat", "incomplete-json"])
@pytest.mark.parametrize("previous", [None, "previous-successful-scan"])
def test_failed_scan_retries_same_file_after_recovery(
    chrome_worker, monkeypatch, caplog, error, previous,
):
    worker, path = chrome_worker
    if previous is not None:
        worker.store.set_state("chrome_bookmarks_mtime", previous)
    clock = [100.0]
    monkeypatch.setattr(worker_module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    attempts = []

    def unreadable(*args, **kwargs):
        attempts.append(args[0])
        raise error

    with monkeypatch.context() as denied:
        denied.setattr(chrome_bookmarks, "open", unreadable, raising=False)
        with caplog.at_level(logging.WARNING, logger=worker_module.__name__):
            worker._scan_chrome_bookmarks_if_changed()
            clock[0] += 59
            worker._scan_chrome_bookmarks_if_changed()

    assert len(attempts) == 1
    assert worker.store.get_state("chrome_bookmarks_mtime") == previous
    assert str(path) in caplog.text
    assert "retrying in 60 seconds" in caplog.text
    with worker.store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM media_items").fetchone()[0] == 0

    # Access recovers without any mtime change; the failed scan must be retried.
    clock[0] += 1
    worker._scan_chrome_bookmarks_if_changed()
    assert worker.store.get_state("chrome_bookmarks_mtime") == f"{path.stat().st_mtime:.6f}"
    with worker.store.connect() as conn:
        assert conn.execute("SELECT title FROM media_items").fetchone()[0] == "Article"
        assert conn.execute("SELECT COUNT(*) FROM thumbnail_jobs").fetchone()[0] == 1


def test_permission_denied_keeps_worker_alive_and_processes_existing_jobs(chrome_worker, monkeypatch):
    worker, _ = chrome_worker
    lookup = worker.store.register_bookmark("https://example.com/existing", "Existing")
    processed = threading.Event()
    errors = []

    def unreadable(*args, **kwargs):
        raise PermissionError(1, "Operation not permitted")

    def process(job):
        assert job["media_id"] == lookup.media_id
        processed.set()

    monkeypatch.setattr(chrome_bookmarks, "open", unreadable, raising=False)
    monkeypatch.setattr(worker, "_process_job", process)

    def run():
        try:
            worker.run_forever()
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert processed.wait(timeout=3), errors
        assert thread.is_alive()
    finally:
        worker.stop()
        thread.join(timeout=3)
    assert not thread.is_alive()
    assert errors == []
    assert worker.store.acquire_runtime_lease("thumbnail-worker", "test:next-worker")


def test_failed_refresh_preserves_content_cover(chrome_worker, monkeypatch, tmp_path):
    worker, _ = chrome_worker
    lookup = worker.store.register_bookmark("https://example.com/article", "Article")
    path = tmp_path / "thumbs" / f"{lookup.media_id}.webp"
    worker_module.Image.new("RGB", (400, 240), "navy").save(path, "WEBP")
    original = path.read_bytes()
    worker.store.record_thumbnail(lookup.media_id, str(path), provider="metadata",
                                 source_url="https://example.com/cover.jpg", width=400, height=240)
    worker.store.enqueue_job(lookup.media_id, force=True)
    job = worker.store.claim_jobs("test", 1)[0]

    def unavailable(*args, **kwargs):
        raise worker_module.FetchError("HTTP 403")

    monkeypatch.setattr(worker_module, "validate_public_http_url", lambda url: url)
    monkeypatch.setattr(worker_module, "provider_for_url", unavailable)
    worker._process_job(job)

    assert path.read_bytes() == original
    status = worker.store.get_status(lookup.media_id)
    assert status["provider"] == "metadata"
    assert status["thumbnail_url"]
    assert status["status"] == "retry"
    assert status["error"] == "HTTP 403"
