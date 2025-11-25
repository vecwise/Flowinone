"""Backward-compatible shim for Flowinone file handler.

Functions now live in src.file_handler; prefer importing from there directly.
"""

from src.file_handler import *  # noqa: F401,F403
