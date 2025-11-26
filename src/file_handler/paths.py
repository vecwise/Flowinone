"""Path, URL, and type helpers for Flowinone file handling."""

import os
from urllib.parse import quote

from .models import AccessDenied, FolderNotFound


IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
VIDEO_EXTENSIONS = {"mp4", "mov", "avi", "mkv", "webm", "m4v"}
DEFAULT_THUMBNAIL_ROUTE = "/static/default_thumbnail.svg"
DEFAULT_VIDEO_THUMBNAIL_ROUTE = "/static/default_video_thumbnail.svg"


def _normalize_source(src):
    return "external" if src == "external" else "internal"


def _normalize_slashes(path):
    return path.replace("\\", "/")


def _is_image_file(filename):
    return os.path.splitext(filename)[1].lower().lstrip(".") in IMAGE_EXTENSIONS


def _is_video_file(filename):
    return os.path.splitext(filename)[1].lower().lstrip(".") in VIDEO_EXTENSIONS


def _build_file_route(abs_path, src):
    normalized = _normalize_slashes(abs_path)
    quoted = quote(normalized, safe="/:")
    if src == "external":
        return f"/serve_image/{quoted}"
    return f"/{quoted}"


def _build_image_url(rel_path, src):
    normalized_src = _normalize_source(src)
    normalized_path = _normalize_slashes(rel_path or "")
    quoted_path = quote(normalized_path, safe="/")
    query = "?src=external" if normalized_src == "external" else "?src=internal"
    if quoted_path:
        return f"/image/{quoted_path}{query}"
    return f"/image/{query}"


def _build_folder_url(rel_path, src):
    normalized_src = _normalize_source(src)
    normalized_path = _normalize_slashes(rel_path or "")
    quoted_path = quote(normalized_path, safe="/")

    if normalized_src == "external":
        return f"/both/{quoted_path}" if quoted_path else "/"

    if quoted_path:
        return f"/both/{quoted_path}/?src=internal"
    return "/?src=internal"


def _build_video_url(rel_path, src):
    normalized = _normalize_slashes(rel_path)
    quoted_path = quote(normalized, safe="/")
    query = "?src=external" if _normalize_source(src) == "external" else "?src=internal"
    return f"/video/{quoted_path}{query}"


def _find_video_thumbnail(abs_video_path, src):
    base, _ = os.path.splitext(abs_video_path)
    candidates = []
    for ext in IMAGE_EXTENSIONS:
        candidates.append(f"{base}_thumbnail.{ext}")
        candidates.append(f"{base}.{ext}")

    for candidate in candidates:
        if os.path.isfile(candidate):
            return _build_file_route(candidate, src)

    return DEFAULT_VIDEO_THUMBNAIL_ROUTE


def _find_directory_thumbnail(abs_folder_path, src):
    for root, _, files in os.walk(abs_folder_path):
        for file_name in sorted(files):
            abs_file_path = os.path.join(root, file_name)
            if _is_image_file(file_name):
                return _build_file_route(abs_file_path, src)
            if _is_video_file(file_name):
                return _find_video_thumbnail(abs_file_path, src)
    return DEFAULT_THUMBNAIL_ROUTE


def _collect_directory_entries(base_dir, relative_path, src,
                               folder_builder, image_builder, video_builder):
    normalized_src = _normalize_source(src)
    target_dir = os.path.join(base_dir, relative_path) if relative_path else base_dir
    if not os.path.isdir(target_dir):
        raise FolderNotFound(f"Directory not found: {target_dir}")

    try:
        entries = sorted(os.listdir(target_dir))
    except FileNotFoundError:
        raise FolderNotFound(f"Directory not found: {target_dir}")

    folders, files = [], []
    for entry in entries:
        if entry.startswith("."):
            continue
        abs_entry = os.path.join(target_dir, entry)
        rel_entry = os.path.relpath(abs_entry, base_dir)
        rel_entry = _normalize_slashes(rel_entry)

        if os.path.isdir(abs_entry):
            folders.append(folder_builder(entry, abs_entry, rel_entry, normalized_src))
        elif _is_image_file(entry):
            files.append(image_builder(entry, abs_entry, rel_entry, normalized_src))
        elif _is_video_file(entry):
            files.append(video_builder(entry, abs_entry, rel_entry, normalized_src))

    return folders + files


def _human_readable_size(num_bytes):
    if num_bytes < 1024:
        return f"{num_bytes} B"
    units = ["KB", "MB", "GB", "TB", "PB"]
    size = float(num_bytes)
    for unit in units:
        size /= 1024.0
        if size < 1024.0 or unit == units[-1]:
            return f"{size:.2f} {unit}"

    return f"{size:.2f} PB"


def _safe_relative_path(path):
    if not path:
        return ""
    normalized = _normalize_slashes(os.path.normpath(path))
    if normalized.startswith("../") or normalized == "..":
        raise AccessDenied(f"Path escapes base directory: {path}")
    if os.path.isabs(path):
        raise AccessDenied(f"Absolute paths are not allowed: {path}")
    return normalized
