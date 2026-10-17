"""Application service orchestrating imports, renderer metadata, and jobs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from collections import Counter
from pathlib import Path
from typing import Iterable, Optional

from src.file_handler.thumbnails.store import compute_media_id, get_thumbnail_store

from .canonical import normalize_resource_url
from .database import ResourceDatabase, get_resource_database
from .importers import BookmarkRecord, load_bookmarks
from .jobs import JobQueue
from .models import AppState, utc_now_text
from .projection import refresh_all_resource_catalog, refresh_resource_catalog
from .repository import ResourceRepository
from src.flowinone.bookmarks.identity import record_dict, reconcile_bound_bookmarks, registry_exists
from sqlalchemy import text


@dataclass
class ImportSummary:
    total: int = 0
    created: int = 0
    duplicates: int = 0
    skipped: int = 0
    conflicts: int = 0
    failed: int = 0
    jobs_queued: int = 0
    errors: list[dict] | None = None

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["errors"] = self.errors or []
        return payload


class ResourceService:
    """High-level operations kept out of Flask route handlers."""

    def __init__(
        self,
        database: Optional[ResourceDatabase] = None,
        *,
        link_thumbnail_cache: bool = True,
    ):
        self.database = database or get_resource_database()
        self.repository = ResourceRepository(self.database)
        self.jobs = JobQueue(self.database)
        self.link_thumbnail_cache = link_thumbnail_cache

    @staticmethod
    def _thumbnail_media_id(record: BookmarkRecord) -> str:
        canonical = normalize_resource_url(record.url)
        try:
            lookup = get_thumbnail_store().register_bookmark(
                record.url,
                record.title,
                {"folder_path": record.folder_path},
                enqueue_missing=False,
            )
            return lookup.media_id
        except Exception:
            return compute_media_id("bookmark", canonical)

    def import_records(
        self,
        records: Iterable[BookmarkRecord],
        *,
        enqueue: bool = True,
        link_thumbnails: Optional[bool] = None,
    ) -> ImportSummary:
        should_link_thumbnails = (
            self.link_thumbnail_cache if link_thumbnails is None else link_thumbnails
        )
        summary = ImportSummary(errors=[])
        records = list(records)
        with self.database.session(write=True) as session:
            observations = [record_dict(record) for record in records]
            source_path = next((r["source_path"] for r in observations if r.get("source_path")), "")
            if source_path and registry_exists(session.connection()) and session.execute(
                text("SELECT 1 FROM bookmark_sources WHERE bookmark_path=:path"), {"path": source_path}
            ).scalar():
                from src.flowinone.bookmarks.backfill import read_bookmarks
                complete, _ = read_bookmarks(Path(source_path))
                def identities(items):
                    return Counter((r.get("guid"), r.get("chrome_id"), r["url"], r["folder_path"])
                                   for r in items)
                if identities(complete) != identities(observations):
                    raise ValueError("Bound bookmark import needs the unchanged, complete source snapshot")
            reconciled = reconcile_bound_bookmarks(session.connection(), observations, source_path)
            summary.duplicates += reconciled["matched"]
            summary.conflicts += reconciled["conflicts"]
            summary.skipped += len(reconciled["handled"]) - reconciled["matched"] - reconciled["conflicts"]
            for index, record in enumerate(records):
                summary.total += 1
                if index in reconciled["handled"]:
                    # The approved registry alone owns these imports. Held and
                    # excluded nodes cannot fall through to URL-based inserts.
                    continue
                try:
                    created = False
                    queued_for_record = 0
                    with session.begin_nested():
                        media_id = self._thumbnail_media_id(record) if should_link_thumbnails else None
                        resource, created = self.repository.upsert_bookmark(
                            session,
                            record,
                            thumbnail_media_id=media_id,
                        )
                        if enqueue and created:
                            for job_type in (
                                "fetch_metadata",
                                "download_thumbnail",
                                "extract_content",
                            ):
                                self.jobs.queue_in_session(
                                    session,
                                    job_type,
                                    resource_id=resource.id,
                                    payload={"url": resource.canonical_url},
                                )
                                queued_for_record += 1
                    if created:
                        summary.created += 1
                    else:
                        summary.duplicates += 1
                    summary.jobs_queued += queued_for_record
                except Exception as exc:
                    summary.failed += 1
                    if len(summary.errors or []) < 50:
                        summary.errors.append({"url": record.url, "error": str(exc)})
        return summary

    def import_file(
        self,
        path: Path,
        *,
        format_hint: Optional[str] = None,
        enqueue: bool = True,
    ) -> ImportSummary:
        records = load_bookmarks(path, format_hint)
        summary = self.import_records(records, enqueue=enqueue)
        stat = path.expanduser().stat()
        with self.database.session(write=True) as session:
            key = f"import:{path.expanduser().resolve()}"
            state = session.get(AppState, key)
            value = f"{stat.st_mtime_ns}:{stat.st_size}:{utc_now_text()}"
            if state is None:
                session.add(AppState(key=key, value=value))
            else:
                state.value = value
                state.updated_at = utc_now_text()
        refresh_all_resource_catalog(self.database)
        return summary

    def import_file_if_changed(
        self,
        path: Path,
        *,
        format_hint: Optional[str] = None,
        enqueue: bool = True,
    ) -> dict:
        resolved = path.expanduser().resolve()
        stat = resolved.stat()
        key = f"import:{resolved}"
        signature = f"{stat.st_mtime_ns}:{stat.st_size}"
        with self.database.session(write=False) as session:
            state = session.get(AppState, key)
            if state and str(state.value or "").startswith(signature + ":"):
                return {"changed": False, "path": str(resolved)}
        summary = self.import_file(resolved, format_hint=format_hint, enqueue=enqueue)
        return {"changed": True, "path": str(resolved), **summary.to_dict()}

    def create_url(
        self,
        url: str,
        *,
        title: str = "",
        enqueue: bool = True,
        link_thumbnails: Optional[bool] = None,
    ) -> dict:
        record = BookmarkRecord(
            url=url,
            title=title.strip() or url,
            captured_at=utc_now_text(),
            source="manual",
        )
        summary = self.import_records(
            [record],
            enqueue=enqueue,
            link_thumbnails=link_thumbnails,
        )
        canonical = normalize_resource_url(url)
        resource = self.repository.get_by_canonical_url(canonical)
        refresh_resource_catalog(self.database, resource["id"])
        return {"resource": resource, "import": summary.to_dict()}

    def update_resource(self, resource_id: str, changes: dict) -> dict:
        resource = self.repository.update(resource_id, changes)
        refresh_resource_catalog(self.database, resource_id)
        return resource

    def replace_tags(self, resource_id: str, tags: Iterable[str]) -> dict:
        resource = self.repository.replace_user_tags(resource_id, tags)
        refresh_resource_catalog(self.database, resource_id)
        return resource

    def enqueue_enrichment(self, resource_id: str, *, include_ai: bool = False, force: bool = False) -> list[dict]:
        resource = self.repository.get(resource_id)
        queued = []
        for job_type in ("fetch_metadata", "download_thumbnail", "extract_content"):
            queued.append(
                self.jobs.queue(
                    job_type,
                    resource_id=resource_id,
                    payload={"url": resource["canonical_url"]},
                    force=force,
                )
            )
        if include_ai:
            queued.append(
                self.jobs.queue(
                    "generate_summary",
                    resource_id=resource_id,
                    payload={"url": resource["canonical_url"]},
                    input_hash=resource.get("content_hash"),
                    force=force,
                )
            )
        return queued

    def enqueue_selected_enrichment(self, resource_ids: Iterable[str]) -> int:
        """Queue a bounded, explicitly selected set of unprocessed resources."""
        selected = list(dict.fromkeys(str(resource_id).strip() for resource_id in resource_ids if str(resource_id).strip()))
        if not selected or len(selected) > 50:
            raise ValueError("請選擇 1–50 筆資源")
        resources = [self.repository.get(resource_id) for resource_id in selected]
        pending = [resource for resource in resources if resource["enrichment_status"] != "complete"]
        if not pending:
            raise ValueError("所選資源已完成擷取")
        for resource in pending:
            self.enqueue_enrichment(resource["id"])
        return len(pending)


__all__ = ["ImportSummary", "ResourceService"]
