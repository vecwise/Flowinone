"""Resource import, worker, and database maintenance commands."""

from __future__ import annotations

from pathlib import Path

import click
from flask import Flask, current_app

from .database import backup_database, upgrade_database
from .http import _database
from .maintenance import rebuild_fts, retry_failed_jobs
from .service import ResourceService
from .worker import ResourceWorker


def register_resource_commands(app: Flask) -> None:
    @app.cli.command("resources-sync")
    @click.option("--path", "path_value", type=click.Path(path_type=Path), default=None)
    @click.option("--format", "format_hint", type=click.Choice(["json", "html"]), default=None)
    @click.option("--no-enqueue", is_flag=True, help="Import without enrichment jobs.")
    def resources_sync(path_value, format_hint, no_enqueue):
        """Import a Chrome profile/export into rendered Resources."""
        target = path_value or Path(current_app.config["CHROME_BOOKMARK_PATH"])
        summary = ResourceService(_database()).import_file(
            target,
            format_hint=format_hint,
            enqueue=not no_enqueue,
        )
        click.echo(summary.to_dict())

    @app.cli.command("resources-worker")
    @click.option("--limit", type=click.IntRange(min=1), default=None)
    def resources_worker(limit):
        """Process ready Resource enrichment jobs and exit when idle."""
        completed = ResourceWorker(_database()).run_until_idle(max_jobs=limit)
        click.echo(f"processed={completed}")

    @app.cli.command("resources-db-upgrade")
    def resources_db_upgrade():
        """Apply all database migrations."""
        database_path = Path(current_app.config["FLOWINONE_RESOURCE_DB_PATH"])
        backup_path = backup_database(database_path)
        if backup_path:
            click.echo(f"backup={backup_path}")
        upgrade_database(database_path)
        click.echo("resource database is at head")

    @app.cli.command("resources-rebuild-fts")
    def resources_rebuild_fts():
        """Rebuild Resource FTS rows from rendered metadata and extracted content."""
        click.echo(rebuild_fts(_database()))

    @app.cli.command("resources-retry-failed")
    def resources_retry_failed():
        """Reset failed enrichment jobs so the worker can retry them."""
        click.echo(retry_failed_jobs(_database()))
