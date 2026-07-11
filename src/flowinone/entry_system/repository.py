"""Persistence operations for Flowinone entries, projects, and audit events."""

from __future__ import annotations

import json
from typing import Any, Iterable, Optional

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session, selectinload

from src.flowinone.resource_library.database import ResourceDatabase
from src.flowinone.resource_library.models import new_id, utc_now_text

from .models import Entry, EntryEvent, Project


class EntryNotFound(LookupError):
    """Raised when an Entry identifier does not exist."""


class ProjectNotFound(LookupError):
    """Raised when a Project identifier does not exist."""


def _load_object(value: Optional[str]) -> dict[str, Any]:
    try:
        decoded = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _load_list(value: Optional[str]) -> list[Any]:
    try:
        decoded = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return decoded if isinstance(decoded, list) else []


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def serialize_project(project: Project, *, entry_count: Optional[int] = None) -> dict[str, Any]:
    """Turn one ORM Project into a stable API/template read model."""
    return {
        "id": project.id,
        "name": project.name,
        "status": project.status,
        "goal": project.goal,
        "next_action": project.next_action,
        "current_entry_id": project.current_entry_id,
        "repositories": _load_list(project.repositories_json),
        "notes": _load_list(project.notes_json),
        "views": _load_list(project.views_json),
        "metadata": _load_object(project.metadata_json),
        "created_at": project.created_at,
        "updated_at": project.updated_at,
        "entry_count": entry_count if entry_count is not None else len(project.entries),
    }


def serialize_entry(entry: Entry, *, events: Optional[Iterable[EntryEvent]] = None) -> dict[str, Any]:
    """Turn one ORM Entry into a stable API/template read model."""
    project = None
    if entry.project is not None:
        project = {
            "id": entry.project.id,
            "name": entry.project.name,
            "status": entry.project.status,
            "current_entry_id": entry.project.current_entry_id,
        }
    payload: dict[str, Any] = {
        "id": entry.id,
        "name": entry.name,
        "mode": entry.mode,
        "kind": entry.kind,
        "status": entry.status,
        "intent": entry.intent,
        "target": {
            "type": entry.target_type,
            "uri": entry.target_uri,
        }
        if entry.target_uri
        else None,
        "context": _load_object(entry.context_json),
        "state": _load_object(entry.state_json),
        "on_enter": _load_object(entry.on_enter_json),
        "next_action": entry.next_action,
        "last_action": entry.last_action,
        "project_id": entry.project_id,
        "project": project,
        "source_entry_id": entry.source_entry_id,
        "created_at": entry.created_at,
        "updated_at": entry.updated_at,
        "last_opened_at": entry.last_opened_at,
        "completed_at": entry.completed_at,
    }
    if events is not None:
        payload["events"] = [
            {
                "id": event.id,
                "event_type": event.event_type,
                "payload": _load_object(event.payload_json),
                "created_at": event.created_at,
            }
            for event in events
        ]
    return payload


class EntryRepository:
    """Small explicit repository; normalization and policy stay in EntryService."""

    def __init__(self, database: ResourceDatabase):
        self.database = database

    @staticmethod
    def _entry_options():
        return (selectinload(Entry.project),)

    @staticmethod
    def _append_event(
        session: Session,
        entry_id: str,
        event_type: str,
        payload: Optional[dict[str, Any]] = None,
    ) -> None:
        session.add(
            EntryEvent(
                entry_id=entry_id,
                event_type=event_type[:48],
                payload_json=_dump(payload or {}),
            )
        )

    def get_entry(self, entry_id: str, *, include_events: bool = False) -> dict[str, Any]:
        with self.database.session() as session:
            entry = session.scalar(
                select(Entry).options(*self._entry_options()).where(Entry.id == entry_id)
            )
            if entry is None:
                raise EntryNotFound(entry_id)
            events = None
            if include_events:
                events = list(
                    session.scalars(
                        select(EntryEvent)
                        .where(EntryEvent.entry_id == entry_id)
                        .order_by(EntryEvent.created_at.desc())
                        .limit(50)
                    )
                )
            return serialize_entry(entry, events=events)

    def list_entries(
        self,
        *,
        mode: Optional[str] = None,
        statuses: Iterable[str] = (),
        project_id: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self.database.session() as session:
            statement = select(Entry).options(*self._entry_options())
            if mode:
                statement = statement.where(Entry.mode == mode)
            statuses = tuple(statuses)
            if statuses:
                statement = statement.where(Entry.status.in_(statuses))
            if project_id:
                statement = statement.where(Entry.project_id == project_id)
            statement = statement.order_by(
                Entry.last_opened_at.desc(), Entry.updated_at.desc(), Entry.created_at.desc()
            ).limit(max(1, min(int(limit), 250)))
            return [serialize_entry(entry) for entry in session.scalars(statement).unique()]

    def create_entry(self, values: dict[str, Any]) -> dict[str, Any]:
        with self.database.session() as session:
            entry = Entry(**values)
            session.add(entry)
            session.flush()
            self._append_event(session, entry.id, "created", {"mode": entry.mode, "kind": entry.kind})
            return serialize_entry(entry)

    def update_entry(self, entry_id: str, values: dict[str, Any]) -> dict[str, Any]:
        with self.database.session() as session:
            entry = session.scalar(
                select(Entry).options(*self._entry_options()).where(Entry.id == entry_id)
            )
            if entry is None:
                raise EntryNotFound(entry_id)
            changed = {}
            for key, value in values.items():
                if getattr(entry, key) != value:
                    setattr(entry, key, value)
                    changed[key] = value
            if entry.status == "completed" and not entry.completed_at:
                entry.completed_at = utc_now_text()
            elif entry.status != "completed":
                entry.completed_at = None
            if changed:
                entry.updated_at = utc_now_text()
                self._append_event(session, entry.id, "updated", {"fields": sorted(changed)})
            return serialize_entry(entry)

    def mark_entered(self, entry_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            entry = session.scalar(
                select(Entry).options(*self._entry_options()).where(Entry.id == entry_id)
            )
            if entry is None:
                raise EntryNotFound(entry_id)
            entry.last_opened_at = utc_now_text()
            entry.updated_at = entry.last_opened_at
            self._append_event(session, entry.id, "entered", {"target_type": entry.target_type})
            return serialize_entry(entry)

    def delete_entry(self, entry_id: str) -> None:
        with self.database.session() as session:
            entry = session.get(Entry, entry_id)
            if entry is None:
                raise EntryNotFound(entry_id)
            session.execute(
                update(Project)
                .where(Project.current_entry_id == entry_id)
                .values(current_entry_id=None, updated_at=utc_now_text())
            )
            session.execute(
                update(Entry)
                .where(Entry.source_entry_id == entry_id)
                .values(source_entry_id=None, updated_at=utc_now_text())
            )
            session.execute(delete(Entry).where(Entry.id == entry_id))

    def add_link(
        self,
        entry_id: str,
        linked_type: str,
        linked_id: str,
        relation_type: str = "source",
        metadata: Optional[dict[str, Any]] = None,
        *,
        direction: str = "source",
    ) -> None:
        """Persist one typed source/output link owned by an Entry."""
        linked_type = str(linked_type or "").strip().lower()[:40]
        linked_id = str(linked_id or "").strip()
        direction = str(direction or "source").strip().lower()
        if not linked_type or not linked_id:
            raise ValueError("Entry link 必須指定 linked_type 與 linked_id")
        if direction not in {"source", "output"}:
            raise ValueError("Entry link direction 必須是 source 或 output")
        with self.database.session() as session:
            if session.get(Entry, entry_id) is None:
                raise EntryNotFound(entry_id)
            if linked_type == "entry" and session.get(Entry, linked_id) is None:
                raise EntryNotFound(linked_id)
            session.execute(
                text(
                    """
                    INSERT OR IGNORE INTO entry_links(
                        id,entry_id,linked_type,linked_id,direction,relation_type,metadata_json,created_at
                    ) VALUES(:id,:entry,:linked_type,:linked_id,:direction,:relation,:metadata,:created)
                    """
                ),
                {
                    "id": new_id(),
                    "entry": entry_id,
                    "linked_type": linked_type,
                    "linked_id": linked_id,
                    "direction": direction,
                    "relation": relation_type[:32],
                    "metadata": _dump(metadata or {}),
                    "created": utc_now_text(),
                },
            )
            self._append_event(session, entry_id, "link_added", {"linked_type": linked_type, "linked_id": linked_id, "direction": direction, "relation_type": relation_type})

    def list_links(self, entry_id: str) -> dict[str, list[dict[str, Any]]]:
        with self.database.session() as session:
            incoming = session.execute(
                text("SELECT linked_id AS entry_id,linked_type,linked_id,direction,relation_type,metadata_json,created_at FROM entry_links WHERE entry_id=:id AND direction='source' ORDER BY created_at"),
                {"id": entry_id},
            ).mappings()
            owned_outputs = session.execute(
                text("SELECT linked_id AS entry_id,linked_type,linked_id,direction,relation_type,metadata_json,created_at FROM entry_links WHERE entry_id=:id AND direction='output' ORDER BY created_at"),
                {"id": entry_id},
            ).mappings()
            transition_outputs = session.execute(
                text("SELECT entry_id, 'entry' AS linked_type, entry_id AS linked_id, 'output' AS direction, relation_type, metadata_json, created_at FROM entry_links WHERE linked_type='entry' AND linked_id=:id AND direction='source' ORDER BY created_at"),
                {"id": entry_id},
            ).mappings()
            def rows(values):
                return [{**dict(row), "metadata": _load_object(row["metadata_json"])} for row in values]
            return {"outgoing": rows([*owned_outputs, *transition_outputs]), "incoming": rows(incoming)}

    def get_project(self, project_id: str, *, include_entries: bool = True) -> dict[str, Any]:
        with self.database.session() as session:
            options = (selectinload(Project.entries),) if include_entries else ()
            project = session.scalar(select(Project).options(*options).where(Project.id == project_id))
            if project is None:
                raise ProjectNotFound(project_id)
            payload = serialize_project(project)
            if include_entries:
                entries = sorted(
                    project.entries,
                    key=lambda entry: (entry.updated_at or "", entry.created_at or ""),
                    reverse=True,
                )
                payload["entries"] = [serialize_entry(entry) for entry in entries]
            return payload

    def list_projects(
        self,
        *,
        statuses: Iterable[str] = (),
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self.database.session() as session:
            count_label = func.count(Entry.id).label("entry_count")
            statement = select(Project, count_label).outerjoin(Entry, Entry.project_id == Project.id)
            statuses = tuple(statuses)
            if statuses:
                statement = statement.where(Project.status.in_(statuses))
            statement = (
                statement.group_by(Project.id)
                .order_by(Project.updated_at.desc(), Project.created_at.desc())
                .limit(max(1, min(int(limit), 250)))
            )
            return [
                serialize_project(project, entry_count=int(entry_count or 0))
                for project, entry_count in session.execute(statement)
            ]

    def create_project(self, values: dict[str, Any]) -> dict[str, Any]:
        with self.database.session() as session:
            project = Project(**values)
            session.add(project)
            session.flush()
            return serialize_project(project, entry_count=0)

    def update_project(self, project_id: str, values: dict[str, Any]) -> dict[str, Any]:
        with self.database.session() as session:
            project = session.get(Project, project_id)
            if project is None:
                raise ProjectNotFound(project_id)
            changed = False
            for key, value in values.items():
                if getattr(project, key) != value:
                    setattr(project, key, value)
                    changed = True
            if changed:
                project.updated_at = utc_now_text()
            return serialize_project(project)

    def set_current_entry(self, project_id: str, entry_id: Optional[str]) -> dict[str, Any]:
        with self.database.session() as session:
            project = session.get(Project, project_id)
            if project is None:
                raise ProjectNotFound(project_id)
            if entry_id:
                entry = session.get(Entry, entry_id)
                if entry is None:
                    raise EntryNotFound(entry_id)
                if entry.project_id != project_id:
                    raise ValueError("目前 Entry 必須屬於這個 Project")
            project.current_entry_id = entry_id
            project.updated_at = utc_now_text()
            return serialize_project(project)

    def delete_project(self, project_id: str) -> None:
        with self.database.session() as session:
            project = session.get(Project, project_id)
            if project is None:
                raise ProjectNotFound(project_id)
            session.execute(
                update(Entry)
                .where(Entry.project_id == project_id)
                .values(project_id=None, updated_at=utc_now_text())
            )
            session.execute(delete(Project).where(Project.id == project_id))


__all__ = [
    "EntryNotFound",
    "EntryRepository",
    "ProjectNotFound",
    "serialize_entry",
    "serialize_project",
]
