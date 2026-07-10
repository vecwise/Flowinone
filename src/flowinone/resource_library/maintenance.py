"""Explicit maintenance operations used by Flask CLI and standalone scripts."""

from __future__ import annotations

from sqlalchemy import select, update

from .database import ResourceDatabase
from .models import ProcessingJob, Resource, utc_now_text
from .obsidian import ObsidianExporter
from .repository import ResourceRepository


def rebuild_fts(database: ResourceDatabase) -> dict:
    repository = ResourceRepository(database)
    with database.session() as session:
        resource_ids = list(session.scalars(select(Resource.id)))
    rebuilt = 0
    for resource_id in resource_ids:
        database.sync_fts(
            resource_id,
            extracted_text=repository.latest_content_text(resource_id),
        )
        rebuilt += 1
    return {"rebuilt": rebuilt}


def retry_failed_jobs(database: ResourceDatabase) -> dict:
    now = utc_now_text()
    with database.session() as session:
        result = session.execute(
            update(ProcessingJob)
            .where(ProcessingJob.status == "failed")
            .values(
                status="pending",
                attempts=0,
                run_after=now,
                lease_owner=None,
                lease_expires_at=None,
                error_message=None,
                finished_at=None,
                updated_at=now,
            )
        )
        count = int(result.rowcount or 0)
    return {"retried": count}


def export_resource_mirrors(database: ResourceDatabase) -> dict:
    exporter = ObsidianExporter(database)
    with database.session() as session:
        resource_ids = list(session.scalars(select(Resource.id)))
    exported = 0
    errors = []
    for resource_id in resource_ids:
        try:
            exporter.export_resource_mirror(resource_id)
            exported += 1
        except Exception as exc:
            errors.append({"resource_id": resource_id, "error": str(exc)})
    return {"exported": exported, "failed": len(errors), "errors": errors[:50]}


__all__ = ["export_resource_mirrors", "rebuild_fts", "retry_failed_jobs"]
