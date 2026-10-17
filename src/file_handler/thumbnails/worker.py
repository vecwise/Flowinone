"""Durable worker runtime for the shared thumbnail processing pipeline."""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Optional
from urllib.parse import urlsplit

from PIL import Image  # Compatibility for existing consumers of this module.

from src.flowinone.config import CHROME_BOOKMARK_PATH
from ..models import BookmarkError
from .http import DomainRateLimiter, FetchError, MAX_HTML_BYTES, MAX_IMAGE_BYTES, SafeHTTPClient
from .images import _convert_to_webp, _site_tile
from .pipeline import ThumbnailPipeline
from .providers import provider_for_url
from .store import PRIORITY_BOOKMARK_CHANGE, PRIORITY_MISSING, ThumbnailStore, get_thumbnail_store
from .urls import validate_public_http_url

LOGGER = logging.getLogger(__name__)


class ThumbnailWorker:
    def __init__(
        self,
        store: Optional[ThumbnailStore] = None,
        max_workers: int = 4,
        domain_filter: Optional[str] = None,
        media_ids: Optional[list[str]] = None,
    ):
        self.store = store or get_thumbnail_store()
        self.max_workers = max(1, min(max_workers, 4))
        self.domain_filter = domain_filter
        self.media_ids = media_ids
        self.owner = f"{os.getpid()}:{uuid.uuid4().hex}"
        self.stop_event = threading.Event()
        self.rate_limiter = DomainRateLimiter(interval_seconds=1.0)
        self._last_bookmark_check = 0.0
        self.pipeline = ThumbnailPipeline(self.store, SafeHTTPClient(self.rate_limiter))

    def _process_job(self, job: dict) -> dict:
        return self.pipeline.process(
            job, resolver=provider_for_url, validator=validate_public_http_url
        )

    def _scan_chrome_bookmarks_if_changed(self) -> None:
        now = time.monotonic()
        if now - self._last_bookmark_check < 60:
            return
        self._last_bookmark_check = now
        try:
            mtime = os.path.getmtime(CHROME_BOOKMARK_PATH)
        except OSError:
            return
        mtime_value = f"{mtime:.6f}"
        previous = self.store.get_state("chrome_bookmarks_mtime")
        if previous == mtime_value:
            return

        from ..chrome_bookmarks import iter_chrome_bookmark_records

        try:
            bookmarks = list(iter_chrome_bookmark_records())
        except BookmarkError as exc:
            # Chrome is optional. Leave the successful mtime unchanged so a
            # denied read or an incomplete Chrome write is retried next minute.
            LOGGER.warning(
                "Chrome bookmarks scan skipped for %s; retrying in 60 seconds: %s",
                CHROME_BOOKMARK_PATH,
                exc,
            )
            return

        initial_scan = previous is None
        priority = PRIORITY_MISSING if initial_scan else PRIORITY_BOOKMARK_CHANGE
        for bookmark in bookmarks:
            if urlsplit(bookmark['url']).scheme.lower() not in {'http','https'}:
                continue
            self.store.register_bookmark(
                bookmark["url"],
                bookmark["title"],
                {"folder_path": bookmark.get("folder_path") or ""},
                priority=priority,
                enqueue_missing=True,
                # Another entry point may have registered this URL without
                # queuing it. enqueue_job preserves any existing retry delay.
            )
        self.store.set_state("chrome_bookmarks_mtime", mtime_value)

    def _run_loop(self, *, stop_when_idle: bool = False, max_jobs: Optional[int] = None) -> int:
        if not self.store.acquire_runtime_lease("thumbnail-worker", self.owner):
            return 0
        completed = 0
        futures: dict[Future, dict] = {}
        active_domains: set[str] = set()
        last_lease_refresh = 0.0

        with ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="flowinone-thumb") as executor:
            while not self.stop_event.is_set():
                for future, job in list(futures.items()):
                    if not future.done():
                        continue
                    try:
                        future.result()
                    except Exception:
                        pass
                    active_domains.discard(str(job["domain"]))
                    del futures[future]
                    completed += 1

                if max_jobs is not None and completed >= max_jobs and not futures:
                    break

                now = time.monotonic()
                if now - last_lease_refresh >= 30:
                    if not self.store.acquire_runtime_lease("thumbnail-worker", self.owner):
                        break
                    last_lease_refresh = now

                if not stop_when_idle:
                    self._scan_chrome_bookmarks_if_changed()

                slots = self.max_workers - len(futures)
                if max_jobs is not None:
                    slots = min(slots, max_jobs - completed - len(futures))
                claimed = self.store.claim_jobs(
                    self.owner,
                    slots,
                    active_domains,
                    domain_filter=self.domain_filter,
                    media_ids=self.media_ids,
                ) if slots > 0 else []
                for job in claimed:
                    active_domains.add(str(job["domain"]))
                    futures[executor.submit(self._process_job, job)] = job

                if stop_when_idle and not claimed and not futures:
                    break
                self.stop_event.wait(0.5)
        return completed

    def run_forever(self) -> None:
        try:
            self._run_loop(stop_when_idle=False)
        finally:
            self.store.release_runtime_lease("thumbnail-worker", self.owner)

    def run_until_idle(self, max_jobs: Optional[int] = None) -> int:
        try:
            return self._run_loop(stop_when_idle=True, max_jobs=max_jobs)
        finally:
            self.store.release_runtime_lease("thumbnail-worker", self.owner)

    def stop(self) -> None:
        self.stop_event.set()


__all__ = [
    "DomainRateLimiter",
    "FetchError",
    "MAX_HTML_BYTES",
    "MAX_IMAGE_BYTES",
    "SafeHTTPClient",
    "ThumbnailWorker",
]
