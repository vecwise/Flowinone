"""Safe Markdown rendering for Obsidian and read-only resource mirrors."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .canonical import ensure_within, sanitize_markdown_filename
from .database import ResourceDatabase, get_resource_database
from .models import DraftNote, NoteExport, new_id, utc_now_text
from .repository import ResourceRepository
from .settings import ResourceSettings, get_resource_settings


class ObsidianNotConfigured(RuntimeError):
    pass


class ObsidianConflict(FileExistsError):
    pass


def _yaml_string(value: str) -> str:
    return json.dumps(value or "", ensure_ascii=False)


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(payload)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def render_note_markdown(note: DraftNote) -> str:
    """Render one literature/synthesis draft with typed source provenance."""
    source_ids = [source.source_id for source in note.sources]
    source_lines = []
    for source in sorted(note.sources, key=lambda item: (item.position, item.added_at)):
        label = (source.citation_label or source.source_id or "來源").replace("\n", " ").strip()
        label = label.replace("[", "\\[").replace("]", "\\]")
        if source.url_snapshot:
            safe_url = source.url_snapshot.replace("<", "%3C").replace(">", "%3E")
            source_lines.append(f"- [{label}](<{safe_url}>) — `{source.source_kind}`")
        else:
            source_lines.append(f"- {label} — `{source.source_kind}:{source.source_id}`")
        if source.annotation:
            source_lines.append(f"  - {source.annotation.strip()}")
    frontmatter = [
        "---",
        f"type: {_yaml_string('literature_note' if note.note_type == 'literature' else 'synthesis_note')}",
        f"flowinone_note_id: {_yaml_string(note.id)}",
        f"created_at: {_yaml_string(note.created_at)}",
        f"updated_at: {_yaml_string(note.updated_at)}",
        "source_ids:",
    ]
    frontmatter.extend(f"  - {_yaml_string(source_id)}" for source_id in source_ids)
    frontmatter.append("---")
    source_section = "\n".join(source_lines) or "- 尚未加入來源"
    return (
        "\n".join(frontmatter)
        + f"\n\n# {note.title.strip()}\n\n"
        + (note.body.rstrip() + "\n\n" if note.body.strip() else "")
        + "## Flowinone 來源\n\n"
        + source_section
        + "\n"
    )


class ObsidianExporter:
    """Export only under an explicitly configured vault and never clobber silently."""

    def __init__(
        self,
        database: Optional[ResourceDatabase] = None,
        settings: Optional[ResourceSettings] = None,
    ):
        self.database = database or get_resource_database()
        self.settings = settings or get_resource_settings()
        self.resources = ResourceRepository(self.database)

    def export_note(
        self,
        note_id: str,
        *,
        vault_path: Optional[Path] = None,
        target_folder: Optional[str] = None,
        filename: Optional[str] = None,
        overwrite: bool = False,
    ) -> dict:
        root = (vault_path or self.settings.obsidian_vault_path)
        if root is None:
            raise ObsidianNotConfigured("請先設定 OBSIDIAN_VAULT_PATH")
        root = root.expanduser().resolve()
        if not root.is_dir():
            raise ObsidianNotConfigured(f"Obsidian vault 不存在: {root}")
        folder_name = target_folder if target_folder is not None else self.settings.obsidian_target_folder
        folder = ensure_within(root, root / folder_name)

        with self.database.session() as session:
            note = session.scalar(
                select(DraftNote)
                .options(selectinload(DraftNote.sources))
                .where(DraftNote.id == note_id)
            )
            if note is None:
                raise LookupError(note_id)
            markdown = render_note_markdown(note)
            note_title = note.title
        payload = markdown.encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        safe_name = sanitize_markdown_filename(filename or note_title)
        target = ensure_within(root, folder / safe_name)
        if target.exists():
            existing = target.read_bytes()
            if existing != payload and not overwrite:
                raise ObsidianConflict(f"Obsidian note 已存在且內容不同: {target}")
        if not target.exists() or target.read_bytes() != payload:
            _atomic_write(target, payload)

        with self.database.session() as session:
            note = session.get(DraftNote, note_id)
            if note is None:
                raise LookupError(note_id)
            note.status = "exported"
            note.obsidian_path = str(target)
            note.export_hash = digest
            note.updated_at = utc_now_text()
            session.add(
                NoteExport(
                    id=new_id(),
                    draft_note_id=note_id,
                    target_path=str(target),
                    content_hash=digest,
                    status="complete",
                )
            )
        return {"note_id": note_id, "path": str(target), "content_hash": digest}

    def export_resource_mirror(self, resource_id: str) -> dict:
        """Write a derived Markdown mirror; the database remains authoritative."""
        resource = self.resources.get(resource_id)
        tags = "\n".join(f"  - {_yaml_string(name)}" for name in resource["tag_names"])
        markdown = f"""---
id: {_yaml_string(resource['id'])}
url: {_yaml_string(resource['original_url'])}
reading_state: {_yaml_string(resource['reading_state'])}
disposition: {_yaml_string(resource['disposition'])}
source_type: {_yaml_string(resource['source_type'])}
captured_at: {_yaml_string(resource['captured_at'])}
tags:
{tags}
---

# {resource['title']}

## One-line Summary

{resource.get('summary_one_line') or ''}

## Short Summary

{resource.get('summary_short') or ''}

## Why This Matters

{resource.get('why_this_matters') or ''}

## User Note

{resource.get('user_note') or ''}
"""
        payload = markdown.encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        path = ensure_within(
            self.settings.export_dir,
            self.settings.export_dir / f"{resource_id}.md",
        )
        _atomic_write(path, payload)
        return {"resource_id": resource_id, "path": str(path), "content_hash": digest}


__all__ = [
    "ObsidianConflict",
    "ObsidianExporter",
    "ObsidianNotConfigured",
    "render_note_markdown",
]
