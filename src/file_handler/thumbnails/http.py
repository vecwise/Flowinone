"""Bounded, rate-limited public HTTP fetching shared by thumbnail entry points."""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Optional
from urllib.parse import urljoin, urlsplit

import requests

from .urls import validate_public_http_url

MAX_HTML_BYTES = 2 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_REDIRECTS = 3

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

    def fetch_bytes(self, url: str, *, max_bytes: int, accept: str, timeout: int = 15,
                    headers: Optional[dict] = None, session_factory=None,
                    allow_truncated: bool = False) -> tuple[bytes, str, str]:
        current_url = url
        session = session_factory() if session_factory else requests.Session()
        session.trust_env = False
        request_headers = {**DEFAULT_HEADERS, "Accept": accept, **(headers or {})}
        request_headers = {key: value for key, value in request_headers.items() if value is not None}

        try:
            for redirect_count in range(MAX_REDIRECTS + 1):
                validate_public_http_url(current_url)
                with self.limiter.slot(current_url):
                    response = session.get(
                        current_url,
                        headers=request_headers,
                        timeout=(5, timeout),
                        stream=True,
                        allow_redirects=False,
                    )
                    try:
                        if response.status_code in {301, 302, 303, 307, 308}:
                            if redirect_count >= MAX_REDIRECTS:
                                raise FetchError("too many redirects")
                            location = response.headers.get("Location")
                            if not location:
                                raise FetchError("redirect had no Location header")
                            current_url = urljoin(current_url, location)
                            continue
                        if response.status_code not in {200, 203}:
                            raise FetchError(f"HTTP {response.status_code}")

                        content_length = response.headers.get("Content-Length")
                        if content_length:
                            try:
                                if int(content_length) > max_bytes and not allow_truncated:
                                    raise FetchError(f"response exceeds {max_bytes} bytes")
                            except ValueError:
                                pass

                        chunks = []
                        size = 0
                        for chunk in response.iter_content(chunk_size=64 * 1024):
                            if not chunk:
                                continue
                            if allow_truncated:
                                chunk = chunk[:max_bytes - size]
                            size += len(chunk)
                            if size > max_bytes:
                                raise FetchError(f"response exceeds {max_bytes} bytes")
                            chunks.append(chunk)
                            if allow_truncated and size >= max_bytes:
                                break
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
            # Keep usable metadata when the page has a large script or body.
            # The response still closes at the original HTML byte budget.
            allow_truncated=True,
        )
        charset = "utf-8"
        if "charset=" in content_type.lower():
            charset = content_type.lower().split("charset=", 1)[1].split(";", 1)[0].strip()
        try:
            return payload.decode(charset, errors="replace")
        except LookupError:
            return payload.decode("utf-8", errors="replace")
