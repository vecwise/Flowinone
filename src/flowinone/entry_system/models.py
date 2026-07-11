"""SQLAlchemy models for the state-based Flowinone entry system."""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.flowinone.resource_library.models import Base, new_id, utc_now_text


ENTRY_MODES = ("build", "think", "learn", "scan", "recover", "write")
ENTRY_STATUSES = ("active", "blocked", "completed", "archived")
PROJECT_STATUSES = ("active", "paused", "completed", "archived")


class Project(Base):
    """A durable context that can own multiple work entries."""

    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','paused','completed','archived')",
            name="ck_projects_status",
        ),
        Index("idx_projects_status_updated", "status", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    goal: Mapped[Optional[str]] = mapped_column(Text)
    next_action: Mapped[Optional[str]] = mapped_column(Text)
    # This deliberately is not a foreign key: an Entry references its Project and
    # SQLite table creation stays acyclic. The service validates it on writes.
    current_entry_id: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    repositories_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    notes_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    views_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_text)
    updated_at: Mapped[str] = mapped_column(
        String(40), nullable=False, default=utc_now_text, onupdate=utc_now_text
    )

    entries: Mapped[List["Entry"]] = relationship(back_populates="project")


class Entry(Base):
    """A named action starting point: target + context + state + next action."""

    __tablename__ = "entries"
    __table_args__ = (
        CheckConstraint(
            "mode IN ('build','think','learn','scan','recover','write')",
            name="ck_entries_mode",
        ),
        CheckConstraint(
            "status IN ('active','blocked','completed','archived')",
            name="ck_entries_status",
        ),
        Index("idx_entries_dashboard", "mode", "status", "last_opened_at", "updated_at"),
        Index("idx_entries_project", "project_id", "status", "updated_at"),
        Index("idx_entries_source", "source_entry_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False, default="build")
    kind: Mapped[str] = mapped_column(String(48), nullable=False, default="task_entry")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    intent: Mapped[Optional[str]] = mapped_column(Text)

    target_type: Mapped[Optional[str]] = mapped_column(String(48))
    target_uri: Mapped[Optional[str]] = mapped_column(Text)
    context_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    state_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    on_enter_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    next_action: Mapped[Optional[str]] = mapped_column(Text)
    last_action: Mapped[Optional[str]] = mapped_column(Text)
    project_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="SET NULL")
    )
    source_entry_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("entries.id", ondelete="SET NULL")
    )
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_text)
    updated_at: Mapped[str] = mapped_column(
        String(40), nullable=False, default=utc_now_text, onupdate=utc_now_text
    )
    last_opened_at: Mapped[Optional[str]] = mapped_column(String(40))
    completed_at: Mapped[Optional[str]] = mapped_column(String(40))

    project: Mapped[Optional[Project]] = relationship(back_populates="entries")
    events: Mapped[List["EntryEvent"]] = relationship(
        back_populates="entry", cascade="all, delete-orphan"
    )


class EntryEvent(Base):
    """Append-only local audit trail for creation, edits, and entry execution."""

    __tablename__ = "entry_events"
    __table_args__ = (Index("idx_entry_events_entry_created", "entry_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    entry_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("entries.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_text)

    entry: Mapped[Entry] = relationship(back_populates="events")


__all__ = [
    "ENTRY_MODES",
    "ENTRY_STATUSES",
    "PROJECT_STATUSES",
    "Entry",
    "EntryEvent",
    "Project",
]
