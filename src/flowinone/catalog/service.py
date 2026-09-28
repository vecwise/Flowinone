"""Stable imports for Catalog callers; implementations live by responsibility."""

from .query import CATALOG_SOURCES, CATALOG_SORTS, CatalogQuery
from .browse import CatalogService
from .eagle_sync import (
    EAGLE_SYNC_CURSOR_VERSION,
    EAGLE_SYNC_FINGERPRINT_VERSION,
    EAGLE_SYNC_PAGE_SIZE,
)
from .sync import (
    CATALOG_LOCK_MAX_RETRIES,
    CATALOG_LOCK_RETRY_BASE_DELAY,
    CATALOG_LOCK_RETRY_MAX_DELAY,
    CatalogSyncService,
)

__all__ = ["CATALOG_SOURCES", "CATALOG_SORTS", "CatalogQuery", "CatalogService", "CatalogSyncService"]
