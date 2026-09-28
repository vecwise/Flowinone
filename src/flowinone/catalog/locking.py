"""Recognize SQLite writer contention through wrapped exceptions."""

from __future__ import annotations

import sqlite3


def database_is_locked(exc: Exception) -> bool:
    """Return whether an exception chain represents SQLite BUSY/LOCKED."""
    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, sqlite3.Error):
            code = getattr(current, "sqlite_errorcode", None)
            if isinstance(code, int) and (code & 0xFF) in {
                sqlite3.SQLITE_BUSY,
                sqlite3.SQLITE_LOCKED,
            }:
                return True
            message = str(current).casefold()
            if (
                "database is locked" in message
                or "database table is locked" in message
            ):
                return True
        for nested in (
            getattr(current, "orig", None),
            current.__cause__,
            current.__context__,
        ):
            if isinstance(nested, BaseException):
                pending.append(nested)
    return False
