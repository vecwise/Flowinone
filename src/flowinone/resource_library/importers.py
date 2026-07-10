"""Parsers for Chrome profile JSON, Netscape bookmark HTML, and URL records."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator, Optional

from bs4 import BeautifulSoup, Tag as SoupTag

from .models import utc_now_text


CHROME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class BookmarkRecord:
    url: str
    title: str
    folder_path: str = ""
    captured_at: str = ""
    source_key: Optional[str] = None
    source: str = "chrome_json"

    def normalized_capture_time(self) -> str:
        return self.captured_at or utc_now_text()


def chrome_time_to_iso(raw: object) -> str:
    """Convert Chrome microseconds since 1601 to UTC ISO-8601."""
    try:
        value = int(str(raw or "0"))
    except (TypeError, ValueError):
        return utc_now_text()
    if value <= 0:
        return utc_now_text()
    try:
        converted = CHROME_EPOCH + timedelta(microseconds=value)
    except OverflowError:
        return utc_now_text()
    return converted.replace(microsecond=0).isoformat()


def iter_chrome_json_bookmarks(payload: dict) -> Iterator[BookmarkRecord]:
    """Yield every URL node and preserve each bookmark placement."""
    roots = payload.get("roots") if isinstance(payload, dict) else None
    if not isinstance(roots, dict):
        raise ValueError("Chrome JSON 缺少 roots 物件")

    def walk(node: dict, labels: list[str]) -> Iterator[BookmarkRecord]:
        node_type = node.get("type")
        if node_type == "folder":
            label = str(node.get("name") or "(未命名資料夾)").strip()
            next_labels = labels + ([label] if label else [])
            for child in node.get("children") or []:
                if isinstance(child, dict):
                    yield from walk(child, next_labels)
            return
        if node_type == "url" and node.get("url"):
            url = str(node["url"])
            yield BookmarkRecord(
                url=url,
                title=str(node.get("name") or url),
                folder_path=" / ".join(labels),
                captured_at=chrome_time_to_iso(node.get("date_added")),
                source_key=str(node.get("guid") or node.get("id") or "") or None,
                source="chrome_json",
            )

    for key in ("bookmark_bar", "other", "synced", "mobile"):
        root = roots.get(key)
        if not isinstance(root, dict):
            continue
        root_label = str(root.get("name") or key.replace("_", " ").title()).strip()
        for child in root.get("children") or []:
            if isinstance(child, dict):
                yield from walk(child, [root_label])


def _unix_seconds_to_iso(raw: object) -> str:
    try:
        value = int(str(raw or "0"))
    except (TypeError, ValueError):
        return utc_now_text()
    if value <= 0:
        return utc_now_text()
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc).replace(microsecond=0).isoformat()
    except (OverflowError, OSError, ValueError):
        return utc_now_text()


def iter_netscape_bookmark_html(html_text: str) -> Iterator[BookmarkRecord]:
    """Parse Chrome/Netscape bookmark export HTML with nested folder paths."""
    soup = BeautifulSoup(html_text, "html.parser")
    root = soup.find("dl")
    if root is None:
        raise ValueError("書籤 HTML 找不到 DL 結構")

    def child_dl(dt: SoupTag) -> Optional[SoupTag]:
        nested = dt.find("dl", recursive=False)
        if isinstance(nested, SoupTag):
            return nested
        sibling = dt.find_next_sibling("dl")
        return sibling if isinstance(sibling, SoupTag) else None

    def walk(container: SoupTag, labels: list[str]) -> Iterator[BookmarkRecord]:
        for dt in container.find_all("dt"):
            if dt.find_parent("dl") is not container:
                continue
            anchor = dt.find("a", recursive=False)
            if isinstance(anchor, SoupTag) and anchor.get("href"):
                url = str(anchor.get("href"))
                yield BookmarkRecord(
                    url=url,
                    title=anchor.get_text(" ", strip=True) or url,
                    folder_path=" / ".join(labels),
                    captured_at=_unix_seconds_to_iso(anchor.get("add_date")),
                    source_key=str(anchor.get("id") or "") or None,
                    source="chrome_html",
                )
                continue
            heading = dt.find("h3", recursive=False)
            if isinstance(heading, SoupTag):
                label = heading.get_text(" ", strip=True) or "(未命名資料夾)"
                nested = child_dl(dt)
                if nested is not None:
                    yield from walk(nested, labels + [label])

    yield from walk(root, [])


def load_bookmarks(path: Path, format_hint: Optional[str] = None) -> Iterable[BookmarkRecord]:
    """Load a Chrome profile/export file based on format or extension."""
    raw = path.expanduser().read_text(encoding="utf-8")
    selected = (format_hint or path.suffix.lstrip(".") or "json").lower()
    if selected in {"html", "htm"}:
        return list(iter_netscape_bookmark_html(raw))
    payload = json.loads(raw)
    return list(iter_chrome_json_bookmarks(payload))


__all__ = [
    "BookmarkRecord",
    "chrome_time_to_iso",
    "iter_chrome_json_bookmarks",
    "iter_netscape_bookmark_html",
    "load_bookmarks",
]
