"""Rebuild the Resource Library FTS5 projection."""

from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.flowinone.resource_library.database import get_resource_database  # noqa: E402
from src.flowinone.resource_library.maintenance import rebuild_fts  # noqa: E402


if __name__ == "__main__":
    print(json.dumps(rebuild_fts(get_resource_database()), ensure_ascii=False, indent=2))
