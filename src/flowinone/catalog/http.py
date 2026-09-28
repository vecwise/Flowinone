"""Shared Catalog Blueprint and request-local database access."""

from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path

from flask import Blueprint, current_app

from src.flowinone.config import DB_route_external, DB_route_internal
from src.flowinone.web.database import request_database

bp = Blueprint("catalog", __name__)


_database = request_database


def _duplicate_review_roots() -> list[str]:
    """Return the same configured Local roots used by media serving."""
    configured = current_app.config.get("FLOWINONE_MEDIA_ROOTS", ())
    if isinstance(configured, str):
        configured = configured.split(os.pathsep)
    return [
        str(Path(value).expanduser().resolve())
        for value in (DB_route_external, DB_route_internal, *configured)
        if value
    ]


def _reveal_duplicate_path(path: Path) -> None:
    """Ask the operating system to reveal one validated Local source file."""
    if platform.system() == "Darwin":
        subprocess.Popen(["open", "-R", str(path)])
    elif platform.system() == "Windows":
        subprocess.Popen(["explorer", "/select,", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])
