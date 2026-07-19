"""Explicit maintenance operations used by Flask CLI and standalone scripts."""

from __future__ import annotations

from sqlalchemy import update

from .database import ResourceDatabase
from .models import ProcessingJob, utc_now_text
from .repository import ResourceRepository


def rebuild_fts(database: ResourceDatabase) -> dict:
    repository = ResourceRepository(database)
    with database.session(write=True) as session:
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
    with database.session(write=True) as session:
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


__all__ = ["rebuild_fts", "retry_failed_jobs"]
