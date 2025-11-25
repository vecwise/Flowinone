"""Shared data models and domain exceptions for Flowinone file handling."""

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional


class MediaError(Exception):
    """Base exception for media domain errors."""


class MediaNotFound(MediaError):
    """Requested media item does not exist."""


class FolderNotFound(MediaError):
    """Requested folder does not exist."""


class AccessDenied(MediaError):
    """The requested path is not allowed."""


class ExternalServiceError(MediaError):
    """Upstream dependency failed (Eagle, Chrome, etc.)."""


class BookmarkError(MediaError):
    """Base exception for bookmark-related issues."""


class BookmarkNotFound(BookmarkError):
    """Bookmark folder or entry not found."""


@dataclass
class MediaDetail:
    name: str
    relative_path: str
    source_url: str
    thumbnail_route: str
    mime_type: str
    size_bytes: int
    size_display: str
    modified_time: str
    download_url: str
    original_url: Optional[str] = None
    parent_url: Optional[str] = None
    folders: Optional[List[Dict[str, Any]]] = None
    ext: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class MediaEntry:
    name: str
    url: str
    thumbnail_route: str
    media_type: str  # "folder" | "image" | "video" | "bookmark" | ...
    item_path: Optional[str] = None
    path: Optional[str] = None
    ext: Optional[str] = None
    description: Optional[str] = None
    folder_labels: Optional[List[str]] = None
    path_display: Optional[str] = None
    id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict, omitting empty optional values."""
        data = {k: v for k, v in asdict(self).items() if v is not None}
        if "path" not in data and "url" in data:
            data["path"] = data["url"]
        return data


@dataclass
class PageMetadata:
    name: str
    category: str
    tags: List[str]
    path: str
    thumbnail_route: str
    filesystem_path: Optional[str] = None
    folders: Optional[List[Dict[str, Any]]] = None
    similar: Optional[List[Dict[str, Any]]] = None
    description: Optional[str] = None
    ext: Optional[str] = None
    focus_modes: Optional[List[Dict[str, Any]]] = None
    focus_mode: Optional[Dict[str, Any]] = None
    focus_stats: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict, omitting empty optional values."""
        return {k: v for k, v in asdict(self).items() if v is not None}


__all__ = [
    "AccessDenied",
    "BookmarkError",
    "BookmarkNotFound",
    "ExternalServiceError",
    "FolderNotFound",
    "MediaEntry",
    "MediaError",
    "MediaDetail",
    "MediaNotFound",
    "PageMetadata",
]
