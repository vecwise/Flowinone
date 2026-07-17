"""SQLAlchemy models for renderer-owned Resource Library data."""

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
            "availability IN ('unknown','available','dead','blocked','auth_required')",
            name="ck_resources_availability",
        ),
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

    availability: Mapped[str] = mapped_column(String(20), nullable=False, default="unknown")

    published_at: Mapped[Optional[str]] = mapped_column(String(40))
    captured_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now_text)
    last_checked_at: Mapped[Optional[str]] = mapped_column(String(40))

    favicon_path: Mapped[Optional[str]] = mapped_column(Text)
    thumbnail_path: Mapped[Optional[str]] = mapped_column(Text)
    screenshot_path: Mapped[Optional[str]] = mapped_column(Text)
    thumbnail_media_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)

    summary_one_line: Mapped[Optional[str]] = mapped_column(Text)
    summary_short: Mapped[Optional[str]] = mapped_column(Text)
    summary_structured_json: Mapped[Optional[str]] = mapped_column(Text)
    why_this_matters: Mapped[Optional[str]] = mapped_column(Text)

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


class AppState(Base):
    """Small renderer operational state, such as import change signatures."""

    __tablename__ = "resource_state"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[Optional[str]] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(
        String(40), nullable=False, default=utc_now_text, onupdate=utc_now_text
    )


__all__ = [
    "AIArtifact",
    "AppState",
    "Base",
    "ProcessingJob",
    "Resource",
    "ResourceContent",
    "ResourceOrigin",
    "ResourceTag",
    "Tag",
    "new_id",
    "utc_now_text",
]
