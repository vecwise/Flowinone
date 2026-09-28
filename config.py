"""Compatibility alias for the application configuration module."""

import sys

from src.flowinone import config as _config

# Keep mutable settings shared with callers that still import ``config``.
sys.modules[__name__] = _config
