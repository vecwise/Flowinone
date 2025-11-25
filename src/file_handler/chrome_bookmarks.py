"""Chrome bookmarks and focus-mode handling for Flowinone."""

import json
import os
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote, quote_plus

from config import CHROME_BOOKMARK_PATH
from .media_cache import CACHE_DATA_DIR, cache_thumbnail_for_bookmark, extract_youtube_id
from .models import BookmarkError, BookmarkNotFound, MediaEntry, PageMetadata
from .paths import DEFAULT_THUMBNAIL_ROUTE


FOCUS_MODES_FILE = Path(CACHE_DATA_DIR) / "focus_modes.json"
_DEFAULT_FOCUS_CONFIG = {
    "default_mode": "all",
    "modes": [
        {
            "id": "all",
            "label": "全部書籤",
            "description": "顯示所有 Chrome 書籤"
        }
    ]
}


def _load_or_create_focus_config() -> dict:
    """
    Ensure the focus-mode configuration exists on disk and return its raw content.
    """
    try:
        FOCUS_MODES_FILE.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return json.loads(json.dumps(_DEFAULT_FOCUS_CONFIG))

    if not FOCUS_MODES_FILE.exists():
        try:
            FOCUS_MODES_FILE.write_text(
                json.dumps(_DEFAULT_FOCUS_CONFIG, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except OSError:
            return json.loads(json.dumps(_DEFAULT_FOCUS_CONFIG))
        return json.loads(json.dumps(_DEFAULT_FOCUS_CONFIG))

    try:
        return json.loads(FOCUS_MODES_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return json.loads(json.dumps(_DEFAULT_FOCUS_CONFIG))


def _sanitize_focus_config(raw_config: dict) -> Dict[str, object]:
    """
    Normalise the focus-mode configuration to a predictable structure.
    """
    modes: List[Dict[str, object]] = []
    seen: set[str] = set()

    raw_modes = raw_config.get("modes") if isinstance(raw_config, dict) else None
    if not isinstance(raw_modes, list):
        raw_modes = []

    for entry in raw_modes:
        if not isinstance(entry, dict):
            continue

        mode_id = str(entry.get("id") or "").strip()
        if not mode_id:
            continue

        mode_key = mode_id.lower()
        if mode_key in seen:
            continue

        seen.add(mode_key)

        label = entry.get("label") or mode_id
        description = entry.get("description")

        def _collect_strings(values):
            if not isinstance(values, list):
                return []
            collected = []
            for value in values:
                if isinstance(value, str):
                    stripped = value.strip()
                    if stripped:
                        collected.append(stripped)
            return collected

        keywords = _collect_strings(entry.get("keywords"))
        folders = _collect_strings(entry.get("folders"))
        include_urls = _collect_strings(entry.get("include_urls"))
        exclude_keywords = _collect_strings(entry.get("exclude_keywords"))
        exclude_urls = _collect_strings(entry.get("exclude_urls"))

        modes.append({
            "id": mode_key,
            "label": label,
            "description": description,
            "keywords": keywords,
            "keywords_lower": [value.lower() for value in keywords],
            "folders": folders,
            "folders_lower": [value.lower() for value in folders],
            "include_urls": include_urls,
            "include_urls_lower": [value.lower() for value in include_urls],
            "exclude_keywords": exclude_keywords,
            "exclude_keywords_lower": [value.lower() for value in exclude_keywords],
            "exclude_urls": exclude_urls,
            "exclude_urls_lower": [value.lower() for value in exclude_urls],
        })

    if "all" not in seen:
        modes.insert(0, {
            "id": "all",
            "label": "全部書籤",
            "description": "顯示所有 Chrome 書籤",
            "keywords": [],
            "keywords_lower": [],
            "folders": [],
            "folders_lower": [],
            "include_urls": [],
            "include_urls_lower": [],
            "exclude_keywords": [],
            "exclude_keywords_lower": [],
            "exclude_urls": [],
            "exclude_urls_lower": [],
        })
        seen.add("all")

    default_mode_raw = raw_config.get("default_mode") if isinstance(raw_config, dict) else "all"
    default_mode = str(default_mode_raw or "all").strip().lower()
    if default_mode not in seen:
        default_mode = "all"

    return {
        "default_mode": default_mode,
        "modes": modes
    }


def _get_focus_mode_config() -> Dict[str, object]:
    """
    Return the normalised focus-mode configuration.
    """
    raw_config = _load_or_create_focus_config()
    return _sanitize_focus_config(raw_config)


def _build_focus_matcher(mode: Dict[str, object]):
    """
    Build a predicate that checks whether a bookmark matches the supplied focus mode.
    """
    keywords = mode.get("keywords_lower", []) or []
    folder_terms = mode.get("folders_lower", []) or []
    include_urls = mode.get("include_urls_lower", []) or []
    exclude_keywords = mode.get("exclude_keywords_lower", []) or []
    exclude_urls = mode.get("exclude_urls_lower", []) or []

    def _match(name: str = "",
               url: Optional[str] = None,
               description: Optional[str] = None,
               folder_labels: Optional[List[str]] = None) -> bool:
        url_lower = (url or "").lower()
        labels = folder_labels or []
        path_blob = " / ".join(label.lower() for label in labels if label)
        text_blob_parts = [name or ""]
        if description:
            text_blob_parts.append(description)
        if path_blob:
            text_blob_parts.append(path_blob)
        text_blob = " ".join(part.lower() for part in text_blob_parts if part)

        if exclude_urls and any(token in url_lower for token in exclude_urls):
            return False
        if exclude_keywords and any(token in text_blob for token in exclude_keywords):
            return False

        positive_checks = []
        if keywords:
            positive_checks.append(any(token in text_blob for token in keywords))
        if folder_terms:
            positive_checks.append(any(token in path_blob for token in folder_terms))
        if include_urls:
            positive_checks.append(any(token in url_lower for token in include_urls))

        if not positive_checks:
            return True

        return any(positive_checks)

    return _match


def _count_focus_matches(node: dict,
                         matcher,
                         parent_labels: List[str],
                         cache: Dict[str, int]) -> int:
    """
    Count the number of bookmarks within a node (recursively) that match the focus mode.
    """
    node_id = node.get("id") or "|".join(parent_labels + [node.get("name") or ""])
    if node_id in cache:
        return cache[node_id]

    node_name = node.get("name") or "(未命名資料夾)"
    current_labels = parent_labels + [node_name]
    total = 0

    for child in node.get("children", []) or []:
        child_type = child.get("type")
        if child_type == "url":
            url = child.get("url") or ""
            title = child.get("name") or url
            if matcher(title, url=url, folder_labels=current_labels):
                total += 1
        elif child_type == "folder":
            total += _count_focus_matches(child, matcher, current_labels, cache)

    cache[node_id] = total
    return total


def _load_chrome_bookmarks():
    if not os.path.exists(CHROME_BOOKMARK_PATH):
        raise BookmarkNotFound(f"Chrome bookmark file missing: {CHROME_BOOKMARK_PATH}")
    try:
        with open(CHROME_BOOKMARK_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        raise BookmarkError(f"Failed to read Chrome bookmarks data: {exc}") from exc


def _find_chrome_node(bookmarks_root, path_parts):
    roots = bookmarks_root.get("roots", {}) if bookmarks_root else {}
    if not path_parts:
        return None, None, None

    first = path_parts[0]
    current = roots.get(first)
    if current is None:
        raise BookmarkNotFound(f"Root node not found: {first}")

    parent = None
    parent_path = ""
    current_path = first

    for part in path_parts[1:]:
        if current.get("type") != "folder":
            raise BookmarkNotFound("Parent is not a folder")
        next_node = None
        for child in current.get("children", []):
            if child.get("id") == part:
                next_node = child
                break
        if next_node is None:
            raise BookmarkNotFound(f"Folder not found: {part}")
        parent = current
        parent_path = current_path
        current = next_node
        current_path = f"{current_path}/{part}"

    return current, parent, parent_path


def get_chrome_bookmarks(folder_path=None, focus_mode_id: Optional[str] = None):
    bookmarks = _load_chrome_bookmarks()
    roots = bookmarks.get("roots", {})
    safe_path = (folder_path or "bookmark_bar").strip("/")

    if not roots:
        raise BookmarkNotFound("No Chrome bookmark roots found")

    parts = [part for part in safe_path.split("/") if part]
    if not parts:
        raise BookmarkNotFound("Invalid bookmark path")

    current, parent, parent_path = _find_chrome_node(bookmarks, parts)
    if current is None:
        raise BookmarkNotFound("Bookmark node not found")

    focus_config = _get_focus_mode_config()
    focus_modes: List[Dict[str, object]] = focus_config.get("modes", [])  # type: ignore[assignment]
    if not focus_modes:
        focus_modes = _sanitize_focus_config(_DEFAULT_FOCUS_CONFIG)["modes"]

    mode_lookup = {mode["id"]: mode for mode in focus_modes}
    requested_mode = (focus_mode_id or "").strip().lower()
    default_mode_id = str(focus_config.get("default_mode") or "all").strip().lower()
    active_mode = mode_lookup.get(requested_mode) or mode_lookup.get(default_mode_id) or focus_modes[0]
    active_mode_id = active_mode["id"]
    matcher = None if active_mode_id == "all" else _build_focus_matcher(active_mode)

    query_suffix = f"?mode={quote_plus(active_mode_id)}" if active_mode_id else ""
    base_path = f"/chrome/{quote(safe_path, safe='/')}"

    breadcrumb = []
    breadcrumb_labels: List[str] = []
    path_cursor: List[str] = []

    root_node = roots.get(parts[0])
    if root_node is None:
        raise BookmarkNotFound(f"Bookmark root missing: {parts[0]}")

    current_node = root_node
    for idx, part in enumerate(parts):
        if idx == 0:
            node_label = root_node.get("name") or ("Bookmarks" if part == "bookmark_bar" else part)
        else:
            if current_node.get("type") != "folder":
                raise BookmarkNotFound("Unexpected non-folder node")
            next_node = None
            for child in current_node.get("children", []) or []:
                if child.get("id") == part:
                    next_node = child
                    break
            if next_node is None:
                raise BookmarkNotFound(f"Bookmark folder not found: {part}")
            current_node = next_node
            node_label = current_node.get("name") or "(未命名資料夾)"

        path_cursor.append(part)
        breadcrumb.append({
            "name": node_label or "(未命名資料夾)",
            "url": f"/chrome/{quote('/'.join(path_cursor), safe='/')}{query_suffix}"
        })
        breadcrumb_labels.append(node_label or "(未命名資料夾)")

    current_name = current.get("name") or (breadcrumb_labels[-1] if breadcrumb_labels else "(未命名資料夾)")

    metadata = PageMetadata(
        name=current_name,
        category="chrome",
        tags=["chrome", "bookmarks"],
        path=base_path,
        thumbnail_route=DEFAULT_THUMBNAIL_ROUTE,
        filesystem_path=CHROME_BOOKMARK_PATH,
        folders=breadcrumb
    )

    if active_mode_id != "all":
        metadata.tags.append(f"focus:{active_mode_id}")

    children = current.get("children", []) or []
    data: List[MediaEntry] = []
    match_cache: Dict[str, int] = {}

    parent_labels_for_current = breadcrumb_labels[:-1] if breadcrumb_labels else []
    total_focus_matches = None
    if matcher:
        total_focus_matches = _count_focus_matches(current, matcher, parent_labels_for_current, match_cache)

    direct_bookmark_total = 0
    direct_bookmark_matches = 0

    for child in children:
        child_type = child.get("type")
        if child_type == "folder":
            child_name = child.get("name") or "(未命名資料夾)"
            child_id = child.get("id")
            if not child_id:
                continue
            child_path = f"{safe_path}/{child_id}"
            folder_labels = breadcrumb_labels + [child_name]
            description = None

            if matcher:
                focus_count = _count_focus_matches(child, matcher, breadcrumb_labels, match_cache)
                if focus_count <= 0:
                    continue
                description = f"{focus_count} 個專注書籤"

            data.append(MediaEntry(
                name=child_name,
                thumbnail_route=DEFAULT_THUMBNAIL_ROUTE,
                url=f"/chrome/{quote(child_path, safe='/')}{query_suffix}",
                item_path=None,
                media_type="folder",
                description=description,
                folder_labels=folder_labels,
                path_display=" / ".join(folder_labels)
            ))
        elif child_type == "url":
            url = child.get("url")
            if not url:
                continue

            direct_bookmark_total += 1

            child_name = child.get("name") or url
            folder_labels = list(breadcrumb_labels)
            path_display = " / ".join(folder_labels)

            matches_focus = True
            if matcher:
                matches_focus = matcher(child_name, url=url, folder_labels=folder_labels, description=path_display)

            if not matches_focus:
                continue

            direct_bookmark_matches += 1

            folder_meta = {"folder_path": path_display}
            thumbnail, sub_type = cache_thumbnail_for_bookmark(url, child_name, folder_meta)
            thumbnail = thumbnail or DEFAULT_THUMBNAIL_ROUTE

            data.append(MediaEntry(
                name=child_name,
                thumbnail_route=thumbnail,
                url=url,
                item_path=url,
                media_type="bookmark",
                ext=sub_type,
                description=path_display or None,
                folder_labels=folder_labels,
                path_display=path_display
            ))

    focus_options = []
    for mode in focus_modes:
        mode_id = mode["id"]
        focus_options.append({
            "id": mode_id,
            "label": mode.get("label"),
            "description": mode.get("description"),
            "is_active": mode_id == active_mode_id,
            "url": f"{base_path}?mode={quote_plus(mode_id)}"
        })

    metadata.focus_modes = focus_options
    metadata.focus_mode = {
        "id": active_mode_id,
        "label": active_mode.get("label"),
        "description": active_mode.get("description")
    }

    focus_stats = {
        "mode_id": active_mode_id,
        "mode_label": active_mode.get("label"),
        "total_bookmarks": direct_bookmark_total,
        "matched_bookmarks": direct_bookmark_matches,
        "matched_including_subfolders": total_focus_matches,
        "has_results": any(
            (item.media_type if hasattr(item, "media_type") else item.get("media_type")) == "bookmark"
            for item in data
        )
    }
    metadata.focus_stats = focus_stats

    if matcher:
        summary_bits = [
            f"本層 {direct_bookmark_matches} / {direct_bookmark_total} 筆"
        ]
        if total_focus_matches is not None:
            summary_bits.append(f"含子層 {total_focus_matches} 筆")
        if not focus_stats["has_results"]:
            summary_bits.append("目前沒有符合的書籤")
        metadata.description = f"🎯 {active_mode.get('label')} 專注模式｜" + "，".join(summary_bits)

    return metadata, data


def get_chrome_youtube_bookmarks():
    bookmarks = _load_chrome_bookmarks()
    roots = bookmarks.get("roots", {})
    results: List[MediaEntry] = []

    def _walk(node, path_labels):
        node_type = node.get("type")
        if node_type == "folder":
            label = node.get("name") or "(未命名資料夾)"
            new_path = path_labels + [label]
            for child in node.get("children", []):
                _walk(child, new_path)
        elif node_type == "url":
            url = node.get("url")
            video_id = extract_youtube_id(url)
            if not video_id:
                return
            label = node.get("name") or url
            folder_meta = {"folder_path": " / ".join(filter(None, path_labels))}
            thumbnail, sub_type = cache_thumbnail_for_bookmark(url, label, folder_meta)
            thumbnail = thumbnail or DEFAULT_THUMBNAIL_ROUTE
            sub_type = sub_type or "youtube"
            results.append(MediaEntry(
                name=label,
                thumbnail_route=thumbnail,
                url=url,
                item_path=url,
                media_type="bookmark",
                ext=sub_type,
                description=folder_meta.get("folder_path")
            ))

    for key in ["bookmark_bar", "other", "synced", "mobile"]:
        node = roots.get(key)
        if not node:
            continue
        root_label = node.get("name") or key.replace("_", " ").title()
        _walk(node, [root_label])

    metadata = PageMetadata(
        name="YouTube 書籤",
        category="chrome-youtube",
        tags=["chrome", "youtube", "bookmarks"],
        path="/chrome_youtube/",
        thumbnail_route=DEFAULT_THUMBNAIL_ROUTE,
        filesystem_path=CHROME_BOOKMARK_PATH
    )

    return metadata, results


def has_chrome_bookmarks() -> bool:
    return os.path.isfile(CHROME_BOOKMARK_PATH)
