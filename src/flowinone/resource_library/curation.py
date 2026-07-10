"""Mixed-source inspiration collections and many-source draft notes."""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlsplit

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from .database import ResourceDatabase, get_resource_database
from .models import (
    Collection,
    CollectionItem,
    DraftNote,
    DraftNoteSource,
    ResourceNoteLink,
    new_id,
    utc_now_text,
)
from .repository import ResourceNotFound, ResourceRepository


COLLECTION_KINDS = {"inspiration", "project", "reading_list"}
NOTE_TYPES = {"literature", "synthesis"}
SOURCE_KINDS = {"resource", "eagle", "filesystem", "bookmark", "obsidian"}


def _safe_snapshot_url(value: str) -> str:
    """Allow navigable references while rejecting script/data URL injection."""
    candidate = (value or "").strip()
    if not candidate:
        return ""
    if candidate.startswith("/") and not candidate.startswith("//"):
        return candidate
    parsed = urlsplit(candidate)
    if parsed.scheme.lower() not in {"http", "https", "eagle", "obsidian"}:
        raise ValueError("來源 URL 只允許 HTTP(S)、Eagle、Obsidian 或站內路徑")
    return candidate


class CollectionNotFound(LookupError):
    pass


class DraftNoteNotFound(LookupError):
    pass


def serialize_collection(collection: Collection, *, detail: bool = False) -> dict:
    payload = {
        "id": collection.id,
        "title": collection.title,
        "description": collection.description,
        "kind": collection.kind,
        "item_count": len(collection.items),
        "created_at": collection.created_at,
        "updated_at": collection.updated_at,
    }
    if detail:
        payload["items"] = [
            {
                "id": item.id,
                "source_kind": item.source_kind,
                "source_id": item.source_id,
                "title": item.title_snapshot or "Untitled",
                "url": item.url_snapshot,
                "thumbnail": item.thumbnail_snapshot,
                "annotation": item.annotation,
                "position": item.position,
                "added_at": item.added_at,
            }
            for item in sorted(collection.items, key=lambda value: (value.position, value.added_at))
        ]
    return payload


def serialize_note(note: DraftNote, *, detail: bool = False) -> dict:
    payload = {
        "id": note.id,
        "title": note.title,
        "note_type": note.note_type,
        "body": note.body if detail else None,
        "status": note.status,
        "obsidian_path": note.obsidian_path,
        "export_hash": note.export_hash,
        "source_count": len(note.sources),
        "created_at": note.created_at,
        "updated_at": note.updated_at,
    }
    if detail:
        payload["sources"] = [
            {
                "id": source.id,
                "source_kind": source.source_kind,
                "source_id": source.source_id,
                "citation_label": source.citation_label,
                "url": source.url_snapshot,
                "annotation": source.annotation,
                "position": source.position,
                "added_at": source.added_at,
            }
            for source in sorted(note.sources, key=lambda value: (value.position, value.added_at))
        ]
    return payload


class CollectionService:
    """Persist cross-source references without taking ownership from Eagle/filesystem."""

    def __init__(self, database: Optional[ResourceDatabase] = None):
        self.database = database or get_resource_database()
        self.resources = ResourceRepository(self.database)

    def create(self, title: str, description: str = "", kind: str = "inspiration") -> dict:
        title = title.strip()
        if not title:
            raise ValueError("Collection 標題不可空白")
        if kind not in COLLECTION_KINDS:
            raise ValueError("無效的 Collection 類型")
        with self.database.session() as session:
            collection = Collection(
                id=new_id(),
                title=title,
                description=description.strip() or None,
                kind=kind,
            )
            session.add(collection)
            session.flush()
            collection_id = collection.id
        return self.get(collection_id)

    def list(self) -> list[dict]:
        with self.database.session() as session:
            collections = list(
                session.scalars(
                    select(Collection)
                    .options(selectinload(Collection.items))
                    .order_by(Collection.updated_at.desc())
                ).unique()
            )
            return [serialize_collection(collection) for collection in collections]

    def get(self, collection_id: str) -> dict:
        with self.database.session() as session:
            collection = session.scalar(
                select(Collection)
                .options(selectinload(Collection.items))
                .where(Collection.id == collection_id)
            )
            if collection is None:
                raise CollectionNotFound(collection_id)
            return serialize_collection(collection, detail=True)

    def update(self, collection_id: str, changes: dict) -> dict:
        with self.database.session() as session:
            collection = session.get(Collection, collection_id)
            if collection is None:
                raise CollectionNotFound(collection_id)
            if "title" in changes:
                title = str(changes["title"] or "").strip()
                if not title:
                    raise ValueError("Collection 標題不可空白")
                collection.title = title
            if "description" in changes:
                collection.description = str(changes["description"] or "").strip() or None
            if "kind" in changes:
                if changes["kind"] not in COLLECTION_KINDS:
                    raise ValueError("無效的 Collection 類型")
                collection.kind = changes["kind"]
            collection.updated_at = utc_now_text()
        return self.get(collection_id)

    def add_item(
        self,
        collection_id: str,
        *,
        source_kind: str,
        source_id: str,
        title: str = "",
        url: str = "",
        thumbnail: str = "",
        annotation: str = "",
    ) -> dict:
        if source_kind not in SOURCE_KINDS:
            raise ValueError("無效的來源類型")
        source_id = source_id.strip()
        if not source_id:
            raise ValueError("來源 ID 不可空白")
        if source_kind == "resource":
            resource = self.resources.get(source_id)
            title = title or resource["title"]
            url = url or resource["original_url"]
            # UI decorators resolve local/cache thumbnails; never persist an absolute
            # Flowinone filesystem path as a browser URL snapshot.
            thumbnail = thumbnail or ""
        url = _safe_snapshot_url(url)
        thumbnail = _safe_snapshot_url(thumbnail) if thumbnail else ""
        try:
            with self.database.session() as session:
                collection = session.get(Collection, collection_id)
                if collection is None:
                    raise CollectionNotFound(collection_id)
                position = int(
                    session.scalar(
                        select(func.coalesce(func.max(CollectionItem.position), -1)).where(
                            CollectionItem.collection_id == collection_id
                        )
                    )
                    or 0
                ) + 1
                session.add(
                    CollectionItem(
                        id=new_id(),
                        collection_id=collection_id,
                        source_kind=source_kind,
                        source_id=source_id,
                        title_snapshot=title.strip() or None,
                        url_snapshot=url or None,
                        thumbnail_snapshot=thumbnail or None,
                        annotation=annotation.strip() or None,
                        position=position,
                    )
                )
                collection.updated_at = utc_now_text()
        except IntegrityError as exc:
            raise ValueError("這個項目已經在 Collection 中") from exc
        return self.get(collection_id)

    def remove_item(self, collection_id: str, item_id: str) -> dict:
        with self.database.session() as session:
            result = session.execute(
                delete(CollectionItem).where(
                    CollectionItem.id == item_id,
                    CollectionItem.collection_id == collection_id,
                )
            )
            if not result.rowcount:
                raise CollectionNotFound(item_id)
            collection = session.get(Collection, collection_id)
            if collection:
                collection.updated_at = utc_now_text()
        return self.get(collection_id)

    def delete(self, collection_id: str) -> None:
        with self.database.session() as session:
            result = session.execute(delete(Collection).where(Collection.id == collection_id))
            if not result.rowcount:
                raise CollectionNotFound(collection_id)


class DraftNoteService:
    """Create literature and synthesis drafts that may cite many source kinds."""

    def __init__(self, database: Optional[ResourceDatabase] = None):
        self.database = database or get_resource_database()
        self.resources = ResourceRepository(self.database)

    def _note_query(self):
        return select(DraftNote).options(selectinload(DraftNote.sources))

    def create(self, title: str, body: str = "", note_type: str = "synthesis") -> dict:
        title = title.strip()
        if not title:
            raise ValueError("筆記標題不可空白")
        if note_type not in NOTE_TYPES:
            raise ValueError("無效的筆記類型")
        with self.database.session() as session:
            note = DraftNote(
                id=new_id(),
                title=title,
                note_type=note_type,
                body=body,
            )
            session.add(note)
            session.flush()
            note_id = note.id
        return self.get(note_id)

    def list(self) -> list[dict]:
        with self.database.session() as session:
            notes = list(
                session.scalars(self._note_query().order_by(DraftNote.updated_at.desc())).unique()
            )
            return [serialize_note(note) for note in notes]

    def get(self, note_id: str) -> dict:
        with self.database.session() as session:
            note = session.scalar(self._note_query().where(DraftNote.id == note_id))
            if note is None:
                raise DraftNoteNotFound(note_id)
            return serialize_note(note, detail=True)

    def update(self, note_id: str, changes: dict) -> dict:
        with self.database.session() as session:
            note = session.get(DraftNote, note_id)
            if note is None:
                raise DraftNoteNotFound(note_id)
            if "title" in changes:
                title = str(changes["title"] or "").strip()
                if not title:
                    raise ValueError("筆記標題不可空白")
                note.title = title
            if "body" in changes:
                note.body = str(changes["body"] or "")
            note.status = "draft"
            note.updated_at = utc_now_text()
        return self.get(note_id)

    def add_source(
        self,
        note_id: str,
        *,
        source_kind: str,
        source_id: str,
        citation_label: str = "",
        url: str = "",
        annotation: str = "",
    ) -> dict:
        if source_kind not in SOURCE_KINDS:
            raise ValueError("無效的來源類型")
        source_id = source_id.strip()
        if not source_id:
            raise ValueError("來源 ID 不可空白")
        if source_kind == "resource":
            resource = self.resources.get(source_id)
            citation_label = citation_label or resource["title"]
            url = url or resource["original_url"]
        url = _safe_snapshot_url(url)
        try:
            with self.database.session() as session:
                note = session.get(DraftNote, note_id)
                if note is None:
                    raise DraftNoteNotFound(note_id)
                position = int(
                    session.scalar(
                        select(func.coalesce(func.max(DraftNoteSource.position), -1)).where(
                            DraftNoteSource.draft_note_id == note_id
                        )
                    )
                    or 0
                ) + 1
                session.add(
                    DraftNoteSource(
                        id=new_id(),
                        draft_note_id=note_id,
                        source_kind=source_kind,
                        source_id=source_id,
                        citation_label=citation_label.strip() or None,
                        url_snapshot=url or None,
                        annotation=annotation.strip() or None,
                        position=position,
                    )
                )
                if source_kind == "resource" and session.get(
                    ResourceNoteLink, (source_id, note_id, "source")
                ) is None:
                    session.add(
                        ResourceNoteLink(
                            resource_id=source_id,
                            draft_note_id=note_id,
                            role="source",
                        )
                    )
                note.status = "draft"
                note.updated_at = utc_now_text()
        except IntegrityError as exc:
            raise ValueError("這個來源已經在筆記中") from exc
        return self.get(note_id)

    def create_literature_note(self, resource_id: str) -> dict:
        resource = self.resources.get(resource_id)
        with self.database.session() as session:
            existing_id = session.scalar(
                select(ResourceNoteLink.draft_note_id).where(
                    ResourceNoteLink.resource_id == resource_id,
                    ResourceNoteLink.role == "literature",
                )
            )
        if existing_id:
            return self.get(existing_id)

        body = f"""## 來源

- URL: {resource['original_url']}
- 作者／頻道: {resource.get('author') or ''}
- 平台: {resource.get('source_platform') or resource.get('domain') or ''}

## 一句話摘要

{resource.get('summary_one_line') or ''}

## 原文重點

{resource.get('summary_short') or ''}

## 我真正學到什麼



## 我同意什麼



## 我不同意或需要驗證什麼



## 與我現有知識的連結



## 可用在哪個專案



## 下一步行動

"""
        note = self.create(resource["title"], body, "literature")
        note = self.add_source(
            note["id"],
            source_kind="resource",
            source_id=resource_id,
            citation_label=resource["title"],
            url=resource["original_url"],
        )
        with self.database.session() as session:
            link = session.get(ResourceNoteLink, (resource_id, note["id"], "source"))
            if link:
                link.role = "literature"
        return self.get(note["id"])

    def create_from_collection(self, collection: dict, title: str = "") -> dict:
        note = self.create(
            title.strip() or collection["title"],
            body=f"## 核心想法\n\n\n\n## 來源之間的關係\n\n\n\n## 我的結論\n\n",
            note_type="synthesis",
        )
        for item in collection.get("items") or []:
            note = self.add_source(
                note["id"],
                source_kind=item["source_kind"],
                source_id=item["source_id"],
                citation_label=item.get("title") or "",
                url=item.get("url") or "",
                annotation=item.get("annotation") or "",
            )
        return note

    def delete(self, note_id: str) -> None:
        with self.database.session() as session:
            result = session.execute(delete(DraftNote).where(DraftNote.id == note_id))
            if not result.rowcount:
                raise DraftNoteNotFound(note_id)


__all__ = [
    "CollectionNotFound",
    "CollectionService",
    "DraftNoteNotFound",
    "DraftNoteService",
    "serialize_collection",
    "serialize_note",
]
