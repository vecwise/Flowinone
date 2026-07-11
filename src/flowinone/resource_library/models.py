"""SQLAlchemy models for resources, mixed-source curation, and notes."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import (
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now_text() -> str:
    """Return an ISO-8601 UTC timestamp with second precision."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_id() -> str:
    """Return an opaque stable identifier suitable for SQLite text keys."""
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    """Declarative base for the Resource Library schema."""


class Resource(Base):
    __tablename__ = "resources"
    __table_args__ = (
        CheckConstraint(
            "reading_state IN ('inbox','unread','skimmed','reading','digested')",
            name="ck_resources_reading_state",
        ),
        CheckConstraint(
            "disposition IN ('active','archived','rejected')",
            name="ck_resources_disposition",
        ),
        CheckConstraint(
            "availability IN ('unknown','available','dead','blocked','auth_required')",
            name="ck_resources_availability",
        ),
        CheckConstraint("priority BETWEEN 0 AND 5", name="ck_resources_priority"),
        Index("idx_resources_workflow", "disposition", "reading_state", "priority"),
        Index("idx_resources_domain", "domain"),
        Index("idx_resources_source_type", "source_type"),
        Index("idx_resources_captured_at", "captured_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    original_url: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    url_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    title: Mapped[Optional[str]] = mapped_column(Text)
    description: Mapped[Optional[str]] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    source_platform: Mapped[Optional[str]] = mapped_column(String(64))
    domain: Mapped[Optional[str]] = mapped_column(String(255))
    author: Mapped[Optional[str]] = mapped_column(Text)
    language: Mapped[Optional[str]] = mapped_column(String(32))
    mime_type: Mapped[Optional[str]] = mapped_column(String(255))

    reading_state: Mapped[str] = mapped_column(String(20), nullable=False, default="inbox")
    disposition: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    availability: Mapped[str] = mapped_column(String(20), nullable=False, default="unknown")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    saved_reason: Mapped[Optional[str]] = mapped_column(Text)

    published_at: Mapped[Optional[str]] = mapped_column(String(40))
    captured_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_text)
    last_checked_at: Mapped[Optional[str]] = mapped_column(String(40))
    read_at: Mapped[Optional[str]] = mapped_column(String(40))

    favicon_path: Mapped[Optional[str]] = mapped_column(Text)
    thumbnail_path: Mapped[Optional[str]] = mapped_column(Text)
    screenshot_path: Mapped[Optional[str]] = mapped_column(Text)
    thumbnail_media_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)

    summary_one_line: Mapped[Optional[str]] = mapped_column(Text)
    summary_short: Mapped[Optional[str]] = mapped_column(Text)
    summary_structured_json: Mapped[Optional[str]] = mapped_column(Text)
    why_this_matters: Mapped[Optional[str]] = mapped_column(Text)
    user_note: Mapped[Optional[str]] = mapped_column(Text)

    content_hash: Mapped[Optional[str]] = mapped_column(String(64))
    enrichment_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    enrichment_error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_text)
    updated_at: Mapped[str] = mapped_column(
        String(40), nullable=False, default=utc_now_text, onupdate=utc_now_text
    )

    origins: Mapped[List["ResourceOrigin"]] = relationship(
        back_populates="resource", cascade="all, delete-orphan"
    )
    contents: Mapped[List["ResourceContent"]] = relationship(
        back_populates="resource", cascade="all, delete-orphan"
    )
    tag_links: Mapped[List["ResourceTag"]] = relationship(
        back_populates="resource", cascade="all, delete-orphan"
    )
    jobs: Mapped[List["ProcessingJob"]] = relationship(
        back_populates="resource", cascade="all, delete-orphan"
    )
    ai_artifacts: Mapped[List["AIArtifact"]] = relationship(
        back_populates="resource", cascade="all, delete-orphan"
    )
    project_links: Mapped[List["ResourceProject"]] = relationship(
        back_populates="resource", cascade="all, delete-orphan"
    )
    mode_links: Mapped[List["ResourceMode"]] = relationship(
        back_populates="resource", cascade="all, delete-orphan"
    )


class ResourceOrigin(Base):
    __tablename__ = "resource_origins"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_resource_origins_dedupe_key"),
        Index("idx_resource_origins_resource", "resource_id"),
        Index("idx_resource_origins_source", "source", "folder_path"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    resource_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("resources.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    source_key: Mapped[Optional[str]] = mapped_column(Text)
    source_title: Mapped[Optional[str]] = mapped_column(Text)
    folder_path: Mapped[Optional[str]] = mapped_column(Text)
    dedupe_key: Mapped[str] = mapped_column(String(64), nullable=False)
    captured_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_text)
    first_seen_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_text)
    last_seen_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_text)

    resource: Mapped[Resource] = relationship(back_populates="origins")


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    category: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_text)

    resource_links: Mapped[List["ResourceTag"]] = relationship(
        back_populates="tag", cascade="all, delete-orphan"
    )


class ResourceTag(Base):
    __tablename__ = "resource_tags"
    __table_args__ = (
        CheckConstraint(
            "source IN ('user','ai','chrome_folder','imported','rule')",
            name="ck_resource_tags_source",
        ),
    )

    resource_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("resources.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )
    source: Mapped[str] = mapped_column(String(24), primary_key=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_text)

    resource: Mapped[Resource] = relationship(back_populates="tag_links")
    tag: Mapped[Tag] = relationship(back_populates="resource_links")


class ResourceContent(Base):
    __tablename__ = "resource_contents"
    __table_args__ = (
        Index("idx_resource_contents_resource_type", "resource_id", "content_type"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    resource_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("resources.id", ondelete="CASCADE"), nullable=False
    )
    content_type: Mapped[str] = mapped_column(String(40), nullable=False)
    content_path: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[Optional[str]] = mapped_column(String(255))
    content_hash: Mapped[Optional[str]] = mapped_column(String(64))
    byte_size: Mapped[Optional[int]] = mapped_column(Integer)
    extractor_name: Mapped[Optional[str]] = mapped_column(String(120))
    extractor_version: Mapped[Optional[str]] = mapped_column(String(40))
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_text)

    resource: Mapped[Resource] = relationship(back_populates="contents")


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','running','retry','complete','failed','cancelled')",
            name="ck_processing_jobs_status",
        ),
        UniqueConstraint("job_key", name="uq_processing_jobs_job_key"),
        Index("idx_processing_jobs_ready", "status", "run_after", "priority"),
        Index("idx_processing_jobs_resource", "resource_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    job_key: Mapped[str] = mapped_column(String(160), nullable=False)
    resource_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("resources.id", ondelete="CASCADE")
    )
    job_type: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    payload_json: Mapped[Optional[str]] = mapped_column(Text)
    input_hash: Mapped[Optional[str]] = mapped_column(String(64))
    run_after: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_text)
    lease_owner: Mapped[Optional[str]] = mapped_column(String(120))
    lease_expires_at: Mapped[Optional[str]] = mapped_column(String(40))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_text)
    updated_at: Mapped[str] = mapped_column(
        String(40), nullable=False, default=utc_now_text, onupdate=utc_now_text
    )
    started_at: Mapped[Optional[str]] = mapped_column(String(40))
    finished_at: Mapped[Optional[str]] = mapped_column(String(40))

    resource: Mapped[Optional[Resource]] = relationship(back_populates="jobs")


class AIArtifact(Base):
    __tablename__ = "ai_artifacts"
    __table_args__ = (
        Index("idx_ai_artifacts_resource_type", "resource_id", "artifact_type"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    resource_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("resources.id", ondelete="CASCADE"), nullable=False
    )
    artifact_type: Mapped[str] = mapped_column(String(48), nullable=False)
    content_text: Mapped[Optional[str]] = mapped_column(Text)
    content_json: Mapped[Optional[str]] = mapped_column(Text)
    provider: Mapped[Optional[str]] = mapped_column(String(120))
    model: Mapped[Optional[str]] = mapped_column(String(160))
    prompt_version: Mapped[Optional[str]] = mapped_column(String(40))
    input_hash: Mapped[Optional[str]] = mapped_column(String(64))
    is_current: Mapped[bool] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_text)

    resource: Mapped[Resource] = relationship(back_populates="ai_artifacts")


class Collection(Base):
    __tablename__ = "collections"
    __table_args__ = (
        CheckConstraint("kind IN ('inspiration','project','reading_list')", name="ck_collections_kind"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(24), nullable=False, default="inspiration")
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_text)
    updated_at: Mapped[str] = mapped_column(
        String(40), nullable=False, default=utc_now_text, onupdate=utc_now_text
    )

    items: Mapped[List["CollectionItem"]] = relationship(
        back_populates="collection", cascade="all, delete-orphan"
    )


class CollectionItem(Base):
    __tablename__ = "collection_items"
    __table_args__ = (
        UniqueConstraint("collection_id", "source_kind", "source_id", name="uq_collection_item_source"),
        Index("idx_collection_items_order", "collection_id", "position", "added_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    collection_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("collections.id", ondelete="CASCADE"), nullable=False
    )
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    title_snapshot: Mapped[Optional[str]] = mapped_column(Text)
    url_snapshot: Mapped[Optional[str]] = mapped_column(Text)
    thumbnail_snapshot: Mapped[Optional[str]] = mapped_column(Text)
    annotation: Mapped[Optional[str]] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    added_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_text)

    collection: Mapped[Collection] = relationship(back_populates="items")


class DraftNote(Base):
    __tablename__ = "draft_notes"
    __table_args__ = (
        CheckConstraint("note_type IN ('literature','synthesis')", name="ck_draft_notes_type"),
        CheckConstraint("status IN ('draft','exported')", name="ck_draft_notes_status"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    note_type: Mapped[str] = mapped_column(String(24), nullable=False, default="synthesis")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    obsidian_path: Mapped[Optional[str]] = mapped_column(Text)
    export_hash: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_text)
    updated_at: Mapped[str] = mapped_column(
        String(40), nullable=False, default=utc_now_text, onupdate=utc_now_text
    )

    sources: Mapped[List["DraftNoteSource"]] = relationship(
        back_populates="note", cascade="all, delete-orphan"
    )
    exports: Mapped[List["NoteExport"]] = relationship(
        back_populates="note", cascade="all, delete-orphan"
    )


class DraftNoteSource(Base):
    __tablename__ = "draft_note_sources"
    __table_args__ = (
        UniqueConstraint("draft_note_id", "source_kind", "source_id", name="uq_draft_note_source"),
        Index("idx_draft_note_sources_order", "draft_note_id", "position"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    draft_note_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("draft_notes.id", ondelete="CASCADE"), nullable=False
    )
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    citation_label: Mapped[Optional[str]] = mapped_column(Text)
    url_snapshot: Mapped[Optional[str]] = mapped_column(Text)
    annotation: Mapped[Optional[str]] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    added_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_text)

    note: Mapped[DraftNote] = relationship(back_populates="sources")


class ResourceNoteLink(Base):
    __tablename__ = "resource_note_links"

    resource_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("resources.id", ondelete="CASCADE"), primary_key=True
    )
    draft_note_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("draft_notes.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(32), primary_key=True, default="source")
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_text)


class NoteExport(Base):
    __tablename__ = "note_exports"
    __table_args__ = (Index("idx_note_exports_note", "draft_note_id", "exported_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    draft_note_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("draft_notes.id", ondelete="CASCADE"), nullable=False
    )
    target_path: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="complete")
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    exported_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_text)

    note: Mapped[DraftNote] = relationship(back_populates="exports")


class AppState(Base):
    __tablename__ = "resource_state"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[Optional[str]] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(
        String(40), nullable=False, default=utc_now_text, onupdate=utc_now_text
    )


class ResourceProject(Base):
    """Manual/AI relevance between a raw Resource and a durable Project context."""

    __tablename__ = "resource_projects"
    __table_args__ = (
        CheckConstraint("source IN ('user','ai','system')", name="ck_resource_projects_source"),
        Index("idx_resource_projects_lookup", "project_id", "relevance"),
    )

    resource_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("resources.id", ondelete="CASCADE"), primary_key=True
    )
    project_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    relevance: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="user")
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_text)

    resource: Mapped[Resource] = relationship(back_populates="project_links")


class ResourceMode(Base):
    """Retrieval relevance between a Resource and one cognitive mode."""

    __tablename__ = "resource_modes"
    __table_args__ = (
        CheckConstraint(
            "mode IN ('build','think','learn','scan','recover','write')",
            name="ck_resource_modes_mode",
        ),
        CheckConstraint("source IN ('user','ai','system')", name="ck_resource_modes_source"),
        Index("idx_resource_modes_lookup", "mode", "relevance"),
    )

    resource_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("resources.id", ondelete="CASCADE"), primary_key=True
    )
    mode: Mapped[str] = mapped_column(String(20), primary_key=True)
    relevance: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="user")
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_text)

    resource: Mapped[Resource] = relationship(back_populates="mode_links")


class OutputAsset(Base):
    """Layer-3 deliverable with explicit provenance."""

    __tablename__ = "output_assets"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','ready','exported','archived')",
            name="ck_output_assets_status",
        ),
        Index("idx_output_assets_project_status", "project_id", "status", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    asset_type: Mapped[str] = mapped_column(String(48), nullable=False)
    project_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    version: Mapped[str] = mapped_column(String(40), nullable=False, default="0.1")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    export_path: Mapped[Optional[str]] = mapped_column(Text)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_text)
    updated_at: Mapped[str] = mapped_column(
        String(40), nullable=False, default=utc_now_text, onupdate=utc_now_text
    )

    sources: Mapped[List["AssetSource"]] = relationship(
        back_populates="asset", cascade="all, delete-orphan"
    )


class AssetSource(Base):
    """Polymorphic provenance link for an Output Asset."""

    __tablename__ = "asset_sources"
    __table_args__ = (Index("idx_asset_sources_reverse", "source_type", "source_id"),)

    asset_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("output_assets.id", ondelete="CASCADE"), primary_key=True
    )
    source_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    source_id: Mapped[str] = mapped_column(Text, primary_key=True)
    citation_label: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_text)

    asset: Mapped[OutputAsset] = relationship(back_populates="sources")


class Decision(Base):
    """Human-owned project decision; AI may assist but must not overwrite it."""

    __tablename__ = "decisions"
    __table_args__ = (Index("idx_decisions_project_created", "project_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="CASCADE")
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[Optional[str]] = mapped_column(Text)
    alternatives_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_text)


__all__ = [
    "AIArtifact",
    "AppState",
    "AssetSource",
    "Base",
    "Collection",
    "CollectionItem",
    "DraftNote",
    "DraftNoteSource",
    "Decision",
    "NoteExport",
    "ProcessingJob",
    "OutputAsset",
    "Resource",
    "ResourceContent",
    "ResourceNoteLink",
    "ResourceOrigin",
    "ResourceMode",
    "ResourceProject",
    "ResourceTag",
    "Tag",
    "new_id",
    "utc_now_text",
]
