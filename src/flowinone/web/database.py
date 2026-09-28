"""Resolve the shared application database for a Flask request or CLI command."""

from __future__ import annotations

from pathlib import Path

from flask import current_app

from src.flowinone.resource_library.database import ResourceDatabase, get_resource_database


def request_database() -> ResourceDatabase:
    configured = current_app.config.get("FLOWINONE_RESOURCE_DB_PATH")
    return get_resource_database(
        Path(configured) if configured else None,
        migrate=bool(
            current_app.config.get("FLOWINONE_AUTO_MIGRATE", current_app.testing)
        ),
    )
