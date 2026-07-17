"""Metadata, content, preview, and optional AI enrichment handlers."""

from __future__ import annotations

import hashlib
import io
import json
import os
import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

import fitz
from PIL import Image
from sqlalchemy import select, update

from src.file_handler.thumbnails.store import get_thumbnail_store
from src.file_handler.thumbnails.worker import DomainRateLimiter, SafeHTTPClient

from .ai import AIUnavailable, OpenAICompatibleClient
from .canonical import hash_text, hash_url, normalize_resource_url
from .database import ResourceDatabase, get_resource_database
from .extractors import ExtractedContent, ExtractedMetadata, extractor_for
from .jobs import JobQueue
from .models import AIArtifact, Resource, ResourceContent, ResourceTag, new_id, utc_now_text
from .repository import ResourceNotFound, ResourceRepository
from .settings import ResourceSettings, get_resource_settings


class EnrichmentError(RuntimeError):
    """Raised when a job cannot produce a trustworthy artifact."""


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


class EnrichmentService:
    """Execute one durable job while preserving provenance and local artifacts."""

    def __init__(
        self,
        database: Optional[ResourceDatabase] = None,
        settings: Optional[ResourceSettings] = None,
    ):
        self.database = database or get_resource_database()
        self.settings = settings or get_resource_settings()
        self.settings.ensure_directories()
        self.repository = ResourceRepository(self.database)
        self.jobs = JobQueue(self.database)
        self.ai = OpenAICompatibleClient(self.settings)

    def _resource_model(self, resource_id: str) -> Resource:
        with self.database.session() as session:
            resource = session.get(Resource, resource_id)
            if resource is None:
                raise ResourceNotFound(resource_id)
            session.expunge(resource)
            return resource

    def _content_path(self, resource_id: str, category: str, digest: str, suffix: str) -> Path:
        return (
            self.settings.content_dir
            / category
            / resource_id
            / f"{digest}{suffix}"
        ).resolve()

    @staticmethod
    def _record_content(
        session,
        *,
        resource_id: str,
        content_type: str,
        path: Path,
        mime_type: str,
        digest: str,
        extractor_name: str,
        extractor_version: str,
    ) -> None:
        existing = session.scalar(
            select(ResourceContent).where(
                ResourceContent.resource_id == resource_id,
                ResourceContent.content_type == content_type,
                ResourceContent.content_hash == digest,
            )
        )
        if existing is None:
            session.add(
                ResourceContent(
                    id=new_id(),
                    resource_id=resource_id,
                    content_type=content_type,
                    content_path=str(path),
                    mime_type=mime_type,
                    content_hash=digest,
                    byte_size=path.stat().st_size if path.exists() else None,
                    extractor_name=extractor_name,
                    extractor_version=extractor_version,
                )
            )

    def _download_favicon(self, resource_id: str, url: str, domain: str) -> Optional[str]:
        if not url:
            return None
        client = SafeHTTPClient(DomainRateLimiter(interval_seconds=0.5))
        try:
            payload, _, _ = client.fetch_bytes(
                url,
                max_bytes=1_500_000,
                accept="image/avif,image/webp,image/png,image/x-icon,image/*,*/*;q=0.1",
            )
            with Image.open(io.BytesIO(payload)) as image:
                image.seek(0)
                converted = image.convert("RGBA")
                converted.thumbnail((128, 128), Image.Resampling.LANCZOS)
                output = io.BytesIO()
                converted.save(output, format="WEBP", quality=82, method=6)
        except Exception:
            return None
        domain_key = hashlib.sha256((domain or resource_id).encode("utf-8", "ignore")).hexdigest()
        path = (self.settings.data_dir / "assets" / "favicons" / f"{domain_key}.webp").resolve()
        _atomic_write(path, output.getvalue())
        return str(path)

    def _render_pdf_thumbnail(self, resource_id: str, payload: bytes) -> Optional[str]:
        try:
            document = fitz.open(stream=payload, filetype="pdf")
            if document.page_count < 1:
                document.close()
                return None
            page = document.load_page(0)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            png = pixmap.tobytes("png")
            document.close()
            with Image.open(io.BytesIO(png)) as image:
                converted = image.convert("RGB")
                converted.thumbnail((640, 640), Image.Resampling.LANCZOS)
                output = io.BytesIO()
                converted.save(output, format="WEBP", quality=82, method=6)
        except Exception:
            return None
        path = (self.settings.data_dir / "assets" / "thumbnails" / f"{resource_id}.webp").resolve()
        _atomic_write(path, output.getvalue())
        return str(path)

    def fetch_metadata(self, resource_id: str) -> ExtractedMetadata:
        resource = self._resource_model(resource_id)
        extractor = extractor_for(
            resource.canonical_url,
            resource.source_type,
            resource.source_platform,
            max_bytes=self.settings.max_download_bytes,
        )
        metadata = extractor.fetch_metadata(resource.canonical_url)
        favicon_path = None
        if metadata.favicon_url:
            favicon_path = self._download_favicon(
                resource_id,
                metadata.favicon_url,
                resource.domain or "",
            )

        with self.database.session() as session:
            stored = session.get(Resource, resource_id)
            if stored is None:
                raise ResourceNotFound(resource_id)
            if metadata.canonical_url:
                try:
                    canonical = normalize_resource_url(metadata.canonical_url)
                    candidate_hash = hash_url(canonical)
                    collision = session.scalar(
                        select(Resource.id).where(
                            Resource.url_hash == candidate_hash,
                            Resource.id != resource_id,
                        )
                    )
                    if collision is None:
                        stored.canonical_url = canonical
                        stored.url_hash = candidate_hash
                        stored.domain = (urlsplit(canonical).hostname or stored.domain or "").lower()
                except ValueError:
                    pass
            if metadata.title:
                stored.title = metadata.title
            stored.description = metadata.description or stored.description
            stored.author = metadata.author or stored.author
            stored.published_at = metadata.published_at or stored.published_at
            stored.language = metadata.language or stored.language
            stored.mime_type = metadata.mime_type or stored.mime_type
            stored.source_type = metadata.source_type or stored.source_type
            stored.source_platform = metadata.source_platform or stored.source_platform
            stored.favicon_path = favicon_path or stored.favicon_path
            stored.availability = "available"
            stored.last_checked_at = utc_now_text()
            stored.enrichment_status = "processing"
            stored.enrichment_error = None
            stored.updated_at = utc_now_text()
            self.repository._sync_fts(session, stored)
        from src.flowinone.catalog.service import CatalogSyncService
        CatalogSyncService(self.database).sync_resource(resource_id)
        return metadata

    def extract_content(self, resource_id: str) -> dict:
        resource = self._resource_model(resource_id)
        extractor = extractor_for(
            resource.canonical_url,
            resource.source_type,
            resource.source_platform,
            max_bytes=self.settings.max_download_bytes,
        )
        extracted: ExtractedContent = extractor.extract_content(resource.canonical_url)
        if len(extracted.text.strip()) < 40:
            raise EnrichmentError("抽取到的可信文字不足")

        text_payload = extracted.text.strip().encode("utf-8")
        text_digest = hashlib.sha256(text_payload).hexdigest()
        text_path = self._content_path(resource_id, "text", text_digest, ".txt")
        _atomic_write(text_path, text_payload)

        markdown_path = None
        markdown_digest = None
        if extracted.markdown.strip():
            markdown_payload = extracted.markdown.strip().encode("utf-8")
            markdown_digest = hashlib.sha256(markdown_payload).hexdigest()
            markdown_path = self._content_path(resource_id, "markdown", markdown_digest, ".md")
            _atomic_write(markdown_path, markdown_payload)

        raw_path = None
        raw_digest = None
        raw_type = None
        thumbnail_path = None
        if extracted.raw_bytes:
            raw_digest = hashlib.sha256(extracted.raw_bytes).hexdigest()
            is_pdf = "pdf" in (extracted.raw_content_type or "").lower() or extracted.content_type == "pdf_text"
            is_markdown = "markdown" in (extracted.raw_content_type or "").lower()
            raw_type = "raw_pdf" if is_pdf else "raw_markdown" if is_markdown else "raw_html"
            suffix = ".pdf" if is_pdf else ".md" if is_markdown else ".html"
            category = "pdf" if is_pdf else "markdown" if is_markdown else "html"
            raw_path = self._content_path(resource_id, category, raw_digest, suffix)
            _atomic_write(raw_path, extracted.raw_bytes)
            if is_pdf:
                thumbnail_path = self._render_pdf_thumbnail(resource_id, extracted.raw_bytes)

        with self.database.session() as session:
            stored = session.get(Resource, resource_id)
            if stored is None:
                raise ResourceNotFound(resource_id)
            self._record_content(
                session,
                resource_id=resource_id,
                content_type=extracted.content_type,
                path=text_path,
                mime_type="text/plain",
                digest=text_digest,
                extractor_name=extracted.extractor_name,
                extractor_version=extracted.extractor_version,
            )
            if markdown_path and markdown_digest:
                self._record_content(
                    session,
                    resource_id=resource_id,
                    content_type="article_markdown",
                    path=markdown_path,
                    mime_type="text/markdown",
                    digest=markdown_digest,
                    extractor_name=extracted.extractor_name,
                    extractor_version=extracted.extractor_version,
                )
            if raw_path and raw_digest and raw_type:
                self._record_content(
                    session,
                    resource_id=resource_id,
                    content_type=raw_type,
                    path=raw_path,
                    mime_type=extracted.raw_content_type or "application/octet-stream",
                    digest=raw_digest,
                    extractor_name=extracted.extractor_name,
                    extractor_version=extracted.extractor_version,
                )
            stored.content_hash = text_digest
            stored.thumbnail_path = thumbnail_path or stored.thumbnail_path
            stored.enrichment_status = "complete"
            stored.enrichment_error = None
            stored.updated_at = utc_now_text()
            self.repository._sync_fts(session, stored, extracted.text)
            if self.ai.available:
                self.jobs.queue_in_session(
                    session,
                    "generate_summary",
                    resource_id=resource_id,
                    payload={"url": stored.canonical_url},
                    input_hash=text_digest,
                )
        from src.flowinone.catalog.service import CatalogSyncService
        CatalogSyncService(self.database).sync_resource(resource_id)
        return {
            "text_path": str(text_path),
            "markdown_path": str(markdown_path) if markdown_path else None,
            "raw_path": str(raw_path) if raw_path else None,
            "content_hash": text_digest,
            "characters": len(extracted.text),
        }

    def enqueue_thumbnail(self, resource_id: str) -> dict:
        resource = self.repository.get(resource_id)
        lookup = get_thumbnail_store().register_bookmark(
            resource["original_url"],
            resource["title"],
            {"resource_id": resource_id},
            enqueue_missing=True,
        )
        with self.database.session() as session:
            stored = session.get(Resource, resource_id)
            if stored:
                stored.thumbnail_media_id = lookup.media_id
                stored.updated_at = utc_now_text()
        return {
            "media_id": lookup.media_id,
            "status": lookup.status,
            "route": lookup.route,
        }

    def generate_ai(self, resource_id: str) -> dict:
        resource = self.repository.get(resource_id)
        content = self.repository.latest_content_text(resource_id)
        result = self.ai.enrich(
            title=resource["title"],
            url=resource["canonical_url"],
            content=content,
        )
        artifact_payload = {
            "summary_one_line": result.summary_one_line,
            "summary_short": result.summary_short,
            "why_this_matters": result.why_this_matters,
            "structured": result.structured,
            "tags": result.tags,
        }
        with self.database.session() as session:
            stored = session.get(Resource, resource_id)
            if stored is None:
                raise ResourceNotFound(resource_id)
            session.execute(
                update(AIArtifact)
                .where(
                    AIArtifact.resource_id == resource_id,
                    AIArtifact.artifact_type == "resource_summary",
                )
                .values(is_current=0)
            )
            session.add(
                AIArtifact(
                    id=new_id(),
                    resource_id=resource_id,
                    artifact_type="resource_summary",
                    content_json=json.dumps(artifact_payload, ensure_ascii=False),
                    provider=result.provider,
                    model=result.model,
                    prompt_version=result.prompt_version,
                    input_hash=stored.content_hash,
                    is_current=1,
                )
            )
            stored.summary_one_line = result.summary_one_line
            stored.summary_short = result.summary_short
            stored.why_this_matters = result.why_this_matters
            stored.summary_structured_json = json.dumps(result.structured, ensure_ascii=False)
            stored.enrichment_error = None
            stored.updated_at = utc_now_text()
            for tag_name in result.tags:
                self.repository._add_tag(session, stored, tag_name, "ai")
            self.repository._sync_fts(session, stored, content)
        from src.flowinone.catalog.service import CatalogSyncService
        CatalogSyncService(self.database).sync_resource(resource_id)
        return artifact_payload

    def ask_question(self, resource_id: str, question: str) -> dict:
        resource = self.repository.get(resource_id)
        content = self.repository.latest_content_text(resource_id)
        result = self.ai.ask(
            title=resource["title"],
            url=resource["canonical_url"],
            content=content,
            question=question,
        )
        with self.database.session() as session:
            session.add(
                AIArtifact(
                    id=new_id(),
                    resource_id=resource_id,
                    artifact_type="answer",
                    content_text=result.answer,
                    content_json=json.dumps({"question": result.question}, ensure_ascii=False),
                    provider=result.provider,
                    model=result.model,
                    prompt_version=result.prompt_version,
                    input_hash=hash_text(f"{resource.get('content_hash') or ''}|{result.question}"),
                    is_current=1,
                )
            )
        return {
            "question": result.question,
            "answer": result.answer,
            "provider": result.provider,
            "model": result.model,
        }

    def process_job(self, job: dict) -> dict:
        resource_id = str(job.get("resource_id") or "")
        if not resource_id:
            raise EnrichmentError("此 job 缺少 resource_id")
        job_type = str(job.get("job_type") or "")
        if job_type == "fetch_metadata":
            metadata = self.fetch_metadata(resource_id)
            return {"title": metadata.title, "source_type": metadata.source_type}
        if job_type == "download_thumbnail":
            return self.enqueue_thumbnail(resource_id)
        if job_type == "extract_content":
            return self.extract_content(resource_id)
        if job_type in {"generate_summary", "generate_tags"}:
            return self.generate_ai(resource_id)
        raise EnrichmentError(f"不支援的 job_type: {job_type}")

    def record_failure(self, resource_id: Optional[str], error: str) -> None:
        if not resource_id:
            return
        with self.database.session() as session:
            resource = session.get(Resource, resource_id)
            if resource is None:
                return
            safe_error = (error or "Unknown enrichment error")[:2000]
            resource.enrichment_status = "failed"
            resource.enrichment_error = safe_error
            resource.last_checked_at = utc_now_text()
            if "HTTP 404" in safe_error or "HTTP 410" in safe_error:
                resource.availability = "dead"
            elif "HTTP 401" in safe_error or "HTTP 403" in safe_error:
                resource.availability = "auth_required"
            resource.updated_at = utc_now_text()


__all__ = ["EnrichmentError", "EnrichmentService"]
