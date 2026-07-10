"""YouTube and structured-metadata thumbnail providers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, Iterable
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .extensions import resolve_local_provider
from .urls import extract_youtube_id


@dataclass(frozen=True)
class ProviderResult:
    provider: str
    image_urls: tuple[str, ...]


def _iter_json_thumbnail_values(value) -> Iterable[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() == "thumbnailurl":
                if isinstance(child, str):
                    yield child
                elif isinstance(child, list):
                    for item in child:
                        if isinstance(item, str):
                            yield item
                        elif isinstance(item, dict):
                            for candidate_key in ("url", "contentUrl"):
                                candidate = item.get(candidate_key)
                                if isinstance(candidate, str):
                                    yield candidate
            yield from _iter_json_thumbnail_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_json_thumbnail_values(child)


def extract_structured_image_urls(html_text: str, page_url: str) -> tuple[str, ...]:
    """Extract only explicit page-image metadata, never arbitrary content images."""
    soup = BeautifulSoup(html_text, "html.parser")
    candidates: list[str] = []
    meta_values: dict[str, list[str]] = {}

    for tag in soup.find_all("meta"):
        key = str(tag.get("property") or tag.get("name") or "").strip().lower()
        content = str(tag.get("content") or "").strip()
        if key and content:
            meta_values.setdefault(key, []).append(content)

    for key in (
        "og:image:secure_url",
        "og:image",
        "twitter:image:src",
        "twitter:image",
    ):
        candidates.extend(meta_values.get(key, []))

    for script in soup.find_all("script", attrs={"type": lambda value: value and value.lower() == "application/ld+json"}):
        raw = script.string or script.get_text("", strip=True)
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            continue
        candidates.extend(_iter_json_thumbnail_values(payload))

    for link in soup.find_all("link"):
        rel = link.get("rel") or []
        rel_values = [str(value).lower() for value in (rel if isinstance(rel, list) else [rel])]
        href = link.get("href")
        if "image_src" in rel_values and href:
            candidates.append(str(href))

    normalized: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        absolute = urljoin(page_url, candidate.strip())
        if absolute.startswith(("http://", "https://")) and absolute not in seen:
            seen.add(absolute)
            normalized.append(absolute)
    return tuple(normalized)


def provider_for_url(url: str, fetch_html: Callable[[str], str]) -> ProviderResult:
    """Resolve ordered candidate image URLs for one bookmark."""
    video_id = extract_youtube_id(url)
    if video_id:
        base = f"https://i.ytimg.com/vi/{video_id}"
        return ProviderResult(
            "youtube",
            (f"{base}/maxresdefault.jpg", f"{base}/sddefault.jpg", f"{base}/hqdefault.jpg"),
        )

    local_result = resolve_local_provider(url, fetch_html)
    if local_result is not None:
        return ProviderResult(str(local_result.provider), tuple(local_result.image_urls))

    html_text = fetch_html(url)
    return ProviderResult("metadata", extract_structured_image_urls(html_text, url))


__all__ = [
    "ProviderResult",
    "extract_structured_image_urls",
    "provider_for_url",
]
