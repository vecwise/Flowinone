"""Catalog maintenance commands, separate from HTTP handlers."""

from __future__ import annotations

from pathlib import Path

import click
from flask import Flask

from .artifacts import CatalogArtifactService
from .discovery import DiscoveryService
from .http import _database
from .query import CATALOG_SOURCES
from .similarity import CatalogSimilarityService
from .sync import CatalogSyncService


def register_catalog_commands(app: Flask) -> None:
    @app.cli.command("catalog-sync")
    @click.option("--source", "sources", multiple=True, type=click.Choice((*CATALOG_SOURCES, "all")), default=("all",))
    @click.option(
        "--full-rescan",
        is_flag=True,
        help="Discard any Eagle checkpoint and rebuild every Eagle projection.",
    )
    def catalog_sync(sources: tuple[str, ...], full_rescan: bool) -> None:
        selected = CATALOG_SOURCES if "all" in sources else sources
        click.echo(
            CatalogSyncService(_database()).sync(
                selected, full_rescan=full_rescan
            )
        )

    @app.cli.command("catalog-relations-rebuild")
    def catalog_relations_rebuild() -> None:
        click.echo(DiscoveryService(_database()).rebuild_item_relations())

    @app.cli.command("catalog-ocr")
    @click.argument("item_id")
    @click.argument("path", type=click.Path(path_type=Path, exists=True, dir_okay=False))
    def catalog_ocr(item_id: str, path: Path) -> None:
        click.echo(CatalogArtifactService(_database()).run_ocr(item_id, path))

    @app.cli.command("catalog-similarity-rebuild")
    @click.option("--force", is_flag=True, help="重新計算所有本機圖片，而不只變更過的檔案。")
    @click.option("--limit", default=20_000, type=click.IntRange(1, 50_000), show_default=True)
    def catalog_similarity_rebuild(force: bool, limit: int) -> None:
        click.echo(
            CatalogSimilarityService(_database()).analyze_local_images(
                limit=limit, force=force
            )
        )
