"""Standalone bookmark preview/apply CLI. Preview never starts the Flask app."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from src.flowinone.bookmarks.backfill import apply_plan, build_plan, digest  # noqa: E402
from src.flowinone.bookmarks.snapshot import read_snapshot  # noqa: E402


def preview(database: Path, bookmarks: Path, baseline: dict, expected_count: int | None = None) -> dict:
    memory, metadata = read_snapshot(database)
    engine = create_engine("sqlite://", creator=lambda: memory, poolclass=StaticPool)
    try:
        with engine.connect() as conn:
            plan = build_plan(conn, bookmarks, baseline, expected_count=expected_count)
        plan["snapshot"] = metadata
        return plan
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("preview", "apply"))
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--bookmarks", required=True, type=Path)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--expected-count", type=int)
    parser.add_argument("--output", type=Path, help="New preview JSON; never a source path")
    parser.add_argument("--plan", type=Path, help="Reviewed JSON for explicit apply")
    parser.add_argument("--confirm-plan", help="Exact approval_digest from the reviewed plan")
    args = parser.parse_args()
    try:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        fresh = preview(args.database, args.bookmarks, baseline, args.expected_count)
        if args.operation == "preview":
            if args.output:
                targets = {args.database.resolve(), args.bookmarks.resolve(), args.baseline.resolve()}
                targets.update(Path(str(args.database.resolve()) + suffix) for suffix in ("-wal", "-shm", "-journal"))
                if args.output.resolve() in targets or args.output.exists():
                    raise ValueError("Preview output must be a new file distinct from all inputs")
                descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                    output.write(json.dumps(fresh, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps({"counts": fresh["counts"], "approval_digest": fresh["approval_digest"],
                              "output": str(args.output) if args.output else None}, ensure_ascii=False, indent=2))
            return 0
        if args.plan is None or not args.confirm_plan:
            raise ValueError("Apply requires --plan and explicit --confirm-plan; preview performs no writes")
        approved = json.loads(args.plan.read_text(encoding="utf-8"))
        payload = {key: value for key, value in approved.items() if key not in {"approval_digest", "snapshot"}}
        if digest(payload) != approved["approval_digest"] or args.confirm_plan != approved["approval_digest"] or fresh["approval_digest"] != approved["approval_digest"] or fresh["conflicts"]:
            raise ValueError("Approval does not match current data; no database writes performed")
        # These imports and the first source SQLite connection occur only in
        # this explicit apply branch, after validating the approved preview.
        from src.flowinone.resource_library.database import ResourceDatabase, backup_database, upgrade_database
        backup = backup_database(args.database)
        upgrade_database(args.database)
        database = ResourceDatabase(args.database, migrate=False)
        try:
            with database.engine.connect() as conn:
                conn.exec_driver_sql("BEGIN IMMEDIATE")
                try:
                    result = apply_plan(conn, approved, args.bookmarks, baseline)
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
        finally:
            database.engine.dispose()
        print(json.dumps({"applied": result, "backup": str(backup)}, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
