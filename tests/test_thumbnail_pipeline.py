import io
import json
import os
from types import SimpleNamespace

import pytest
from PIL import Image

from src.file_handler.thumbnails import extensions, http, pipeline
from src.file_handler.thumbnails.batch import run_thumbnail_batch
from src.file_handler.thumbnails.providers import ProviderResult
from src.file_handler.thumbnails.store import ThumbnailStore
from src.file_handler.thumbnails.worker import ThumbnailWorker


def image_bytes(color="navy"):
    buffer = io.BytesIO()
    Image.new("RGB", (400, 240), color).save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture
def store(tmp_path):
    return ThumbnailStore(str(tmp_path / "cache.db"), str(tmp_path / "thumbs"))


@pytest.fixture
def public_urls(monkeypatch):
    monkeypatch.setattr(http, "validate_public_http_url", lambda url: url)
    monkeypatch.setattr(pipeline, "validate_public_http_url", lambda url: url)
    monkeypatch.setattr("src.file_handler.thumbnails.worker.validate_public_http_url", lambda url: url)


def test_automatic_and_batch_share_local_asset_pipeline(store, tmp_path, monkeypatch, public_urls):
    cached = tmp_path / "private-cover.png"
    cached.write_bytes(image_bytes())
    calls = []

    def sources(url, media_id, target_store):
        calls.append(url)
        return ProviderResult("local-asset", local_paths=(str(cached),))

    monkeypatch.setattr(extensions, "load_local_provider", lambda: SimpleNamespace(resolve_sources=sources))
    monkeypatch.setattr(http.SafeHTTPClient, "fetch_bytes", lambda *a, **kw: pytest.fail("local asset made a network request"))
    bookmarks = tmp_path / "Bookmarks"
    bookmarks.write_text(json.dumps({"roots":{"bookmark_bar":{"type":"folder","name":"Saved","children":[
        {"type":"url","name":"Automatic","url":"https://example.com/automatic"}
    ]}}}))
    monkeypatch.setattr("src.file_handler.thumbnails.worker.CHROME_BOOKMARK_PATH", str(bookmarks))
    monkeypatch.setattr("src.file_handler.chrome_bookmarks.CHROME_BOOKMARK_PATH", str(bookmarks))
    # A prior import can register a new URL before the watcher observes it.
    store.register_bookmark("https://example.com/automatic", "Automatic", enqueue_missing=False)
    store.set_state("chrome_bookmarks_mtime", "previous-scan")
    automatic = ThumbnailWorker(store=store)
    automatic._scan_chrome_bookmarks_if_changed()
    assert automatic.run_until_idle() == 1
    manual = store.register_bookmark("https://example.com/batch", "Batch", enqueue_missing=False)
    result = run_thumbnail_batch(store=store, media_ids=[manual.media_id])
    assert result["processed"] == 1
    assert len(calls) == 2
    with store.connect() as connection:
        images = connection.execute("SELECT source,width,height FROM thumbnails").fetchall()
    assert [tuple(row) for row in images] == [("local-asset",400,240)] * 2


def test_bad_local_sources_fall_back_to_metadata_and_image_transport(store, tmp_path, monkeypatch, public_urls):
    attempts = []

    def sources(*_):
        return ProviderResult("local-asset", local_paths=(str(tmp_path / "absent.png"),),
                              image_urls=("https://cdn.example.com/broken",))

    def image(url, page_url, client):
        attempts.append((url, page_url))
        if url.endswith("broken"):
            raise http.FetchError("HTTP 403")
        return image_bytes(), "image/png", url

    monkeypatch.setattr(extensions, "load_local_provider", lambda: SimpleNamespace(resolve_sources=sources, fetch_image=image))
    monkeypatch.setattr(http.SafeHTTPClient, "fetch_html", lambda *a: '<meta property="og:image" content="https://cdn.example.com/cover">')
    lookup = store.register_bookmark("https://example.com/article", "Article")
    assert ThumbnailWorker(store=store).run_until_idle() == 1
    assert store.get_status(lookup.media_id)["provider"] == "metadata"
    assert [url for url, _ in attempts] == ["https://cdn.example.com/broken", "https://cdn.example.com/cover"]


def test_batch_queues_for_live_worker_and_does_not_override_lease(store, monkeypatch, public_urls):
    monkeypatch.setattr(extensions, "load_local_provider", lambda: SimpleNamespace(
        resolve=lambda *a: ProviderResult("custom", ("https://cdn.example.com/cover",)),
        fetch_image=lambda *a: (image_bytes(), "image/png", "https://cdn.example.com/cover"),
    ))
    lookup = store.register_bookmark("https://example.com/article", "Article", enqueue_missing=False)
    owner = f"{os.getpid()}:running-worker"
    assert store.acquire_runtime_lease("thumbnail-worker", owner)
    result = run_thumbnail_batch(store=store, media_ids=[lookup.media_id])
    assert result["queued"] == 1 and result["processed"] == 0
    assert store.get_status(lookup.media_id)["status"] == "queued"
    store.release_runtime_lease("thumbnail-worker", owner)
    assert ThumbnailWorker(store=store).run_until_idle() == 1
    assert store.get_status(lookup.media_id)["status"] == "ready"


def test_batch_retries_tiles_but_preserves_good_covers(store, tmp_path, monkeypatch, public_urls):
    lookups = [store.register_bookmark(f"https://example.com/{i}", str(i)) for i in range(2)]
    for i, lookup in enumerate(lookups):
        path = tmp_path / f"{i}.png"
        path.write_bytes(image_bytes())
        store.record_thumbnail(lookup.media_id, str(path), provider="site-tile" if i == 0 else "metadata",
                               source_url=None, width=400, height=240)
    monkeypatch.setattr(extensions, "load_local_provider", lambda: SimpleNamespace(
        resolve=lambda *a: ProviderResult("custom", ("https://cdn.example.com/cover",)),
        fetch_image=lambda *a: (image_bytes(), "image/png", "https://cdn.example.com/cover"),
    ))
    result = run_thumbnail_batch(store=store, retry_failed=True)
    assert result["considered"] == result["processed"] == 1
    assert store.get_status(lookups[0].media_id)["provider"] == "custom"
    assert store.get_status(lookups[1].media_id)["provider"] == "metadata"


def test_claim_filters_before_queue_window_and_respects_requested_ids(store):
    for i in range(105):
        store.register_bookmark(f"https://busy.example/{i}", str(i))
    wanted = store.register_bookmark("https://target.example/item", "Wanted")
    assert store.claim_jobs("batch", 1, domain_filter="target.example", media_ids=[wanted.media_id])[0]["media_id"] == wanted.media_id
    assert store.claim_jobs("other", 4, media_ids=[]) == []


def test_image_override_cannot_return_private_redirect(store, monkeypatch, public_urls):
    from src.file_handler.thumbnails.urls import UnsafeURLError
    monkeypatch.setattr(extensions, "load_local_provider", lambda: SimpleNamespace(
        resolve=lambda *a: ProviderResult("custom", ("https://cdn.example.com/cover",)),
        fetch_image=lambda *a: (image_bytes(), "image/png", "http://127.0.0.1/private"),
    ))
    def reject_private(url):
        if "127.0.0.1" in url:
            raise UnsafeURLError("private target")
        return url
    monkeypatch.setattr(pipeline, "validate_public_http_url", reject_private)
    lookup = store.register_bookmark("https://example.com/article", "Article")
    ThumbnailWorker(store=store).run_until_idle()
    assert store.get_status(lookup.media_id)["provider"] == "site-tile"
    assert "private target" in store.get_status(lookup.media_id)["error"]


def test_batch_cli_uses_shared_pipeline(store, monkeypatch, public_urls):
    from flask import Flask
    from src.flowinone.web import chrome

    monkeypatch.setattr(chrome, "get_thumbnail_store", lambda:store)
    monkeypatch.setattr(chrome, "iter_chrome_bookmark_records", lambda:iter([
        {"url":"https://example.com/article","title":"Article","folder_path":"Saved"},
    ]))
    monkeypatch.setattr(http.SafeHTTPClient, "fetch_html", lambda *a:'<meta property="og:image" content="https://cdn.example.com/cover">')
    monkeypatch.setattr(http.SafeHTTPClient, "fetch_bytes", lambda *a,**kw:(image_bytes(),"image/png","https://cdn.example.com/cover"))
    app = Flask(__name__)
    chrome.register_commands(app)
    result = app.test_cli_runner().invoke(args=["thumbnails-sync","--missing","--retry-failed","--limit","1"])
    assert result.exit_code == 0, result.output
    output = json.loads(result.output)
    assert output["registered"] == output["processed"] == output["queued"] == 1
