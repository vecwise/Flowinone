"""One image-processing path for automatic jobs and explicit backfills."""
from __future__ import annotations

from pathlib import Path

from .extensions import fetch_local_image, resolve_local_sources
from .http import FetchError, MAX_IMAGE_BYTES, SafeHTTPClient
from .images import _convert_to_webp, _site_tile
from .providers import normalize_result, provider_for_url
from .urls import validate_public_http_url


class ThumbnailPipeline:
    def __init__(self, store, client=None):
        self.store = store
        self.client = client or SafeHTTPClient()

    def _save(self, media_id, payload, result, source_url=None):
        if len(payload) > MAX_IMAGE_BYTES:
            raise FetchError("image exceeds byte budget")
        path, width, height = _convert_to_webp(
            media_id, payload, self.store.cache_dir, min_width=result.min_width
        )
        self.store.record_thumbnail(
            media_id, path, provider=result.provider, source_url=source_url,
            width=width, height=height, ttl_days=result.ttl_days,
        )
        return self.store.get_status(media_id)

    def _attempt(self, media_id, page_url, result, errors):
        result = normalize_result(result)
        for raw_path in result.local_paths:
            try:
                with Path(raw_path).expanduser().open("rb") as handle:
                    payload = handle.read(MAX_IMAGE_BYTES + 1)
                return self._save(media_id, payload, result)
            except Exception as exc:
                errors.append(str(exc))
        for candidate in result.image_urls:
            try:
                validate_public_http_url(candidate)
                fetched = fetch_local_image(candidate, page_url, self.client)
                payload, _, final_url = fetched if fetched is not None else self.client.fetch_bytes(
                    candidate, max_bytes=MAX_IMAGE_BYTES, accept="image/*"
                )
                validate_public_http_url(final_url)
                return self._save(media_id, payload, result, final_url)
            except Exception as exc:
                errors.append(str(exc))
        return None

    def process(self, job, *, resolver=provider_for_url, validator=validate_public_http_url):
        media_id, url = str(job["media_id"]), str(job["url"])
        errors = []
        try:
            validator(url)
            try:
                cached = resolve_local_sources(url, media_id, self.store)
                if cached is not None:
                    saved = self._attempt(media_id, url, cached, errors)
                    if saved:
                        return saved
            except Exception as exc:
                errors.append(str(exc))
            result = resolver(url, self.client.fetch_html)
            saved = self._attempt(media_id, url, result, errors)
            if saved:
                return saved
            raise FetchError("; ".join(errors[-3:]) or "provider returned no explicit thumbnail")
        except Exception as exc:
            try:
                existing = self.store.get_status(media_id)
                if not existing.get("thumbnail_url") or existing.get("provider") == "site-tile":
                    path, width, height = _site_tile(media_id, url, self.store.cache_dir)
                    self.store.record_thumbnail(
                        media_id, path, provider="site-tile", source_url=None,
                        width=width, height=height, ttl_days=7, status="retry",
                    )
            finally:
                self.store.mark_job_failure(media_id, str(exc))
            return self.store.get_status(media_id)
