"""Load optional machine-local media and thumbnail rules outside the package."""

from __future__ import annotations

import importlib.util
import logging
import os
import re
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Callable, Optional


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_LOCAL_PROVIDER_PATH = PROJECT_ROOT / ".flowinone_local" / "thumbnail_provider.py"
LOCAL_PROVIDER_ENV = "FLOWINONE_LOCAL_THUMBNAIL_PROVIDER"


@lru_cache(maxsize=1)
def load_local_provider() -> Optional[ModuleType]:
    """Load a local adapter by file path without including it in the package."""
    configured_path = os.environ.get(LOCAL_PROVIDER_ENV)
    provider_path = Path(configured_path).expanduser() if configured_path else DEFAULT_LOCAL_PROVIDER_PATH
    if not provider_path.is_file():
        return None

    spec = importlib.util.spec_from_file_location("flowinone_machine_local_thumbnail_provider", provider_path)
    if spec is None or spec.loader is None:
        LOGGER.warning("Could not create the machine-local thumbnail provider loader")
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:
        LOGGER.exception("Could not load the machine-local thumbnail provider")
        return None
    return module


def classify_local_url(url: str) -> Optional[str]:
    module = load_local_provider()
    hook = getattr(module, "classify_url", None) if module else None
    if not callable(hook):
        return None
    try:
        result = hook(url)
    except Exception:
        LOGGER.exception("Machine-local thumbnail URL classification failed")
        return None
    return str(result) if result else None


def local_media_url_rules() -> dict[str, tuple[tuple[str, str], ...]]:
    """Read optional browse rules as (exact host, SQL-LIKE path) pairs."""
    module = load_local_provider()
    hook = getattr(module, "media_url_rules", None) if module else None
    if not callable(hook):
        return {}
    try:
        rules = hook()
        result = {}
        for category, pairs in rules.items():
            if category not in {"video", "image", "album"}:
                raise ValueError("Unsupported local media category")
            validated = []
            for host, path in pairs:
                if not isinstance(host, str) or not re.fullmatch(r"[a-zA-Z0-9]+(?:[.-][a-zA-Z0-9]+)*", host):
                    raise ValueError("Local media rules require exact hostnames")
                if not isinstance(path, str) or not path.startswith("/"):
                    raise ValueError("Local media rules require absolute URL paths")
                validated.append((host.lower(), path.lower()))
            result[category] = tuple(validated)
        return result
    except Exception:
        LOGGER.exception("Could not load machine-local media rules")
        return {}


def resolve_local_provider(url: str, fetch_html: Callable[[str], str]):
    module = load_local_provider()
    hook = getattr(module, "resolve", None) if module else None
    if not callable(hook):
        return None
    return hook(url, fetch_html)


def canonicalize_local_url(url: str) -> Optional[str]:
    module = load_local_provider()
    hook = getattr(module, "canonicalize_url", None) if module else None
    if not callable(hook):
        return None
    try:
        value = hook(url)
        return str(value) if value else None
    except Exception:
        LOGGER.exception("Machine-local URL normalization failed")
        return None


def resolve_local_sources(url: str, media_id: str, store):
    module = load_local_provider()
    hook = getattr(module, "resolve_sources", None) if module else None
    return hook(url, media_id, store) if callable(hook) else None


def fetch_local_image(url: str, page_url: str, client):
    module = load_local_provider()
    hook = getattr(module, "fetch_image", None) if module else None
    return hook(url, page_url, client) if callable(hook) else None


__all__ = [
    "DEFAULT_LOCAL_PROVIDER_PATH",
    "LOCAL_PROVIDER_ENV",
    "classify_local_url",
    "load_local_provider",
    "local_media_url_rules",
    "resolve_local_provider",
    "canonicalize_local_url",
    "resolve_local_sources",
    "fetch_local_image",
]
