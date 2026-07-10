"""Replaceable metadata and content extractors for supported resource types."""

from __future__ import annotations

import io
import base64
import json
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin, urlsplit

import trafilatura
import yt_dlp
from bs4 import BeautifulSoup
from markdownify import markdownify
from pypdf import PdfReader

from src.file_handler.thumbnails.urls import validate_public_http_url
from src.file_handler.thumbnails.worker import DomainRateLimiter, SafeHTTPClient

from .canonical import classify_resource, normalize_resource_url


@dataclass
class ExtractedMetadata:
    title: Optional[str] = None
    description: Optional[str] = None
    canonical_url: Optional[str] = None
    author: Optional[str] = None
    published_at: Optional[str] = None
    language: Optional[str] = None
    source_type: str = "web_page"
    source_platform: Optional[str] = None
    mime_type: Optional[str] = None
    favicon_url: Optional[str] = None
    thumbnail_candidates: list[str] = field(default_factory=list)
    final_url: Optional[str] = None


@dataclass
class ExtractedContent:
    content_type: str
    text: str
    markdown: str = ""
    raw_bytes: bytes = b""
    raw_content_type: str = ""
    extractor_name: str = "generic_web"
    extractor_version: str = "1"


def _decode_payload(payload: bytes, content_type: str) -> str:
    charset = "utf-8"
    match = re.search(r"charset=([^;\s]+)", content_type or "", flags=re.IGNORECASE)
    if match:
        charset = match.group(1).strip("\"'")
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _meta_map(soup: BeautifulSoup) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for element in soup.find_all("meta"):
        key = str(element.get("property") or element.get("name") or "").strip().lower()
        value = str(element.get("content") or "").strip()
        if key and value:
            result.setdefault(key, []).append(value)
    return result


def _first(mapping: dict[str, list[str]], *keys: str) -> Optional[str]:
    for key in keys:
        values = mapping.get(key)
        if values:
            return values[0]
    return None


def _json_ld_author(soup: BeautifulSoup) -> Optional[str]:
    def author_from(value) -> Optional[str]:
        if isinstance(value, str):
            return value.strip() or None
        if isinstance(value, dict):
            name = value.get("name")
            return str(name).strip() if name else None
        if isinstance(value, list):
            names = [author_from(item) for item in value]
            return ", ".join(name for name in names if name) or None
        return None

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text("", strip=True)
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            continue
        candidates = payload if isinstance(payload, list) else [payload]
        for candidate in candidates:
            if isinstance(candidate, dict):
                author = author_from(candidate.get("author") or candidate.get("creator"))
                if author:
                    return author
    return None


class BaseExtractor:
    """Extractor interface used by the worker router."""

    name = "base"
    version = "1"

    def fetch_metadata(self, url: str) -> ExtractedMetadata:
        raise NotImplementedError

    def extract_content(self, url: str) -> ExtractedContent:
        raise NotImplementedError


class GenericWebExtractor(BaseExtractor):
    name = "generic_web"

    def __init__(self, client: Optional[SafeHTTPClient] = None, max_bytes: int = 20_000_000):
        self.client = client or SafeHTTPClient(DomainRateLimiter(interval_seconds=1.0))
        self.max_bytes = max_bytes

    def _fetch(self, url: str) -> tuple[bytes, str, str, str]:
        payload, content_type, final_url = self.client.fetch_bytes(
            url,
            max_bytes=self.max_bytes,
            accept="text/html,application/xhtml+xml,application/pdf,text/plain;q=0.8,*/*;q=0.2",
        )
        return payload, content_type, final_url, _decode_payload(payload, content_type)

    def fetch_metadata(self, url: str) -> ExtractedMetadata:
        payload, content_type, final_url, html_text = self._fetch(url)
        inferred_type, inferred_platform = classify_resource(final_url, content_type)
        if inferred_type == "pdf" or "application/pdf" in content_type.lower():
            return ExtractedMetadata(
                title=urlsplit(final_url).path.rsplit("/", 1)[-1] or "PDF",
                canonical_url=normalize_resource_url(final_url),
                source_type="pdf",
                mime_type=content_type.split(";", 1)[0],
                final_url=final_url,
            )

        soup = BeautifulSoup(html_text, "html.parser")
        metadata = _meta_map(soup)
        canonical_tag = soup.find("link", attrs={"rel": lambda value: value and "canonical" in value})
        canonical_href = canonical_tag.get("href") if canonical_tag else None
        title = _first(metadata, "og:title", "twitter:title")
        if not title and soup.title:
            title = soup.title.get_text(" ", strip=True)
        if not title:
            heading = soup.find("h1")
            title = heading.get_text(" ", strip=True) if heading else None

        favicon = None
        for link in soup.find_all("link"):
            rel = link.get("rel") or []
            rel_values = [str(value).lower() for value in (rel if isinstance(rel, list) else [rel])]
            if any(value in {"icon", "shortcut icon", "apple-touch-icon"} for value in rel_values):
                if link.get("href"):
                    favicon = urljoin(final_url, str(link.get("href")))
                    break
        if not favicon:
            favicon = urljoin(final_url, "/favicon.ico")

        thumbnail_candidates = []
        for key in ("og:image:secure_url", "og:image", "twitter:image:src", "twitter:image"):
            for value in metadata.get(key, []):
                candidate = urljoin(final_url, value)
                if candidate not in thumbnail_candidates:
                    thumbnail_candidates.append(candidate)

        language = None
        if soup.html:
            language = str(soup.html.get("lang") or "").strip() or None
        canonical_url = urljoin(final_url, str(canonical_href)) if canonical_href else final_url
        try:
            canonical_url = normalize_resource_url(canonical_url)
        except ValueError:
            canonical_url = normalize_resource_url(final_url)
        return ExtractedMetadata(
            title=title,
            description=_first(metadata, "og:description", "twitter:description", "description"),
            canonical_url=canonical_url,
            author=_first(metadata, "author", "article:author", "byl") or _json_ld_author(soup),
            published_at=_first(
                metadata,
                "article:published_time",
                "date",
                "datepublished",
                "pubdate",
            ),
            language=language,
            source_type=inferred_type,
            source_platform=inferred_platform,
            mime_type=content_type.split(";", 1)[0] or "text/html",
            favicon_url=favicon,
            thumbnail_candidates=thumbnail_candidates,
            final_url=final_url,
        )

    def extract_content(self, url: str) -> ExtractedContent:
        payload, content_type, final_url, html_text = self._fetch(url)
        if "application/pdf" in content_type.lower() or classify_resource(final_url, content_type)[0] == "pdf":
            return PdfExtractor(self.client, self.max_bytes).extract_payload(payload, content_type)

        extracted_text = trafilatura.extract(
            html_text,
            url=final_url,
            output_format="txt",
            include_comments=False,
            include_tables=True,
            favor_precision=True,
        ) or ""
        extracted_markdown = trafilatura.extract(
            html_text,
            url=final_url,
            output_format="markdown",
            include_comments=False,
            include_tables=True,
            favor_precision=True,
        ) or ""
        if not extracted_text:
            soup = BeautifulSoup(html_text, "html.parser")
            for unwanted in soup(["script", "style", "noscript", "svg"]):
                unwanted.decompose()
            extracted_text = soup.get_text("\n", strip=True)
        if not extracted_markdown:
            extracted_markdown = markdownify(html_text, heading_style="ATX", strip=["script", "style"])
        return ExtractedContent(
            content_type="article_text",
            text=extracted_text.strip(),
            markdown=extracted_markdown.strip(),
            raw_bytes=payload,
            raw_content_type=content_type,
            extractor_name=self.name,
            extractor_version=self.version,
        )


class PdfExtractor(BaseExtractor):
    name = "pdf"

    def __init__(self, client: Optional[SafeHTTPClient] = None, max_bytes: int = 20_000_000):
        self.client = client or SafeHTTPClient(DomainRateLimiter(interval_seconds=1.0))
        self.max_bytes = max_bytes

    def fetch_metadata(self, url: str) -> ExtractedMetadata:
        payload, content_type, final_url = self.client.fetch_bytes(
            url,
            max_bytes=self.max_bytes,
            accept="application/pdf,*/*;q=0.1",
        )
        title = urlsplit(final_url).path.rsplit("/", 1)[-1] or "PDF"
        try:
            reader = PdfReader(io.BytesIO(payload))
            if reader.metadata and reader.metadata.title:
                title = str(reader.metadata.title)
            author = str(reader.metadata.author) if reader.metadata and reader.metadata.author else None
        except Exception:
            author = None
        return ExtractedMetadata(
            title=title,
            canonical_url=normalize_resource_url(final_url),
            author=author,
            source_type="pdf",
            mime_type=content_type.split(";", 1)[0] or "application/pdf",
            final_url=final_url,
        )

    def extract_payload(self, payload: bytes, content_type: str = "application/pdf") -> ExtractedContent:
        reader = PdfReader(io.BytesIO(payload))
        text_parts = []
        for page in reader.pages:
            try:
                value = page.extract_text() or ""
            except Exception:
                value = ""
            if value.strip():
                text_parts.append(value.strip())
        extracted = "\n\n".join(text_parts)
        return ExtractedContent(
            content_type="pdf_text",
            text=extracted,
            markdown=extracted,
            raw_bytes=payload,
            raw_content_type=content_type,
            extractor_name=self.name,
            extractor_version=self.version,
        )

    def extract_content(self, url: str) -> ExtractedContent:
        payload, content_type, _ = self.client.fetch_bytes(
            url,
            max_bytes=self.max_bytes,
            accept="application/pdf,*/*;q=0.1",
        )
        return self.extract_payload(payload, content_type)


def _vtt_to_text(vtt: str) -> str:
    lines: list[str] = []
    previous = ""
    for raw_line in vtt.splitlines():
        line = re.sub(r"<[^>]+>", "", raw_line).strip()
        if not line or line == "WEBVTT" or "-->" in line or line.isdigit():
            continue
        if line.startswith(("Kind:", "Language:", "NOTE ")):
            continue
        if line != previous:
            lines.append(line)
            previous = line
    return "\n".join(lines)


class YouTubeExtractor(BaseExtractor):
    name = "youtube"

    def __init__(self, client: Optional[SafeHTTPClient] = None, max_bytes: int = 20_000_000):
        self.client = client or SafeHTTPClient(DomainRateLimiter(interval_seconds=1.0))
        self.max_bytes = max_bytes

    @staticmethod
    def _info(url: str) -> dict:
        validate_public_http_url(url)
        options = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            "socket_timeout": 20,
        }
        with yt_dlp.YoutubeDL(options) as downloader:
            return downloader.extract_info(url, download=False) or {}

    def fetch_metadata(self, url: str) -> ExtractedMetadata:
        info = self._info(url)
        thumbnails = [
            str(item.get("url"))
            for item in reversed(info.get("thumbnails") or [])
            if isinstance(item, dict) and item.get("url")
        ]
        upload_date = str(info.get("upload_date") or "")
        published_at = (
            f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}"
            if len(upload_date) == 8
            else None
        )
        return ExtractedMetadata(
            title=str(info.get("title") or "") or None,
            description=str(info.get("description") or "") or None,
            canonical_url=str(info.get("webpage_url") or url),
            author=str(info.get("channel") or info.get("uploader") or "") or None,
            published_at=published_at,
            language=str(info.get("language") or "") or None,
            source_type="video",
            source_platform="youtube",
            mime_type="text/html",
            thumbnail_candidates=thumbnails,
            final_url=str(info.get("webpage_url") or url),
        )

    def extract_content(self, url: str) -> ExtractedContent:
        info = self._info(url)
        description = str(info.get("description") or "").strip()
        transcript = ""
        captions = info.get("subtitles") or info.get("automatic_captions") or {}
        preferred = []
        for language in ("zh-TW", "zh-Hant", "zh", "en"):
            if language in captions:
                preferred.extend(captions[language])
        if not preferred and captions:
            preferred = next(iter(captions.values()), [])
        candidate = next(
            (
                item
                for item in preferred
                if isinstance(item, dict) and item.get("url") and item.get("ext") in {"vtt", "srv3", "json3"}
            ),
            None,
        )
        if candidate:
            payload, content_type, _ = self.client.fetch_bytes(
                str(candidate["url"]),
                max_bytes=min(self.max_bytes, 5_000_000),
                accept="text/vtt,text/plain,application/json,*/*;q=0.1",
            )
            raw = _decode_payload(payload, content_type)
            if candidate.get("ext") == "json3":
                try:
                    events = json.loads(raw).get("events") or []
                    transcript = "\n".join(
                        "".join(str(segment.get("utf8") or "") for segment in event.get("segs") or []).strip()
                        for event in events
                        if event.get("segs")
                    )
                except (TypeError, ValueError):
                    transcript = ""
            else:
                transcript = _vtt_to_text(raw)
        parts = [part for part in (description, transcript.strip()) if part]
        return ExtractedContent(
            content_type="video_transcript" if transcript else "article_text",
            text="\n\n".join(parts),
            markdown="\n\n".join(parts),
            extractor_name=self.name,
            extractor_version=self.version,
        )


class GitHubExtractor(BaseExtractor):
    name = "github"

    def __init__(self, client: Optional[SafeHTTPClient] = None, max_bytes: int = 20_000_000):
        self.client = client or SafeHTTPClient(DomainRateLimiter(interval_seconds=1.0))
        self.max_bytes = max_bytes

    @staticmethod
    def _repo_parts(url: str) -> tuple[str, str]:
        parsed = urlsplit(url)
        parts = [part for part in parsed.path.split("/") if part]
        if (parsed.hostname or "").lower().removeprefix("www.") != "github.com" or len(parts) < 2:
            raise ValueError("不是可辨識的 GitHub repository URL")
        return parts[0], parts[1].removesuffix(".git")

    def _json(self, url: str) -> dict:
        payload, _, _ = self.client.fetch_bytes(
            url,
            max_bytes=min(self.max_bytes, 5_000_000),
            accept="application/vnd.github+json,application/json",
        )
        parsed = json.loads(payload.decode("utf-8", errors="replace"))
        if not isinstance(parsed, dict):
            raise ValueError("GitHub API 回傳格式錯誤")
        return parsed

    def fetch_metadata(self, url: str) -> ExtractedMetadata:
        owner, repo = self._repo_parts(url)
        info = self._json(f"https://api.github.com/repos/{owner}/{repo}")
        owner_info = info.get("owner") if isinstance(info.get("owner"), dict) else {}
        topics = info.get("topics") if isinstance(info.get("topics"), list) else []
        description = str(info.get("description") or "").strip()
        if topics:
            description = (description + "\nTopics: " + ", ".join(map(str, topics))).strip()
        return ExtractedMetadata(
            title=str(info.get("full_name") or f"{owner}/{repo}"),
            description=description or None,
            canonical_url=str(info.get("html_url") or url),
            author=str(owner_info.get("login") or owner),
            published_at=str(info.get("created_at") or "") or None,
            language=str(info.get("language") or "") or None,
            source_type="github",
            source_platform="github",
            mime_type="text/markdown",
            thumbnail_candidates=[str(owner_info.get("avatar_url"))]
            if owner_info.get("avatar_url")
            else [],
            final_url=str(info.get("html_url") or url),
        )

    def extract_content(self, url: str) -> ExtractedContent:
        owner, repo = self._repo_parts(url)
        info = self._json(f"https://api.github.com/repos/{owner}/{repo}/readme")
        raw = b""
        download_url = str(info.get("download_url") or "")
        if download_url:
            raw, _, _ = self.client.fetch_bytes(
                download_url,
                max_bytes=min(self.max_bytes, 10_000_000),
                accept="text/plain,text/markdown,*/*;q=0.1",
            )
        elif info.get("content"):
            raw = base64.b64decode(str(info["content"]).encode("ascii"), validate=False)
        markdown = raw.decode("utf-8", errors="replace").strip()
        if not markdown:
            raise ValueError("GitHub repository 沒有可讀取的 README")
        return ExtractedContent(
            content_type="article_text",
            text=markdown,
            markdown=markdown,
            raw_bytes=raw,
            raw_content_type="text/markdown",
            extractor_name=self.name,
            extractor_version=self.version,
        )


def extractor_for(
    url: str,
    source_type: str = "unknown",
    source_platform: Optional[str] = None,
    *,
    max_bytes: int = 20_000_000,
) -> BaseExtractor:
    """Route a resource to the narrowest available extractor."""
    client = SafeHTTPClient(DomainRateLimiter(interval_seconds=1.0))
    if source_platform == "youtube" or "youtu" in (urlsplit(url).hostname or ""):
        return YouTubeExtractor(client, max_bytes)
    if source_type == "github" or (urlsplit(url).hostname or "").lower().endswith("github.com"):
        return GitHubExtractor(client, max_bytes)
    if source_type == "pdf" or urlsplit(url).path.lower().endswith(".pdf"):
        return PdfExtractor(client, max_bytes)
    return GenericWebExtractor(client, max_bytes)


__all__ = [
    "BaseExtractor",
    "ExtractedContent",
    "ExtractedMetadata",
    "GenericWebExtractor",
    "GitHubExtractor",
    "PdfExtractor",
    "YouTubeExtractor",
    "extractor_for",
]
