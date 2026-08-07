"""Read-only version history and bounded text diffs for Resource snapshots."""

from __future__ import annotations

from difflib import SequenceMatcher, unified_diff
from pathlib import Path
from typing import Any

from sqlalchemy import select

from .canonical import ensure_within
from .database import ResourceDatabase
from .models import Resource, ResourceContent
from .repository import ResourceNotFound
from .settings import ResourceSettings, get_resource_settings


TEXT_CONTENT_TYPES = (
    "article_text",
    "pdf_text",
    "video_transcript",
    "social_post_text",
)
_MAX_DIFF_LINES = 600
_MAX_DIFF_CHARACTERS = 48_000
_MAX_COMPARE_INPUT_LINES = 20_000


class ResourceVersionService:
    """Expose content snapshots without treating downloaded source files as URLs."""

    def __init__(
        self,
        database: ResourceDatabase,
        settings: ResourceSettings | None = None,
    ):
        self.database = database
        self.settings = settings or get_resource_settings()

    @staticmethod
    def _serialize(content: ResourceContent, *, current_hash: str | None) -> dict[str, Any]:
        return {
            "id": content.id,
            "content_type": content.content_type,
            "content_hash": content.content_hash,
            "byte_size": content.byte_size,
            "extractor_name": content.extractor_name,
            "extractor_version": content.extractor_version,
            "created_at": content.created_at,
            "is_current": bool(content.content_hash and content.content_hash == current_hash),
        }

    def _content_rows(self, resource_id: str) -> tuple[Resource, list[ResourceContent]]:
        with self.database.session(write=False) as session:
            resource = session.get(Resource, resource_id)
            if resource is None:
                raise ResourceNotFound(resource_id)
            rows = list(
                session.scalars(
                    select(ResourceContent)
                    .where(
                        ResourceContent.resource_id == resource_id,
                        ResourceContent.content_type.in_(TEXT_CONTENT_TYPES),
                    )
                    .order_by(ResourceContent.created_at.desc(), ResourceContent.id.desc())
                )
            )
            session.expunge(resource)
            for row in rows:
                session.expunge(row)
        return resource, rows

    def list_versions(self, resource_id: str, *, limit: int = 30) -> list[dict[str, Any]]:
        resource, rows = self._content_rows(resource_id)
        versions = [
            self._serialize(row, current_hash=resource.content_hash)
            for row in rows[:max(1, min(int(limit), 100))]
        ]
        # SQLite snapshots are second-precision, so two quick refreshes can
        # share a timestamp.  The Resource's current hash is authoritative;
        # always surface it first instead of relying on UUID ordering.
        current = [version for version in versions if version["is_current"]]
        history = sorted(
            (version for version in versions if not version["is_current"]),
            key=lambda version: (str(version["created_at"]), str(version["id"])),
            reverse=True,
        )
        return current + history

    def _load_content(self, content: ResourceContent) -> str:
        try:
            path = ensure_within(self.settings.content_dir, Path(content.content_path))
        except ValueError as exc:
            raise ValueError("內容快照位於不允許的位置") from exc
        try:
            if not path.is_file():
                raise FileNotFoundError(path)
            if path.stat().st_size > self.settings.max_download_bytes:
                raise ValueError("內容快照過大，無法在瀏覽器中比較")
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("內容快照不是可比較的 UTF-8 文字") from exc
        except OSError as exc:
            raise ValueError("內容快照已無法讀取") from exc

    def compare(
        self,
        resource_id: str,
        *,
        from_version_id: str,
        to_version_id: str,
    ) -> dict[str, Any]:
        if not from_version_id or not to_version_id:
            raise ValueError("請選擇兩個內容版本")
        resource, rows = self._content_rows(resource_id)
        by_id = {row.id: row for row in rows}
        before = by_id.get(from_version_id)
        after = by_id.get(to_version_id)
        if before is None or after is None:
            raise LookupError("內容版本不存在")
        before_text = self._load_content(before)
        after_text = self._load_content(after)
        before_lines = before_text.splitlines()
        after_lines = after_text.splitlines()
        input_truncated = (
            len(before_lines) > _MAX_COMPARE_INPUT_LINES
            or len(after_lines) > _MAX_COMPARE_INPUT_LINES
        )
        if len(before_lines) > _MAX_COMPARE_INPUT_LINES:
            before_lines = before_lines[:_MAX_COMPARE_INPUT_LINES] + [
                "… 較早版本其餘內容未比較。"
            ]
        if len(after_lines) > _MAX_COMPARE_INPUT_LINES:
            after_lines = after_lines[:_MAX_COMPARE_INPUT_LINES] + [
                "… 較新版本其餘內容未比較。"
            ]
        matcher = SequenceMatcher(None, before_lines, after_lines, autojunk=False)
        added = removed = change_blocks = 0
        for tag, start_before, end_before, start_after, end_after in matcher.get_opcodes():
            if tag == "equal":
                continue
            change_blocks += 1
            removed += end_before - start_before
            added += end_after - start_after
        diff_lines = list(
            unified_diff(
                before_lines,
                after_lines,
                fromfile=f"版本 {before.created_at}",
                tofile=f"版本 {after.created_at}",
                n=2,
                lineterm="",
            )
        )
        truncated = input_truncated or len(diff_lines) > _MAX_DIFF_LINES
        if truncated:
            diff_lines = diff_lines[:_MAX_DIFF_LINES]
            diff_lines.append("… diff 已截斷；請縮小內容範圍後再比較。")
        diff_text = "\n".join(diff_lines)
        if len(diff_text) > _MAX_DIFF_CHARACTERS:
            diff_text = diff_text[:_MAX_DIFF_CHARACTERS].rstrip() + "\n… diff 已截斷。"
            truncated = True
        return {
            "from_version": self._serialize(before, current_hash=resource.content_hash),
            "to_version": self._serialize(after, current_hash=resource.content_hash),
            "changed": bool(diff_lines),
            "similarity_percent": round(matcher.ratio() * 100),
            "added_lines": added,
            "removed_lines": removed,
            "change_blocks": change_blocks,
            "diff": diff_text,
            "truncated": truncated,
        }


__all__ = ["ResourceVersionService", "TEXT_CONTENT_TYPES"]
