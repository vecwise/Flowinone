"""Persistence operations for the Resource Library domain."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence
from urllib.parse import urlsplit

from sqlalchemy import case, delete, func, select, text
from sqlalchemy.orm import Session, selectinload

from .canonical import classify_resource, hash_text, normalize_tag, resource_identity
from .database import ResourceDatabase
from .importers import BookmarkRecord
from .models import (
    ProcessingJob,
    Resource,
    ResourceContent,
    ResourceOrigin,
    ResourceTag,
    Tag,
    new_id,
    utc_now_text,
)


AVAILABILITY_STATES = {"unknown", "available", "dead", "blocked", "auth_required"}
TAG_SOURCES = {"user", "ai", "chrome_folder", "imported", "rule"}


class ResourceNotFound(LookupError):
    """Raised when a resource id is not present."""


@dataclass(frozen=True)
class ResourcePage:
    items: list[dict]
    total: int
    page: int
    per_page: int


def _fts_query(value: str) -> str:
    tokens = [token.strip().replace('"', "") for token in value.split() if token.strip()]
    return " AND ".join(f'"{token}"*' for token in tokens)


def _origin_dedupe_key(resource_id: str, record: BookmarkRecord) -> str:
    locator = record.source_key or record.folder_path or record.url
    raw = f"{resource_id}|{record.source}|{locator}|{record.url}"
    return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()


def _resource_options():
    return (
        selectinload(Resource.origins),
        selectinload(Resource.contents),
        selectinload(Resource.tag_links).selectinload(ResourceTag.tag),
        selectinload(Resource.ai_artifacts),
    )


def _artifact_question(artifact) -> Optional[str]:
    if artifact.artifact_type != "answer":
        return None
    try:
        payload = json.loads(artifact.content_json or "{}")
    except (TypeError, ValueError):
        return None
    return str(payload.get("question") or "").strip() or None if isinstance(payload, dict) else None


def serialize_resource(resource: Resource, *, detail: bool = False) -> dict:
    """Return a stable JSON/template representation without leaking ORM state."""
    tag_rows = sorted(
        (
            {
                "id": link.tag.id,
                "name": link.tag.name,
                "normalized_name": link.tag.normalized_name,
                "source": link.source,
                "confidence": link.confidence,
            }
            for link in resource.tag_links
        ),
        key=lambda row: (row["source"] != "user", row["name"].casefold()),
    )
    payload = {
        "id": resource.id,
        "original_url": resource.original_url,
        "canonical_url": resource.canonical_url,
        "title": resource.title or resource.canonical_url,
        "description": resource.description,
        "source_type": resource.source_type,
        "source_platform": resource.source_platform,
        "domain": resource.domain,
        "author": resource.author,
        "language": resource.language,
        "mime_type": resource.mime_type,
        "availability": resource.availability,
        "published_at": resource.published_at,
        "captured_at": resource.captured_at,
        "last_checked_at": resource.last_checked_at,
        "favicon_path": resource.favicon_path,
        "thumbnail_path": resource.thumbnail_path,
        "thumbnail_media_id": resource.thumbnail_media_id,
        "summary_one_line": resource.summary_one_line,
        "summary_short": resource.summary_short,
        "summary_structured_json": resource.summary_structured_json,
        "why_this_matters": resource.why_this_matters,
        "content_hash": resource.content_hash,
        "enrichment_status": resource.enrichment_status,
        "enrichment_error": resource.enrichment_error,
        "created_at": resource.created_at,
        "updated_at": resource.updated_at,
        "tags": tag_rows,
        "tag_names": [row["name"] for row in tag_rows],
    }
    if detail:
        payload["origins"] = [
            {
                "id": origin.id,
                "source": origin.source,
                "source_key": origin.source_key,
                "source_title": origin.source_title,
                "folder_path": origin.folder_path,
                "captured_at": origin.captured_at,
                "last_seen_at": origin.last_seen_at,
            }
            for origin in sorted(resource.origins, key=lambda row: row.captured_at)
        ]
        payload["contents"] = [
            {
                "id": content.id,
                "content_type": content.content_type,
                "content_path": content.content_path,
                "mime_type": content.mime_type,
                "content_hash": content.content_hash,
                "byte_size": content.byte_size,
                "extractor_name": content.extractor_name,
                "extractor_version": content.extractor_version,
                "created_at": content.created_at,
            }
            for content in sorted(resource.contents, key=lambda row: row.created_at, reverse=True)
        ]
        payload["ai_artifacts"] = [
            {
                "id": artifact.id,
                "artifact_type": artifact.artifact_type,
                "provider": artifact.provider,
                "model": artifact.model,
                "prompt_version": artifact.prompt_version,
                "created_at": artifact.created_at,
                "is_current": bool(artifact.is_current),
                "content_text": artifact.content_text
                if artifact.artifact_type == "answer"
                else None,
                "question": _artifact_question(artifact),
            }
            for artifact in sorted(resource.ai_artifacts, key=lambda row: row.created_at, reverse=True)
        ]
    return payload


class ResourceRepository:
    """Repository with explicit transactional methods and FTS synchronization."""

    def __init__(self, database: ResourceDatabase):
        self.database = database

    @staticmethod
    def _sync_fts(session: Session, resource: Resource, extracted_text: str = "") -> None:
        session.flush()
        session.execute(
            text("DELETE FROM resource_fts WHERE resource_id=:resource_id"),
            {"resource_id": resource.id},
        )
        session.execute(
            text(
                """
                INSERT INTO resource_fts (
                    resource_id, title, summary_one_line, summary_short,
                    why_this_matters, extracted_text
                ) VALUES (
                    :resource_id, :title, :summary_one_line, :summary_short,
                    :why_this_matters, :extracted_text
                )
                """
            ),
            {
                "resource_id": resource.id,
                "title": resource.title or "",
                "summary_one_line": resource.summary_one_line or "",
                "summary_short": resource.summary_short or "",
                "why_this_matters": resource.why_this_matters or "",
                "extracted_text": extracted_text or "",
            },
        )

    @staticmethod
    def _add_tag(
        session: Session,
        resource: Resource,
        name: str,
        source: str,
        confidence: Optional[float] = None,
    ) -> None:
        normalized = normalize_tag(name)
        if not normalized or source not in TAG_SOURCES:
            return
        tag = session.scalar(select(Tag).where(Tag.normalized_name == normalized))
        if tag is None:
            tag = Tag(name=name.strip().lstrip("#")[:120], normalized_name=normalized)
            session.add(tag)
            session.flush()
        existing = session.get(ResourceTag, (resource.id, tag.id, source))
        if existing is None:
            session.add(
                ResourceTag(
                    resource_id=resource.id,
                    tag_id=tag.id,
                    source=source,
                    confidence=confidence,
                )
            )
        elif confidence is not None:
            existing.confidence = confidence

    def upsert_bookmark(
        self,
        session: Session,
        record: BookmarkRecord,
        *,
        thumbnail_media_id: Optional[str] = None,
    ) -> tuple[Resource, bool]:
        """Create/merge one URL while preserving every source placement."""
        canonical_url, url_hash = resource_identity(record.url)
        resource = session.scalar(select(Resource).where(Resource.url_hash == url_hash))
        created = resource is None
        if resource is None:
            source_type, source_platform = classify_resource(canonical_url)
            resource = Resource(
                original_url=record.url,
                canonical_url=canonical_url,
                url_hash=url_hash,
                title=record.title or canonical_url,
                domain=(urlsplit(canonical_url).hostname or "").lower(),
                source_type=source_type,
                source_platform=source_platform,
                captured_at=record.normalized_capture_time(),
                thumbnail_media_id=thumbnail_media_id,
            )
            session.add(resource)
            session.flush()
        else:
            if not resource.title and record.title:
                resource.title = record.title
            if thumbnail_media_id and not resource.thumbnail_media_id:
                resource.thumbnail_media_id = thumbnail_media_id
            if record.normalized_capture_time() < resource.captured_at:
                resource.captured_at = record.normalized_capture_time()
            resource.updated_at = utc_now_text()

        dedupe_key = _origin_dedupe_key(resource.id, record)
        origin = session.scalar(
            select(ResourceOrigin).where(ResourceOrigin.dedupe_key == dedupe_key)
        )
        if origin is None:
            session.add(
                ResourceOrigin(
                    resource_id=resource.id,
                    source=record.source,
                    source_key=record.source_key,
                    source_title=record.title,
                    folder_path=record.folder_path or None,
                    dedupe_key=dedupe_key,
                    captured_at=record.normalized_capture_time(),
                )
            )
        else:
            origin.last_seen_at = utc_now_text()
            if record.title:
                origin.source_title = record.title

        if record.folder_path:
            leaf = record.folder_path.split(" / ")[-1].strip()
            if leaf:
                self._add_tag(session, resource, leaf, "chrome_folder")
        self._sync_fts(session, resource)
        return resource, created

    def get(self, resource_id: str) -> dict:
        with self.database.session() as session:
            resource = session.scalar(
                select(Resource).options(*_resource_options()).where(Resource.id == resource_id)
            )
            if resource is None:
                raise ResourceNotFound(resource_id)
            return serialize_resource(resource, detail=True)

    def get_by_canonical_url(self, canonical_url: str) -> dict:
        _, url_hash = resource_identity(canonical_url)
        with self.database.session() as session:
            resource = session.scalar(
                select(Resource).options(*_resource_options()).where(Resource.url_hash == url_hash)
            )
            if resource is None:
                raise ResourceNotFound(canonical_url)
            return serialize_resource(resource, detail=True)

    def list(
        self,
        *,
        query: str = "",
        source_types: Sequence[str] = (),
        tag: str = "",
        domain: str = "",
        page: int = 1,
        per_page: int = 30,
    ) -> ResourcePage:
        page = max(1, page)
        per_page = max(1, min(per_page, 100))
        with self.database.session() as session:
            conditions = []
            if source_types:
                conditions.append(Resource.source_type.in_(list(source_types)))
            if domain:
                conditions.append(Resource.domain == domain.strip().lower())
            if tag:
                normalized = normalize_tag(tag)
                conditions.append(
                    Resource.id.in_(
                        select(ResourceTag.resource_id)
                        .join(Tag, Tag.id == ResourceTag.tag_id)
                        .where(Tag.normalized_name == normalized)
                    )
                )

            ranked_ids: list[str] = []
            if query.strip():
                fts_value = _fts_query(query)
                if not fts_value:
                    return ResourcePage([], 0, page, per_page)
                ranked_ids = list(
                    session.execute(
                        text(
                            """
                            SELECT resource_id
                            FROM resource_fts
                            WHERE resource_fts MATCH :query
                            ORDER BY bm25(resource_fts)
                            LIMIT 1000
                            """
                        ),
                        {"query": fts_value},
                    ).scalars()
                )
                if not ranked_ids:
                    return ResourcePage([], 0, page, per_page)
                conditions.append(Resource.id.in_(ranked_ids))

            total = int(
                session.scalar(select(func.count(Resource.id)).where(*conditions)) or 0
            )
            statement = select(Resource).options(*_resource_options()).where(*conditions)
            if ranked_ids:
                ordering = case(
                    {resource_id: index for index, resource_id in enumerate(ranked_ids)},
                    value=Resource.id,
                    else_=len(ranked_ids),
                )
                statement = statement.order_by(ordering, Resource.captured_at.desc())
            else:
                statement = statement.order_by(
                    Resource.captured_at.desc(), Resource.created_at.desc()
                )
            resources = list(
                session.scalars(
                    statement.offset((page - 1) * per_page).limit(per_page)
                ).unique()
            )
            return ResourcePage(
                [serialize_resource(resource) for resource in resources],
                total,
                page,
                per_page,
            )

    def update(self, resource_id: str, changes: dict) -> dict:
        allowed = {
            "availability",
            "title",
        }
        with self.database.session() as session:
            resource = session.get(Resource, resource_id)
            if resource is None:
                raise ResourceNotFound(resource_id)
            filtered = {key: value for key, value in changes.items() if key in allowed}
            if "availability" in filtered and filtered["availability"] not in AVAILABILITY_STATES:
                raise ValueError("無效的連結狀態")
            for key, value in filtered.items():
                setattr(resource, key, value)
            resource.updated_at = utc_now_text()
            self._sync_fts(session, resource)
        return self.get(resource_id)

    def replace_user_tags(self, resource_id: str, names: Iterable[str]) -> dict:
        normalized_names = {
            normalize_tag(name): name.strip().lstrip("#")
            for name in names
            if normalize_tag(name)
        }
        with self.database.session() as session:
            resource = session.get(Resource, resource_id)
            if resource is None:
                raise ResourceNotFound(resource_id)
            user_links = list(
                session.scalars(
                    select(ResourceTag)
                    .options(selectinload(ResourceTag.tag))
                    .where(ResourceTag.resource_id == resource_id, ResourceTag.source == "user")
                )
            )
            for link in user_links:
                if link.tag.normalized_name not in normalized_names:
                    session.delete(link)
            for normalized, display in normalized_names.items():
                tag = session.scalar(select(Tag).where(Tag.normalized_name == normalized))
                if tag is None:
                    tag = Tag(name=display[:120], normalized_name=normalized)
                    session.add(tag)
                    session.flush()
                if session.get(ResourceTag, (resource_id, tag.id, "user")) is None:
                    session.add(ResourceTag(resource_id=resource_id, tag_id=tag.id, source="user"))
            resource.updated_at = utc_now_text()
        return self.get(resource_id)

    def remove_tag(self, resource_id: str, tag_id: str, source: str = "user") -> dict:
        if source not in TAG_SOURCES:
            raise ValueError("無效的 tag source")
        with self.database.session() as session:
            if session.get(Resource, resource_id) is None:
                raise ResourceNotFound(resource_id)
            session.execute(
                delete(ResourceTag).where(
                    ResourceTag.resource_id == resource_id,
                    ResourceTag.tag_id == tag_id,
                    ResourceTag.source == source,
                )
            )
        return self.get(resource_id)

    def delete(self, resource_id: str) -> None:
        with self.database.session() as session:
            result = session.execute(delete(Resource).where(Resource.id == resource_id))
            if not result.rowcount:
                raise ResourceNotFound(resource_id)
            session.execute(
                text("DELETE FROM resource_fts WHERE resource_id=:resource_id"),
                {"resource_id": resource_id},
            )

    def stats(self) -> dict:
        with self.database.session() as session:
            total = int(session.scalar(select(func.count(Resource.id))) or 0)
            source_types = {
                key: int(value)
                for key, value in session.execute(
                    select(Resource.source_type, func.count(Resource.id)).group_by(
                        Resource.source_type
                    )
                )
            }
            pending_jobs = int(
                session.scalar(
                    select(func.count(ProcessingJob.id)).where(
                        ProcessingJob.status.in_(("pending", "retry", "running"))
                    )
                )
                or 0
            )
        return {
            "total": total,
            "source_types": source_types,
            "pending_jobs": pending_jobs,
        }

    def latest_content_text(self, resource_id: str) -> str:
        with self.database.session() as session:
            content = session.scalar(
                select(ResourceContent)
                .where(
                    ResourceContent.resource_id == resource_id,
                    ResourceContent.content_type.in_(
                        ("article_text", "pdf_text", "video_transcript", "social_post_text")
                    ),
                )
                .order_by(ResourceContent.created_at.desc())
            )
            if content is None:
                return ""
            path = Path(content.content_path)
            try:
                return path.read_text(encoding="utf-8")
            except OSError:
                return ""

    def find_similar(self, resource_id: str, limit: int = 8) -> list[dict]:
        """Rank a bounded candidate set by explainable metadata overlap."""
        target = self.get(resource_id)
        candidates = self.list(
            page=1,
            per_page=100,
        ).items
        target_tags = {name.casefold() for name in target.get("tag_names") or []}
        target_words = {
            word.casefold()
            for word in re.findall(r"[\w\u4e00-\u9fff]{3,}", target.get("title") or "")
        }

        def score(candidate: dict) -> tuple[int, str]:
            candidate_tags = {name.casefold() for name in candidate.get("tag_names") or []}
            candidate_words = {
                word.casefold()
                for word in re.findall(r"[\w\u4e00-\u9fff]{3,}", candidate.get("title") or "")
            }
            value = 5 * len(target_tags & candidate_tags)
            value += 2 * len(target_words & candidate_words)
            value += 2 if candidate.get("source_type") == target.get("source_type") else 0
            value += 1 if candidate.get("domain") == target.get("domain") else 0
            return value, candidate.get("captured_at") or ""

        ranked = [candidate for candidate in candidates if candidate["id"] != resource_id]
        ranked.sort(key=score, reverse=True)
        return [candidate for candidate in ranked[: max(1, min(limit, 20))] if score(candidate)[0] > 0]


__all__ = [
    "AVAILABILITY_STATES",
    "ResourceNotFound",
    "ResourcePage",
    "ResourceRepository",
    "serialize_resource",
]
