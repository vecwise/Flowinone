"""Stable, environment-backed filesystem locations shared by Flowinone."""

from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    """Return the absolute writable data directory, independent of cwd."""
    configured = os.environ.get("FLOWINONE_DATA_DIR", "").strip()
    return (
        Path(configured).expanduser().resolve()
        if configured
        else (PROJECT_ROOT / "data").resolve()
    )


def item_database_path() -> Path:
    configured = os.environ.get("FLOWINONE_ITEM_DB_PATH", "").strip()
    return (
        Path(configured).expanduser().resolve()
        if configured
        else data_dir() / "item_db.db"
    )


def thumbnail_cache_database_path() -> Path:
    configured = os.environ.get("FLOWINONE_THUMBNAIL_DB_PATH", "").strip()
    return (
        Path(configured).expanduser().resolve()
        if configured
        else data_dir() / "cache.db"
    )


__all__ = [
    "PROJECT_ROOT",
    "data_dir",
    "item_database_path",
    "thumbnail_cache_database_path",
]
