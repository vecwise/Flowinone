"""Load an optional machine-local thumbnail provider outside the package."""

from __future__ import annotations

import importlib.util
import logging
import os
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


def resolve_local_provider(url: str, fetch_html: Callable[[str], str]):
    module = load_local_provider()
    hook = getattr(module, "resolve", None) if module else None
    if not callable(hook):
        return None
    return hook(url, fetch_html)


__all__ = [
    "DEFAULT_LOCAL_PROVIDER_PATH",
    "LOCAL_PROVIDER_ENV",
    "classify_local_url",
    "load_local_provider",
    "resolve_local_provider",
]
