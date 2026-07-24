"""Compatibility entry point for registering Flowinone Flask adapters.

The source-specific routes live under ``src.flowinone.web``.  This module stays
small so existing ``from routes import register_routes`` callers keep working.
"""

from __future__ import annotations

from pathlib import Path

import click
from flask import Blueprint, Flask, current_app, render_template

from src.flowinone.catalog.blueprint import register_catalog
from src.flowinone.gallery.blueprint import register_gallery
from src.flowinone.resource_library.blueprint import register_resource_library
from src.flowinone.web import chrome, eagle, local, media
from src.flowinone.web.api import register_api_error_handlers
from src.flowinone.web.common import get_feature_flags
from src.flowinone.web.security import register_request_security


debug_bp = Blueprint("debug", __name__)


@debug_bp.get("/debug/")
def debug_print():
    rows = [
        {
            "author": "Lynn",
            "title": "Blog Post 1",
            "content": "First post content",
            "date_posted": "September 3, 2018",
        },
        {
            "author": "Lydia",
            "title": "Blog Post 2",
            "content": "Second post content",
            "date_posted": "September 6, 2018",
        },
    ]
    return render_template("test_arg.html", title="All in One", df_to_post=rows)


def register_routes(app: Flask) -> None:
    """Register source blueprints, domain blueprints, and CLI commands."""

    register_api_error_handlers(app)
    register_request_security(app)

    @app.context_processor
    def inject_feature_flags():
        return {"feature_flags": get_feature_flags()}

    app.register_blueprint(local.bp)
    app.register_blueprint(chrome.bp)
    app.register_blueprint(eagle.bp)
    app.register_blueprint(media.bp)
    local.register_commands(app)
    chrome.register_commands(app)

    register_resource_library(app)
    register_gallery(app)
    register_catalog(app)

    @app.cli.command("flowinone-doctor")
    def flowinone_doctor() -> None:
        """Check configuration and runtime dependencies without changing them."""
        import config
        from src.flowinone.resource_library.database import ensure_database_current

        checks = [
            ("external media root", config.DB_route_external),
            ("internal media root", config.DB_route_internal),
            (
                "resource database",
                current_app.config["FLOWINONE_RESOURCE_DB_PATH"],
            ),
        ]
        failed = False
        for label, raw_path in checks[:2]:
            path = Path(raw_path) if raw_path else None
            available = bool(path and path.is_dir())
            failed = failed or not available
            click.echo(f"{'OK' if available else 'FAIL'}  {label}: {path or '(unset)'}")
        database_path = Path(checks[2][1])
        try:
            ensure_database_current(database_path)
        except Exception as exc:
            failed = True
            click.echo(f"FAIL  resource database: {exc}")
        else:
            click.echo(f"OK  resource database: {database_path}")
        if failed:
            raise click.ClickException("Flowinone has configuration errors")


def register_routes_debug(app: Flask) -> None:
    app.register_blueprint(debug_bp)

__all__ = ["register_routes", "register_routes_debug"]
