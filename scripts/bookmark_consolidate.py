"""Preview or explicitly apply reviewed Chrome URL-owner pairs, without backups.

Stop Web/Worker before apply. Normal synchronization never calls this command.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from src.flowinone.bookmarks.consolidate import apply_plan, build_plan
from src.flowinone.bookmarks.snapshot import read_snapshot


def preview(database: Path, bookmarks: Path, selection: dict) -> dict:
    memory, _ = read_snapshot(database)
    engine = create_engine("sqlite://", creator=lambda: memory, poolclass=StaticPool)
    try:
        with engine.connect() as conn:
            return build_plan(conn, bookmarks, selection)
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("preview", "apply"))
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--bookmarks", type=Path, required=True)
    parser.add_argument("--selection", type=Path, help="Reviewed proposed-plan.json with explicit row/binding IDs")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--confirm-plan")
    parser.add_argument("--output", type=Path, required=True, help="New private JSON output, distinct from inputs")
    parser.add_argument("--cache-database", type=Path)
    parser.add_argument("--cache-dir", type=Path)
    args = parser.parse_args()
    protected = {p.resolve() for p in (args.database, args.bookmarks, args.selection, args.plan, args.cache_database) if p}
    for path in tuple(protected):
        protected.update(Path(str(path) + suffix) for suffix in ("-wal", "-shm", "-journal"))
    if args.output.exists() or args.output.resolve() in protected:
        raise ValueError("Output must be a new file distinct from every input")
    args.database.resolve(strict=True)
    if args.operation == "preview":
        if not args.selection:
            raise ValueError("Preview requires a reviewed --selection")
        result = preview(args.database, args.bookmarks, json.loads(args.selection.read_text()))
    else:
        if not args.plan or not args.confirm_plan or not args.cache_database or not args.cache_dir:
            raise ValueError("Apply requires --plan, --confirm-plan and explicit cache paths")
        args.cache_database.resolve(strict=True)
        args.cache_dir.resolve(strict=True)
        if args.cache_database.resolve() in {args.database.resolve(), args.bookmarks.resolve()}:
            raise ValueError("Cache database must be distinct from source inputs")
        approved = json.loads(args.plan.read_text())
        if args.confirm_plan != approved["approval_digest"]:
            raise ValueError("Approval digest does not match")
        if preview(args.database, args.bookmarks, approved["selection"])["approval_digest"] != args.confirm_plan:
            raise ValueError("The consolidation preview is stale")
        # No migration, backup, or Chrome write occurs in this apply branch.
        from src.flowinone.resource_library.database import ResourceDatabase, ensure_database_current
        from src.flowinone.bookmarks.mirror import maintain_mirror_cache
        from src.flowinone.catalog.discovery import DiscoveryService
        from src.file_handler.thumbnails.store import ThumbnailStore

        ensure_database_current(args.database)
        database = ResourceDatabase(args.database, migrate=False)
        result = {"data_committed": False}
        try:
            with database.engine.connect() as conn:
                conn.exec_driver_sql("BEGIN IMMEDIATE")
                try:
                    result["applied"] = apply_plan(conn, approved, args.bookmarks)
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
            result["data_committed"] = True
            result["relations"] = DiscoveryService(database).rebuild_item_relations()
            store = ThumbnailStore(str(args.cache_database), str(args.cache_dir))
            result["cache_cleanup"] = maintain_mirror_cache(database, store, deleted_content=True)
            if result["cache_cleanup"].get("errors"):
                raise RuntimeError("Cache cleanup reported file errors")
        except Exception as exc:
            result["error"] = str(exc)
            raise
        finally:
            database.dispose()
            _write_new(args.output, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    _write_new(args.output, result)
    print(json.dumps({"pairs": len(result["cases"]), "approval_digest": result["approval_digest"],
                      "output": str(args.output)}, ensure_ascii=False, indent=2))


def _write_new(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
