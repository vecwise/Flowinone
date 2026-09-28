"""Refresh Catalog projections after committed Resource changes."""

from __future__ import annotations

from src.flowinone.catalog.sync import CatalogSyncService

from .database import ResourceDatabase


def refresh_resource_catalog(database: ResourceDatabase, resource_id: str) -> None:
    CatalogSyncService(database).sync_resource(resource_id)


def refresh_all_resource_catalog(database: ResourceDatabase) -> None:
    CatalogSyncService(database).sync(("resources",))
