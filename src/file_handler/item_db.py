"""Centralised item database and filesystem crawler for tagged media folders."""

import hashlib
import json
import mimetypes
import os
import sqlite3
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from config import DB_route_external
from .paths import _is_image_file, _is_video_file, _normalize_slashes


ITEM_DB_PATH = os.path.join("data", "item_db.db")


@dataclass
class ItemRecord:
    item_id: str
    name: str
    data_source: str
    item_type: str
    tags: List[str]
    relative_path: str
    absolute_path: str
    library_root: str
    ext: Optional[str]
    mime_type: Optional[str]
    size_bytes: Optional[int]
    is_archived: bool = False
    thumbnail_route: Optional[str] = None
    actors: Optional[List[str]] = None
    authors: Optional[List[str]] = None
    face_ids: Optional[List[str]] = None
    region: Optional[str] = None
    rating: Optional[float] = None
    is_censored: Optional[bool] = None


def _ensure_item_db() -> None:
    """Create the SQLite DB and base schema if missing."""
    os.makedirs(os.path.dirname(ITEM_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(ITEM_DB_PATH)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS items (
                item_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                data_source TEXT NOT NULL,
                item_type TEXT NOT NULL,
                tags TEXT NOT NULL,
                actors TEXT,
                authors TEXT,
                face_ids TEXT,
                is_archived INTEGER NOT NULL DEFAULT 0,
                thumbnail_route TEXT,
                region TEXT,
                rating REAL,
                is_censored INTEGER,
                relative_path TEXT NOT NULL,
                absolute_path TEXT NOT NULL,
                library_root TEXT NOT NULL,
                ext TEXT,
                mime_type TEXT,
                size_bytes INTEGER,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_items_library_path
            ON items(library_root, relative_path)
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_items_type ON items(item_type)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_items_tags ON items(tags)"
        )
    finally:
        conn.commit()
        conn.close()


def _get_db_connection():
    _ensure_item_db()
    conn = sqlite3.connect(ITEM_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _clean_tag(tag_name: str) -> Optional[str]:
    cleaned = tag_name.lstrip("#").strip()
    return cleaned or None


def _extract_tags(tag_directory: str, base_dir: str) -> List[str]:
    """Extract stacked tags from base_dir up to the current tag directory."""
    relative = os.path.relpath(tag_directory, base_dir)
    parts = _normalize_slashes(relative).split("/")
    tags = []
    for part in parts:
        if part.startswith("#"):
            cleaned = _clean_tag(part)
            if cleaned:
                tags.append(cleaned)
    return tags


def _compute_item_id(library_root: str, relative_path: str) -> str:
    """Stable ID for an item, based on library root and relative path."""
    base = f"{os.path.abspath(library_root)}::{_normalize_slashes(relative_path)}"
    return hashlib.sha1(base.encode("utf-8", "ignore")).hexdigest()


def _detect_item_type(name: str, abs_path: str) -> str:
    if os.path.isdir(abs_path):
        return "folder"
    if _is_image_file(name):
        return "image"
    if _is_video_file(name):
        return "video"
    return "file"


def _build_item_record(
    entry_name: str,
    abs_path: str,
    relative_path: str,
    base_dir: str,
    tags: List[str],
) -> ItemRecord:
    ext = os.path.splitext(entry_name)[1].lstrip(".").lower() or None
    mime_type, _ = mimetypes.guess_type(entry_name)
    size_bytes = os.path.getsize(abs_path) if os.path.isfile(abs_path) else None
    return ItemRecord(
        item_id=_compute_item_id(base_dir, relative_path),
        name=entry_name,
        data_source="filesystem",
        item_type=_detect_item_type(entry_name, abs_path),
        tags=tags,
        relative_path=_normalize_slashes(relative_path),
        absolute_path=os.path.abspath(abs_path),
        library_root=os.path.abspath(base_dir),
        ext=ext,
        mime_type=mime_type,
        size_bytes=size_bytes,
    )


def iter_tagged_items(base_dir: str) -> Iterable[ItemRecord]:
    """Yield ItemRecord objects for every item found under tagged folders."""
    if not base_dir:
        raise FileNotFoundError("Base directory is not configured.")
    base_dir = os.path.abspath(base_dir)
    if not os.path.isdir(base_dir):
        raise FileNotFoundError(base_dir)

    for root, _, _ in os.walk(base_dir):
        if not os.path.basename(root).startswith("#"):
            continue

        tags = _extract_tags(root, base_dir)
        try:
            entries = os.listdir(root)
        except (FileNotFoundError, PermissionError):
            continue

        for entry in entries:
            if entry.startswith(".") or entry.startswith("#"):
                continue
            abs_entry = os.path.join(root, entry)
            rel_entry = os.path.relpath(abs_entry, base_dir)
            yield _build_item_record(entry, abs_entry, rel_entry, base_dir, tags)


def _serialise_optional_list(values: Optional[List[str]]) -> Optional[str]:
    if not values:
        return None
    return json.dumps(values, ensure_ascii=False)


def update_item_database(base_dir: Optional[str] = None) -> Dict[str, object]:
    """Crawl tagged folders and persist new items into the central DB.

    Existing entries (matched by library_root + relative_path) are left untouched.
    """
    target_dir = base_dir or DB_route_external
    records = list(iter_tagged_items(target_dir))

    inserted = 0
    skipped = 0
    errors: List[Tuple[str, str]] = []

    with _get_db_connection() as conn:
        for record in records:
            try:
                cur = conn.execute(
                    """
                    INSERT INTO items (
                        item_id, name, data_source, item_type, tags,
                        actors, authors, face_ids, is_archived, thumbnail_route,
                        region, rating, is_censored, relative_path, absolute_path,
                        library_root, ext, mime_type, size_bytes, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(item_id) DO NOTHING
                    """,
                    (
                        record.item_id,
                        record.name,
                        record.data_source,
                        record.item_type,
                        json.dumps(record.tags, ensure_ascii=False),
                        _serialise_optional_list(record.actors),
                        _serialise_optional_list(record.authors),
                        _serialise_optional_list(record.face_ids),
                        1 if record.is_archived else 0,
                        record.thumbnail_route,
                        record.region,
                        record.rating,
                        1 if record.is_censored else 0 if record.is_censored is not None else None,
                        record.relative_path,
                        record.absolute_path,
                        record.library_root,
                        record.ext,
                        record.mime_type,
                        record.size_bytes,
                    ),
                )
                inserted += 1 if cur.rowcount == 1 else 0
                skipped += 0 if cur.rowcount == 1 else 1
            except sqlite3.DatabaseError as exc:  # pragma: no cover - defensive
                errors.append((record.relative_path, str(exc)))
        conn.commit()

    return {
        "base_dir": os.path.abspath(target_dir),
        "seen": len(records),
        "inserted": inserted,
        "skipped": skipped,
        "errors": errors,
        "db_path": os.path.abspath(ITEM_DB_PATH),
    }


__all__ = ["ItemRecord", "ITEM_DB_PATH", "iter_tagged_items", "update_item_database"]
