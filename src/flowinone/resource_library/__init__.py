"""Resource ingestion, curation, search, and note-promotion domain."""

from .database import ResourceDatabase, get_resource_database
from .settings import ResourceSettings, get_resource_settings

__all__ = [
    "ResourceDatabase",
    "ResourceSettings",
    "get_resource_database",
    "get_resource_settings",
]
