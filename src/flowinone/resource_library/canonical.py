"""Canonical identity and lightweight source classification helpers."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.file_handler.thumbnails.urls import canonicalize_url as canonicalize_bookmark_url


SUPPORTED_SCHEMES = {"http", "https"}
PDF_PATH_RE = re.compile(r"\.pdf$", re.IGNORECASE)
SOCIAL_HOSTS = {
    "bsky.app": "bluesky",
    "facebook.com": "facebook",
    "instagram.com": "instagram",
    "linkedin.com": "linkedin",
    "mastodon.social": "mastodon",
    "threads.net": "threads",
    "twitter.com": "x",
    "x.com": "x",
}


class InvalidResourceURL(ValueError):
    """Raised when a URL cannot be imported into the external resource library."""


def normalize_resource_url(url: str) -> str:
    """Return a stable HTTP(S) URL while retaining content-defining queries."""
    canonical = canonicalize_bookmark_url((url or "").strip())
    try:
        parsed = urlsplit(canonical)
    except ValueError as exc:
        raise InvalidResourceURL("URL 格式無效") from exc
    if parsed.scheme.lower() not in SUPPORTED_SCHEMES or not parsed.hostname:
        raise InvalidResourceURL("Resource Library 只接受公開 HTTP(S) 網址")

    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    query = urlencode(parse_qsl(parsed.query, keep_blank_values=True), doseq=True)
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, query, ""))


def hash_url(canonical_url: str) -> str:
    return hashlib.sha256(canonical_url.encode("utf-8", "ignore")).hexdigest()


def hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "ignore")).hexdigest()


def resource_identity(url: str) -> tuple[str, str]:
    canonical = normalize_resource_url(url)
    return canonical, hash_url(canonical)


def classify_resource(url: str, mime_type: str | None = None) -> tuple[str, str | None]:
    """Infer a conservative content type and known platform without fetching."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    mime = (mime_type or "").lower()
    if "pdf" in mime or PDF_PATH_RE.search(parsed.path):
        return "pdf", None
    if host in {"youtube.com", "youtu.be", "vimeo.com"}:
        return "video", "youtube" if "youtu" in host else "vimeo"
    if host == "github.com" or host.endswith(".github.com"):
        return "github", "github"
    if host in SOCIAL_HOSTS:
        return "social_post", SOCIAL_HOSTS[host]
    if mime.startswith("image/"):
        return "image", None
    if mime.startswith("video/"):
        return "video", None
    return "web_page", None


def normalize_tag(value: str) -> str:
    """Normalize a tag for uniqueness while keeping Unicode words readable."""
    normalized = unicodedata.normalize("NFKC", value or "").strip().lstrip("#")
    normalized = re.sub(r"\s+", "-", normalized)
    normalized = re.sub(r"[^\w\-\u4e00-\u9fff]+", "", normalized, flags=re.UNICODE)
    return normalized.casefold()[:120]


def sanitize_markdown_filename(title: str, fallback: str = "Untitled") -> str:
    """Return a portable Markdown filename without path traversal characters."""
    cleaned = unicodedata.normalize("NFKC", title or fallback)
    cleaned = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "-", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .-")
    cleaned = cleaned[:120].strip(" .-") or fallback
    if cleaned in {".", ".."}:
        cleaned = fallback
    return cleaned if cleaned.casefold().endswith(".md") else f"{cleaned}.md"


def ensure_within(root: Path, candidate: Path) -> Path:
    """Resolve candidate and reject paths that leave root."""
    root_resolved = root.expanduser().resolve()
    candidate_resolved = candidate.expanduser().resolve()
    if candidate_resolved != root_resolved and root_resolved not in candidate_resolved.parents:
        raise ValueError("目標路徑不在允許的資料夾內")
    return candidate_resolved


__all__ = [
    "InvalidResourceURL",
    "classify_resource",
    "ensure_within",
    "hash_text",
    "hash_url",
    "normalize_resource_url",
    "normalize_tag",
    "resource_identity",
    "sanitize_markdown_filename",
]
