"""Durable, leased background job queue shared by resource enrichers."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .canonical import hash_text
from .database import ResourceDatabase
from .models import ProcessingJob, new_id, utc_now_text


DEFAULT_JOB_PRIORITIES = {
    "fetch_metadata": 10,
    "download_thumbnail": 20,
    "extract_content": 30,
    "generate_summary": 50,
    "generate_tags": 55,
    "generate_embedding": 80,
    "export_obsidian": 90,
}


class JobQueue:
    """SQLite queue with idempotent keys, retry backoff, and expiring leases."""

    def __init__(self, database: ResourceDatabase):
        self.database = database

    def queue_in_session(
        self,
        session: Session,
        job_type: str,
        *,
        resource_id: Optional[str],
        payload: Optional[dict] = None,
        input_hash: Optional[str] = None,
        priority: Optional[int] = None,
        force: bool = False,
    ) -> ProcessingJob:
        payload_json = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)
        version = input_hash or hash_text(payload_json)[:16]
        target = resource_id or "global"
        job_key = f"{target}:{job_type}:{version}"
        job = session.scalar(select(ProcessingJob).where(ProcessingJob.job_key == job_key))
        now = utc_now_text()
        if job is None:
            job = ProcessingJob(
                id=new_id(),
                job_key=job_key,
                resource_id=resource_id,
                job_type=job_type,
                status="pending",
                priority=priority if priority is not None else DEFAULT_JOB_PRIORITIES.get(job_type, 100),
                payload_json=payload_json,
                input_hash=input_hash,
                run_after=now,
            )
            session.add(job)
        elif force or job.status in {"failed", "cancelled"}:
            job.status = "pending"
            job.attempts = 0
            job.run_after = now
            job.lease_owner = None
            job.lease_expires_at = None
            job.error_message = None
            job.finished_at = None
            job.updated_at = now
        return job

    def queue(
        self,
        job_type: str,
        *,
        resource_id: Optional[str],
        payload: Optional[dict] = None,
        input_hash: Optional[str] = None,
        priority: Optional[int] = None,
        force: bool = False,
    ) -> dict:
        with self.database.session() as session:
            job = self.queue_in_session(
                session,
                job_type,
                resource_id=resource_id,
                payload=payload,
                input_hash=input_hash,
                priority=priority,
                force=force,
            )
            session.flush()
            return self.serialize(job)

    @staticmethod
    def serialize(job: ProcessingJob | dict) -> dict:
        if isinstance(job, dict):
            payload = dict(job)
        else:
            payload = {
                column.name: getattr(job, column.name)
                for column in ProcessingJob.__table__.columns
            }
        raw_payload = payload.get("payload_json")
        try:
            payload["payload"] = json.loads(raw_payload or "{}")
        except (TypeError, ValueError):
            payload["payload"] = {}
        return payload

    def claim(self, owner: str, limit: int = 1, lease_seconds: int = 180) -> list[dict]:
        """Atomically claim ready jobs, including expired leases."""
        if limit <= 0:
            return []
        now = datetime.now(timezone.utc).replace(microsecond=0)
        now_text = now.isoformat()
        lease_until = (now + timedelta(seconds=max(30, lease_seconds))).isoformat()
        raw = self.database.engine.raw_connection()
        try:
            cursor = raw.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            rows = cursor.execute(
                """
                SELECT * FROM processing_jobs
                WHERE (
                    status IN ('pending','retry') AND run_after <= ?
                ) OR (
                    status='running' AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?
                )
                ORDER BY priority ASC, run_after ASC, created_at ASC
                LIMIT ?
                """,
                (now_text, now_text, min(limit, 50)),
            ).fetchall()
            columns = [description[0] for description in cursor.description] if cursor.description else []
            claimed: list[dict] = []
            for row in rows:
                data = dict(zip(columns, row))
                updated = cursor.execute(
                    """
                    UPDATE processing_jobs
                    SET status='running', lease_owner=?, lease_expires_at=?,
                        started_at=COALESCE(started_at, ?), updated_at=?
                    WHERE id=? AND (
                        (status IN ('pending','retry') AND run_after <= ?)
                        OR (status='running' AND lease_expires_at <= ?)
                    )
                    """,
                    (owner, lease_until, now_text, now_text, data["id"], now_text, now_text),
                )
                if updated.rowcount:
                    data.update(
                        status="running",
                        lease_owner=owner,
                        lease_expires_at=lease_until,
                        started_at=data.get("started_at") or now_text,
                    )
                    claimed.append(self.serialize(data))
            raw.commit()
            return claimed
        except Exception:
            raw.rollback()
            raise
        finally:
            raw.close()

    def complete(self, job_id: str) -> None:
        now = utc_now_text()
        with self.database.session() as session:
            job = session.get(ProcessingJob, job_id)
            if job is None:
                return
            job.status = "complete"
            job.finished_at = now
            job.updated_at = now
            job.lease_owner = None
            job.lease_expires_at = None
            job.error_message = None

    def fail(self, job_id: str, error: str) -> str:
        """Apply bounded exponential backoff and return the resulting status."""
        now = datetime.now(timezone.utc).replace(microsecond=0)
        with self.database.session() as session:
            job = session.get(ProcessingJob, job_id)
            if job is None:
                return "missing"
            job.attempts += 1
            job.error_message = (error or "Unknown processing error")[:2000]
            job.lease_owner = None
            job.lease_expires_at = None
            job.updated_at = now.isoformat()
            if job.attempts >= job.max_attempts:
                job.status = "failed"
                job.finished_at = now.isoformat()
            else:
                delays = (30, 360, 1440)
                minutes = delays[min(job.attempts - 1, len(delays) - 1)]
                job.status = "retry"
                job.run_after = (now + timedelta(minutes=minutes)).isoformat()
            return job.status

    def list_for_resource(self, resource_id: str) -> list[dict]:
        with self.database.session() as session:
            jobs = list(
                session.scalars(
                    select(ProcessingJob)
                    .where(ProcessingJob.resource_id == resource_id)
                    .order_by(ProcessingJob.created_at.desc())
                )
            )
            return [self.serialize(job) for job in jobs]

    def counts(self) -> dict[str, int]:
        with self.database.session() as session:
            rows = session.execute(
                select(ProcessingJob.status, func.count(ProcessingJob.id)).group_by(
                    ProcessingJob.status
                )
            )
            return {status: int(count) for status, count in rows}


__all__ = ["DEFAULT_JOB_PRIORITIES", "JobQueue"]
