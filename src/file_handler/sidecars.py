"""Portable, versioned sidecar manifests for local Flowinone media."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .item_db import _get_db_connection, fetch_items


SIDECAR_NAME = ".flowinone.json"
SIDECAR_SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def content_fingerprint(path: Path, chunk_size: int = 1_048_576) -> str:
    """Return a move-stable bounded fingerprint without hashing an entire large video."""
    stat = path.stat()
    digest = hashlib.sha256()
    digest.update(str(stat.st_size).encode())
    with path.open("rb") as handle:
        digest.update(handle.read(chunk_size))
        if stat.st_size > chunk_size:
            handle.seek(max(0, stat.st_size - chunk_size))
            digest.update(handle.read(chunk_size))
    return f"sha256-bounded:{digest.hexdigest()}"


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _safe_child(parent: Path, relative: str) -> Path:
    candidate = (parent / relative).resolve()
    if os.path.commonpath((str(parent.resolve()), str(candidate))) != str(parent.resolve()):
        raise ValueError(f"Sidecar path escapes its directory: {relative}")
    return candidate


class SidecarService:
    """Export/import user-owned local metadata while leaving caches in SQLite."""

    def export(self, root: Path, *, dry_run: bool = True, overwrite: bool = False) -> dict[str, Any]:
        root = root.expanduser().resolve()
        grouped: dict[Path, list[dict[str, Any]]] = defaultdict(list)
        offset = 0
        while True:
            payload = fetch_items(limit=1000, offset=offset)
            rows = payload.get("items") or []
            if not rows:
                break
            for row in rows:
                absolute = Path(str(row.get("absolute_path") or ""))
                if not absolute.is_file():
                    continue
                try:
                    absolute.relative_to(root)
                except ValueError:
                    continue
                fingerprint = row.get("content_fingerprint") or content_fingerprint(absolute)
                uid = row.get("portable_uid") or uuid.uuid5(uuid.NAMESPACE_URL, fingerprint).hex
                grouped[absolute.parent].append(
                    {
                        "uid": uid,
                        "fingerprint": fingerprint,
                        "path": absolute.name,
                        "title": row.get("name") or absolute.name,
                        "tags": row.get("tags") or [],
                        "people": row.get("people_labels") or [],
                        "annotation": row.get("annotation") or "",
                        "source_url": row.get("source_url") or "",
                        "updated_at": row.get("sidecar_updated_at") or row.get("updated_at") or _now(),
                    }
                )
                if not dry_run:
                    with _get_db_connection() as conn:
                        conn.execute("UPDATE items SET portable_uid=?,content_fingerprint=? WHERE item_id=?", (uid, fingerprint, row["item_id"]))
            offset += len(rows)
            if offset >= int(payload.get("total") or 0):
                break
        result = {"directories": len(grouped), "items": sum(map(len, grouped.values())), "written": 0, "unchanged": 0, "conflicts": []}
        for directory, items in grouped.items():
            target = directory / SIDECAR_NAME
            data = {"schema_version": SIDECAR_SCHEMA_VERSION, "kind": "flowinone-directory-manifest", "updated_at": _now(), "items": sorted(items, key=lambda item: item["path"].casefold())}
            encoded = (json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
            if target.exists():
                try:
                    existing = json.loads(target.read_text(encoding="utf-8"))
                    comparable = dict(existing)
                    comparable["updated_at"] = data["updated_at"]
                    if comparable == data:
                        result["unchanged"] += 1
                        continue
                except (OSError, ValueError):
                    pass
                if not overwrite:
                    result["conflicts"].append(str(target))
                    continue
            if not dry_run:
                _atomic_write(target, encoded)
            result["written"] += 1
        result["dry_run"] = dry_run
        return result

    def audit(self, root: Path) -> dict[str, Any]:
        root = root.expanduser().resolve()
        result: dict[str, Any] = {"manifests": 0, "items": 0, "missing": [], "invalid": []}
        for manifest in root.rglob(SIDECAR_NAME):
            result["manifests"] += 1
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                if payload.get("schema_version") != SIDECAR_SCHEMA_VERSION or not isinstance(payload.get("items"), list):
                    raise ValueError("unsupported schema")
                for item in payload["items"]:
                    path = _safe_child(manifest.parent, str(item.get("path") or ""))
                    result["items"] += 1
                    if not path.is_file():
                        result["missing"].append(str(path))
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                result["invalid"].append({"path": str(manifest), "error": str(exc)})
        return result

    def import_(self, root: Path, *, dry_run: bool = True) -> dict[str, Any]:
        root = root.expanduser().resolve()
        audit = self.audit(root)
        result = {**audit, "updated": 0, "unmatched": [], "dry_run": dry_run}
        for manifest in root.rglob(SIDECAR_NAME):
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                if payload.get("schema_version") != SIDECAR_SCHEMA_VERSION:
                    continue
                for item in payload.get("items") or []:
                    path = _safe_child(manifest.parent, str(item.get("path") or ""))
                    if not path.is_file():
                        continue
                    fingerprint = str(item.get("fingerprint") or content_fingerprint(path))
                    uid = str(item.get("uid") or uuid.uuid5(uuid.NAMESPACE_URL, fingerprint).hex)
                    with _get_db_connection() as conn:
                        row = conn.execute("SELECT item_id FROM items WHERE portable_uid=? OR content_fingerprint=? OR absolute_path=? LIMIT 1", (uid, fingerprint, str(path))).fetchone()
                        if row is None:
                            result["unmatched"].append(str(path))
                            continue
                        if not dry_run:
                            conn.execute(
                                """UPDATE items SET portable_uid=?,content_fingerprint=?,name=?,tags=?,people_labels=?,annotation=?,source_url=?,absolute_path=?,relative_path=?,sidecar_updated_at=?,updated_at=CURRENT_TIMESTAMP WHERE item_id=?""",
                                (uid, fingerprint, str(item.get("title") or path.name), json.dumps(item.get("tags") or [], ensure_ascii=False), json.dumps(item.get("people") or [], ensure_ascii=False), str(item.get("annotation") or ""), str(item.get("source_url") or ""), str(path), os.path.relpath(path, root).replace(os.sep, "/"), str(item.get("updated_at") or _now()), row["item_id"]),
                            )
                        result["updated"] += 1
            except (OSError, ValueError, json.JSONDecodeError):
                continue
        return result


__all__ = ["SIDECAR_NAME", "SIDECAR_SCHEMA_VERSION", "SidecarService", "content_fingerprint"]
