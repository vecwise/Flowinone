"""Import Chrome JSON/HTML into the Flowinone Resource Library."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from config import CHROME_BOOKMARK_PATH  # noqa: E402
from src.flowinone.resource_library.service import ResourceService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", type=Path, default=Path(CHROME_BOOKMARK_PATH))
    parser.add_argument("--format", choices=("json", "html"), default=None)
    parser.add_argument("--no-enqueue", action="store_true")
    args = parser.parse_args()
    summary = ResourceService().import_file(
        args.path,
        format_hint=args.format,
        enqueue=not args.no_enqueue,
    )
    print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))
    return 0 if summary.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
