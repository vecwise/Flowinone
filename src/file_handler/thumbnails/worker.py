"""Background thumbnail worker with bounded networking and image validation."""

from __future__ import annotations

import hashlib
import io
import os
import tempfile
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urljoin, urlsplit

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

from config import CHROME_BOOKMARK_PATH

from .providers import provider_for_url
from .store import (
    PRIORITY_BOOKMARK_CHANGE,
    PRIORITY_MISSING,
    ThumbnailStore,
    get_thumbnail_store,
)
from .urls import UnsafeURLError, validate_public_http_url


MAX_HTML_BYTES = 2 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_REDIRECTS = 3
MAX_IMAGE_EDGE = 640
WEBP_QUALITY = 80

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


class FetchError(RuntimeError):
    """Raised when a bounded HTTP fetch cannot produce usable content."""


class DomainRateLimiter:
    """Allow one active request per hostname with a minimum start interval."""

    def __init__(self, interval_seconds: float = 1.0):
        self.interval_seconds = interval_seconds
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._last_start: dict[str, float] = {}

    @contextmanager
    def slot(self, url: str):
        domain = (urlsplit(url).hostname or "").lower()
        with self._guard:
            lock = self._locks.setdefault(domain, threading.Lock())
        with lock:
            with self._guard:
                elapsed = time.monotonic() - self._last_start.get(domain, 0.0)
            if elapsed < self.interval_seconds:
                time.sleep(self.interval_seconds - elapsed)
            with self._guard:
                self._last_start[domain] = time.monotonic()
            yield


class SafeHTTPClient:
    """HTTP client that checks every redirect target and enforces byte limits."""

    def __init__(self, limiter: Optional[DomainRateLimiter] = None):
        self.limiter = limiter or DomainRateLimiter()

    def fetch_bytes(self, url: str, *, max_bytes: int, accept: str, timeout: int = 15) -> tuple[bytes, str, str]:
        current_url = url
        session = requests.Session()
        session.trust_env = False
        headers = dict(DEFAULT_HEADERS)
        headers["Accept"] = accept

        try:
            for redirect_count in range(MAX_REDIRECTS + 1):
                validate_public_http_url(current_url)
                with self.limiter.slot(current_url):
                    response = session.get(
                        current_url,
                        headers=headers,
                        timeout=(5, timeout),
                        stream=True,
                        allow_redirects=False,
                    )
                    try:
                        if response.is_redirect or response.is_permanent_redirect:
                            if redirect_count >= MAX_REDIRECTS:
                                raise FetchError("too many redirects")
                            location = response.headers.get("Location")
                            if not location:
                                raise FetchError("redirect had no Location header")
                            current_url = urljoin(current_url, location)
                            continue
                        if response.status_code != 200:
                            raise FetchError(f"HTTP {response.status_code}")

                        content_length = response.headers.get("Content-Length")
                        if content_length:
                            try:
                                if int(content_length) > max_bytes:
                                    raise FetchError(f"response exceeds {max_bytes} bytes")
                            except ValueError:
                                pass

                        chunks = []
                        size = 0
                        for chunk in response.iter_content(chunk_size=64 * 1024):
                            if not chunk:
                                continue
                            size += len(chunk)
                            if size > max_bytes:
                                raise FetchError(f"response exceeds {max_bytes} bytes")
                            chunks.append(chunk)
                        if not chunks:
                            raise FetchError("empty response")
                        return b"".join(chunks), response.headers.get("Content-Type", ""), current_url
                    finally:
                        response.close()
        except requests.RequestException as exc:
            raise FetchError(str(exc)) from exc
        finally:
            session.close()
        raise FetchError("request did not complete")

    def fetch_html(self, url: str) -> str:
        payload, content_type, _ = self.fetch_bytes(
            url,
            max_bytes=MAX_HTML_BYTES,
            accept="text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
        )
        charset = "utf-8"
        if "charset=" in content_type.lower():
            charset = content_type.lower().split("charset=", 1)[1].split(";", 1)[0].strip()
        try:
            return payload.decode(charset, errors="replace")
        except LookupError:
            return payload.decode("utf-8", errors="replace")


def _font(size: int, bold: bool = False):
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size=size)
            except OSError:
                continue
    return ImageFont.load_default(size=size)


def _site_tile(media_id: str, url: str, cache_dir: str) -> tuple[str, int, int]:
    """Create a restrained local fallback that remains recognizable in a grid."""
    width, height = 640, 360
    domain = (urlsplit(url).hostname or "bookmark").lower()
    display_domain = domain.removeprefix("www.")[:42]
    digest = hashlib.sha1(domain.encode("utf-8", "ignore")).digest()
    accents = [(21, 135, 119), (194, 82, 61), (214, 156, 52), (52, 105, 163)]
    accent = accents[digest[0] % len(accents)]
    background = (244, 242, 236)
    ink = (28, 31, 34)
    muted = (95, 99, 102)

    image = Image.new("RGB", (width, height), background)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width, 12), fill=accent)
    draw.rectangle((40, 46, 116, 122), fill=ink)
    initials = "".join(part[0] for part in display_domain.split(".") if part)[:2].upper() or "B"
    initials_font = _font(34, bold=True)
    box = draw.textbbox((0, 0), initials, font=initials_font)
    draw.text(
        (78 - (box[2] - box[0]) / 2, 84 - (box[3] - box[1]) / 2 - box[1]),
        initials,
        font=initials_font,
        fill=(255, 255, 255),
    )
    draw.text((40, 166), "BOOKMARK COVER", font=_font(15, bold=True), fill=accent)
    draw.text((40, 205), display_domain, font=_font(34, bold=True), fill=ink)
    draw.line((40, 288, 600, 288), fill=(207, 205, 199), width=1)
    draw.text((40, 306), "Flowinone local fallback", font=_font(16), fill=muted)

    os.makedirs(cache_dir, exist_ok=True)
    target_path = os.path.abspath(os.path.join(cache_dir, f"{media_id}.webp"))
    handle = tempfile.NamedTemporaryFile(prefix=f".{media_id}-", suffix=".webp", dir=cache_dir, delete=False)
    temp_path = handle.name
    handle.close()
    try:
        image.save(temp_path, format="WEBP", quality=WEBP_QUALITY, method=6)
        os.replace(temp_path, target_path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
    return target_path, width, height


def _convert_to_webp(media_id: str, payload: bytes, cache_dir: str, *, min_width: int = 80) -> tuple[str, int, int]:
    try:
        with Image.open(io.BytesIO(payload)) as source:
            source.load()
            image = ImageOps.exif_transpose(source)
            if image.width < min_width or image.height < 60:
                raise FetchError(f"image dimensions are too small: {image.width}x{image.height}")
            if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
                rgba = image.convert("RGBA")
                flattened = Image.new("RGB", rgba.size, (244, 242, 236))
                flattened.paste(rgba, mask=rgba.getchannel("A"))
                image = flattened
            else:
                image = image.convert("RGB")
            image.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE), Image.Resampling.LANCZOS)
            width, height = image.size

            os.makedirs(cache_dir, exist_ok=True)
            target_path = os.path.abspath(os.path.join(cache_dir, f"{media_id}.webp"))
            handle = tempfile.NamedTemporaryFile(prefix=f".{media_id}-", suffix=".webp", dir=cache_dir, delete=False)
            temp_path = handle.name
            handle.close()
            try:
                image.save(temp_path, format="WEBP", quality=WEBP_QUALITY, method=6)
                os.replace(temp_path, target_path)
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
            return target_path, width, height
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise FetchError("response was not a valid raster image") from exc


class ThumbnailWorker:
    def __init__(
        self,
        store: Optional[ThumbnailStore] = None,
        max_workers: int = 4,
        domain_filter: Optional[str] = None,
    ):
        self.store = store or get_thumbnail_store()
        self.max_workers = max(1, min(max_workers, 4))
        self.domain_filter = domain_filter
        self.owner = f"{os.getpid()}:{uuid.uuid4().hex}"
        self.stop_event = threading.Event()
        self.rate_limiter = DomainRateLimiter(interval_seconds=1.0)
        self._last_bookmark_check = 0.0

    def _process_job(self, job: dict) -> None:
        media_id = str(job["media_id"])
        url = str(job["url"])
        client = SafeHTTPClient(self.rate_limiter)
        try:
            validate_public_http_url(url)
            result = provider_for_url(url, client.fetch_html)
            errors = []
            for candidate in result.image_urls:
                try:
                    payload, _, final_url = client.fetch_bytes(
                        candidate,
                        max_bytes=MAX_IMAGE_BYTES,
                        accept="image/avif,image/webp,image/apng,image/*,*/*;q=0.5",
                    )
                    min_width = 320 if result.provider == "youtube" else 80
                    local_path, width, height = _convert_to_webp(
                        media_id,
                        payload,
                        self.store.cache_dir,
                        min_width=min_width,
                    )
                    ttl_days = 180 if result.provider == "youtube" else 90 if result.provider != "metadata" else 30
                    self.store.record_thumbnail(
                        media_id,
                        local_path,
                        provider=result.provider,
                        source_url=final_url,
                        width=width,
                        height=height,
                        ttl_days=ttl_days,
                    )
                    return
                except (FetchError, UnsafeURLError) as exc:
                    errors.append(str(exc))
            detail = "; ".join(errors[-3:]) if errors else "provider returned no explicit thumbnail"
            raise FetchError(detail)
        except Exception as exc:
            try:
                local_path, width, height = _site_tile(media_id, url, self.store.cache_dir)
                self.store.record_thumbnail(
                    media_id,
                    local_path,
                    provider="site-tile",
                    source_url=None,
                    width=width,
                    height=height,
                    ttl_days=7,
                    status="retry",
                )
            finally:
                self.store.mark_job_failure(media_id, str(exc))

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

        initial_scan = previous is None
        priority = PRIORITY_MISSING if initial_scan else PRIORITY_BOOKMARK_CHANGE
        for bookmark in iter_chrome_bookmark_records():
            self.store.register_bookmark(
                bookmark["url"],
                bookmark["title"],
                {"folder_path": bookmark.get("folder_path") or ""},
                priority=priority,
                enqueue_missing=True,
                only_if_new=not initial_scan,
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


_WORKER: Optional[ThumbnailWorker] = None
_WORKER_THREAD: Optional[threading.Thread] = None
_WORKER_LOCK = threading.Lock()


def start_background_worker() -> Optional[ThumbnailWorker]:
    global _WORKER, _WORKER_THREAD
    with _WORKER_LOCK:
        if _WORKER_THREAD and _WORKER_THREAD.is_alive():
            return _WORKER
        worker = ThumbnailWorker()
        thread = threading.Thread(target=worker.run_forever, name="flowinone-thumbnail-worker", daemon=True)
        thread.start()
        _WORKER = worker
        _WORKER_THREAD = thread
        return worker


__all__ = [
    "DomainRateLimiter",
    "FetchError",
    "MAX_HTML_BYTES",
    "MAX_IMAGE_BYTES",
    "SafeHTTPClient",
    "ThumbnailWorker",
    "start_background_worker",
]
