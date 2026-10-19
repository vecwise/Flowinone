"""Stable natural filename keys shared by SQL pagination and album covers."""

import re


def natural_sort_key(value):
    def number(match):
        digits = match.group().lstrip("0") or "0"
        return f"\x01{len(digits):06d}:{digits}\x00"

    return re.sub(r"[0-9]+", number, str(value or "").casefold())
