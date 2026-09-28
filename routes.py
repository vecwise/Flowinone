"""Compatibility imports for existing ``routes`` callers."""

from src.flowinone.web.registration import (
    debug_bp,
    debug_print,
    register_routes,
    register_routes_debug,
)

__all__ = ["register_routes", "register_routes_debug"]
