"""Local-only duplicate and visual-similarity analysis for Catalog images.

Hashes are stored as renderer-owned Catalog artifacts.  The source library and
the local item index remain untouched: the job reads a path already known to
the item DB and can therefore be run, stopped, or rebuilt independently.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import text

from src.file_handler.item_db import fetch_item_paths
from src.flowinone.resource_library.database import ResourceDatabase
from src.flowinone.resource_library.models import new_id, utc_now_text

from .service import CatalogService


VISUAL_HASH_ARTIFACT = "visual_hash"
VISUAL_HASH_ALGORITHM = "dhash-64"
VISUAL_HASH_VERSION = "1"
_MAX_IMAGE_PIXELS = 100_000_000


def _input_signature(path: Path) -> str:
    stat = path.stat()
    return f"{stat.st_size}:{stat.st_mtime_ns}"


def _full_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1_048_576), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _dhash64(path: Path) -> str:
    """Return a small perceptual hash without adding an imagehash dependency."""
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - Pillow is a project dependency
        raise RuntimeError("Pillow 尚未安裝，無法分析圖片") from exc
    with Image.open(path) as source:
        width, height = source.size
        if width * height > _MAX_IMAGE_PIXELS:
            raise ValueError(f"圖片像素過大（{width}×{height}）")
        image = source.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    pixels = list(image.getdata())
    value = 0
    for y in range(8):
        row = y * 9
        for x in range(8):
            value = (value << 1) | int(pixels[row + x] > pixels[row + x + 1])
    return f"{value:016x}"


def _artifact_payload(value: str | None) -> dict[str, Any]:
    try:
        payload = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


class CatalogSimilarityService:
    """Build and query bounded local image similarity artifacts."""

    def __init__(self, database: ResourceDatabase):
        self.database = database
        self.catalog = CatalogService(database)

    def _local_image_rows(self, *, limit: int) -> list[dict[str, str]]:
        with self.database.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT i.id,o.source_key
                    FROM catalog_items i
                    JOIN catalog_origins o ON o.catalog_item_id=i.id
                    WHERE i.is_deleted=0 AND i.item_type='image'
                      AND o.source_kind='local' AND o.stale=0
                    ORDER BY i.id
                    LIMIT :limit
                    """
                ),
                {"limit": max(1, min(int(limit), 50_000))},
            ).mappings()
            return [
                {"catalog_item_id": str(row["id"]), "local_item_id": str(row["source_key"])}
                for row in rows
            ]

    def _current_signatures(self, item_ids: Iterable[str]) -> dict[str, str]:
        selected = tuple(dict.fromkeys(str(item_id) for item_id in item_ids if item_id))
        result: dict[str, str] = {}
        with self.database.engine.connect() as conn:
            for offset in range(0, len(selected), 800):
                batch = selected[offset:offset + 800]
                if not batch:
                    continue
                placeholders = ",".join(f":item_{index}" for index in range(len(batch)))
                rows = conn.execute(
                    text(
                        f"""
                        SELECT catalog_item_id,input_fingerprint
                        FROM catalog_artifacts
                        WHERE artifact_type=:type AND is_current=1
                          AND catalog_item_id IN ({placeholders})
                        """
                    ),
                    {
                        "type": VISUAL_HASH_ARTIFACT,
                        **{f"item_{index}": item_id for index, item_id in enumerate(batch)},
                    },
                ).mappings()
                result.update(
                    {
                        str(row["catalog_item_id"]): str(row["input_fingerprint"] or "")
                        for row in rows
                    }
                )
        return result

    def analyze_local_images(self, *, limit: int = 20_000, force: bool = False) -> dict[str, Any]:
        """Hash local Catalog images, skipping files unchanged since prior work."""
        rows = self._local_image_rows(limit=limit)
        paths = fetch_item_paths(row["local_item_id"] for row in rows)
        current = self._current_signatures(row["catalog_item_id"] for row in rows)
        prepared: list[dict[str, Any]] = []
        skipped = unavailable = failed = 0
        errors: list[dict[str, str]] = []
        for row in rows:
            path_data = paths.get(row["local_item_id"])
            path = Path(str((path_data or {}).get("absolute_path") or ""))
            if not path.is_file():
                unavailable += 1
                continue
            try:
                signature = _input_signature(path)
                if not force and current.get(row["catalog_item_id"]) == signature:
                    skipped += 1
                    continue
                prepared.append(
                    {
                        "catalog_item_id": row["catalog_item_id"],
                        "input_fingerprint": signature,
                        "content": {
                            "algorithm": VISUAL_HASH_ALGORITHM,
                            "visual_hash": _dhash64(path),
                            "content_hash": _full_sha256(path),
                        },
                    }
                )
            except Exception as exc:  # A single corrupt image must not stop the scan.
                failed += 1
                errors.append({"item_id": row["catalog_item_id"], "error": str(exc)[:240]})

        if prepared:
            now = utc_now_text()
            with self.database.write_transaction() as conn:
                for item in prepared:
                    conn.execute(
                        text(
                            """
                            UPDATE catalog_artifacts SET is_current=0
                            WHERE catalog_item_id=:item AND artifact_type=:type
                            """
                        ),
                        {"item": item["catalog_item_id"], "type": VISUAL_HASH_ARTIFACT},
                    )
                    conn.execute(
                        text(
                            """
                            INSERT INTO catalog_artifacts(
                                id,catalog_item_id,artifact_type,content_text,content_json,
                                provider,model,version,input_fingerprint,is_current,created_at
                            ) VALUES(
                                :id,:item,:type,NULL,:content,'pillow',:model,:version,
                                :fingerprint,1,:created
                            )
                            """
                        ),
                        {
                            "id": new_id(),
                            "item": item["catalog_item_id"],
                            "type": VISUAL_HASH_ARTIFACT,
                            "content": json.dumps(item["content"], ensure_ascii=False, sort_keys=True),
                            "model": VISUAL_HASH_ALGORITHM,
                            "version": VISUAL_HASH_VERSION,
                            "fingerprint": item["input_fingerprint"],
                            "created": now,
                        },
                    )
        return {
            "candidates": len(rows),
            "analyzed": len(prepared),
            "skipped": skipped,
            "unavailable": unavailable,
            "failed": failed,
            "errors": errors[:20],
        }

    def _current_artifacts(self) -> list[dict[str, Any]]:
        with self.database.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT a.catalog_item_id,a.content_json,i.title,i.thumbnail_ref,
                           o.source_key AS local_item_id,o.detail_uri
                    FROM catalog_artifacts a
                    JOIN catalog_items i ON i.id=a.catalog_item_id
                    JOIN catalog_origins o ON o.catalog_item_id=i.id
                    WHERE a.artifact_type=:type AND a.is_current=1 AND i.is_deleted=0
                      AND i.item_type='image' AND o.source_kind='local' AND o.stale=0
                    """
                ),
                {"type": VISUAL_HASH_ARTIFACT},
            ).mappings()
            return [
                {
                    "item_id": str(row["catalog_item_id"]),
                    "title": str(row["title"] or ""),
                    "thumbnail_ref": str(row["thumbnail_ref"] or ""),
                    "local_item_id": str(row["local_item_id"] or ""),
                    "detail_uri": str(row["detail_uri"] or ""),
                    "content": _artifact_payload(row["content_json"]),
                }
                for row in rows
            ]

    @staticmethod
    def _duplicate_members(artifacts: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in artifacts:
            content_hash = str(item["content"].get("content_hash") or "")
            if content_hash.startswith("sha256:"):
                grouped[content_hash].append(item)
        return {
            content_hash: members
            for content_hash, members in grouped.items()
            if len(members) > 1
        }

    def _canonical_selections(self, content_hashes: Iterable[str]) -> dict[str, str]:
        selected = tuple(dict.fromkeys(str(value) for value in content_hashes if value))
        if not selected:
            return {}
        result: dict[str, str] = {}
        with self.database.engine.connect() as conn:
            for offset in range(0, len(selected), 800):
                batch = selected[offset:offset + 800]
                placeholders = ",".join(f":hash_{index}" for index in range(len(batch)))
                rows = conn.execute(
                    text(
                        f"""
                        SELECT content_hash,canonical_item_id
                        FROM catalog_duplicate_reviews
                        WHERE content_hash IN ({placeholders})
                        """
                    ),
                    {f"hash_{index}": value for index, value in enumerate(batch)},
                ).mappings()
                result.update(
                    {
                        str(row["content_hash"]): str(row["canonical_item_id"])
                        for row in rows
                    }
                )
        return result

    @staticmethod
    def _review_member(item: dict[str, Any], paths: dict[str, dict[str, Any]]) -> dict[str, Any]:
        path_data = paths.get(str(item["local_item_id"])) or {}
        path = Path(str(path_data.get("absolute_path") or ""))
        return {
            "id": item["item_id"],
            "title": item["title"] or "Untitled",
            "thumbnail_ref": item["thumbnail_ref"] or None,
            "open_uri": item["detail_uri"] or None,
            # A path is included only when the current indexed file still exists.
            # The web layer uses the item id again before revealing it, so a stale
            # rendered path can never be used as a filesystem command argument.
            "local_path": str(path) if path.is_file() else None,
        }

    def duplicate_review(self) -> dict[str, Any]:
        """Return every exact Local-image duplicate group from current artifacts.

        dHash remains available through ``similar_images`` on the existing image
        cards.  This review surface deliberately presents only byte-identical
        SHA-256 groups so choosing a canonical remains unambiguous.
        """
        artifacts = self._current_artifacts()
        duplicate_members = self._duplicate_members(artifacts)
        selections = self._canonical_selections(duplicate_members)
        groups: list[dict[str, Any]] = []
        for content_hash, members in duplicate_members.items():
            ids = {str(member["item_id"]) for member in members}
            selected = selections.get(content_hash)
            canonical_item_id = selected if selected in ids else str(
                min(
                    members,
                    key=lambda member: (
                        str(member["title"]).casefold(), str(member["item_id"])
                    ),
                )["item_id"]
            )
            paths = fetch_item_paths(member["local_item_id"] for member in members)
            ordered_members = sorted(
                members,
                key=lambda member: (
                    0 if member["item_id"] == canonical_item_id else 1,
                    str(member["title"]).casefold(),
                    str(member["item_id"]),
                ),
            )
            groups.append(
                {
                    "content_hash": content_hash,
                    "canonical_item_id": canonical_item_id,
                    "items": [self._review_member(member, paths) for member in ordered_members],
                }
            )
        groups.sort(
            key=lambda group: (
                -len(group["items"]),
                str(group["items"][0]["title"]).casefold(),
                group["content_hash"],
            )
        )
        return {
            "analyzed_items": len(artifacts),
            "duplicate_groups": len(groups),
            "duplicate_items": sum(len(group["items"]) for group in groups),
            "groups": groups,
        }

    def choose_duplicate_canonical(self, content_hash: str, item_id: str) -> dict[str, Any]:
        """Persist a review-only canonical choice for one exact-hash group."""
        normalized_hash = str(content_hash or "").strip()
        normalized_item_id = str(item_id or "").strip()
        members = self._duplicate_members(self._current_artifacts()).get(normalized_hash, [])
        if len(members) < 2:
            raise LookupError(normalized_hash)
        if normalized_item_id not in {str(member["item_id"]) for member in members}:
            raise ValueError("選定的圖片不屬於這組完全相同檔案")
        now = utc_now_text()
        with self.database.write_transaction() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO catalog_duplicate_reviews(content_hash,canonical_item_id,updated_at)
                    VALUES(:content_hash,:canonical_item_id,:updated_at)
                    ON CONFLICT(content_hash) DO UPDATE SET
                        canonical_item_id=:canonical_item_id,
                        updated_at=:updated_at
                    """
                ),
                {
                    "content_hash": normalized_hash,
                    "canonical_item_id": normalized_item_id,
                    "updated_at": now,
                },
            )
        return {
            "content_hash": normalized_hash,
            "canonical_item_id": normalized_item_id,
        }

    def duplicate_review_path(self, item_id: str) -> Path:
        """Resolve a currently reviewed duplicate to its local source path."""
        normalized_item_id = str(item_id or "").strip()
        artifacts = self._current_artifacts()
        duplicate_members = self._duplicate_members(artifacts)
        matching = next(
            (
                member
                for members in duplicate_members.values()
                for member in members
                if str(member["item_id"]) == normalized_item_id
            ),
            None,
        )
        if matching is None:
            raise LookupError(normalized_item_id)
        path_data = fetch_item_paths([matching["local_item_id"]]).get(
            str(matching["local_item_id"]), {}
        )
        path = Path(str(path_data.get("absolute_path") or ""))
        if not path.is_file():
            raise FileNotFoundError(normalized_item_id)
        return path

    def status(self) -> dict[str, Any]:
        artifacts = self._current_artifacts()
        duplicate_counts = [
            len(members) for members in self._duplicate_members(artifacts).values()
        ]
        with self.database.engine.connect() as conn:
            job = conn.execute(
                text(
                    """
                    SELECT id,job_type,status,error_message,created_at,updated_at
                    FROM processing_jobs
                    WHERE job_type='catalog_similarity'
                    ORDER BY created_at DESC LIMIT 1
                    """
                )
            ).mappings().first()
        return {
            "algorithm": VISUAL_HASH_ALGORITHM,
            "analyzed_items": len(artifacts),
            "duplicate_groups": len(duplicate_counts),
            "duplicate_items": sum(duplicate_counts),
            "job": dict(job) if job else None,
        }

    def similar_images(
        self, item_id: str, *, limit: int = 12, max_distance: int = 8
    ) -> dict[str, Any]:
        # Validate the target even when it has not been analyzed yet.
        self.catalog.get(item_id)
        artifacts = self._current_artifacts()
        target = next((item for item in artifacts if item["item_id"] == item_id), None)
        if target is None:
            return {"analyzed": False, "items": []}
        target_content = target["content"]
        target_visual = str(target_content.get("visual_hash") or "")
        target_exact = str(target_content.get("content_hash") or "")
        matches: list[dict[str, Any]] = []
        for candidate in artifacts:
            if candidate["item_id"] == item_id:
                continue
            content = candidate["content"]
            exact = str(content.get("content_hash") or "")
            visual = str(content.get("visual_hash") or "")
            if target_exact and exact == target_exact:
                match_type = "duplicate"
                distance = 0
            elif len(target_visual) == 16 and len(visual) == 16:
                try:
                    distance = (int(target_visual, 16) ^ int(visual, 16)).bit_count()
                except ValueError:
                    continue
                if distance > max(0, min(int(max_distance), 32)):
                    continue
                match_type = "visual"
            else:
                continue
            try:
                catalog_item = self.catalog.get(candidate["item_id"])
            except LookupError:
                continue
            matches.append(
                {
                    **catalog_item,
                    "match_type": match_type,
                    "distance": distance,
                    "similarity_percent": round((1 - distance / 64) * 100),
                }
            )
        matches.sort(
            key=lambda item: (
                0 if item["match_type"] == "duplicate" else 1,
                int(item["distance"]),
                str(item["title"]).casefold(),
            )
        )
        return {"analyzed": True, "items": matches[:max(1, min(int(limit), 40))]}


__all__ = [
    "CatalogSimilarityService",
    "VISUAL_HASH_ALGORITHM",
    "VISUAL_HASH_ARTIFACT",
]
