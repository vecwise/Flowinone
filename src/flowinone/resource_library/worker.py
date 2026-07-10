"""Background runtime for durable resource enrichment jobs."""

from __future__ import annotations

import os
import logging
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Optional

from .database import ResourceDatabase, get_resource_database
from .enrichment import EnrichmentService
from .jobs import JobQueue


LOGGER = logging.getLogger(__name__)


class ResourceWorker:
    """Small local worker; the SQLite lease makes crashes and restarts recoverable."""

    def __init__(self, database: Optional[ResourceDatabase] = None, max_workers: int = 2):
        self.database = database or get_resource_database()
        self.queue = JobQueue(self.database)
        self.enrichment = EnrichmentService(self.database)
        self.max_workers = max(1, min(max_workers, 4))
        self.owner = f"{os.getpid()}:{uuid.uuid4().hex}"
        self.stop_event = threading.Event()

    def _process(self, job: dict) -> None:
        started = time.monotonic()
        try:
            self.enrichment.process_job(job)
        except Exception as exc:
            status = self.queue.fail(str(job["id"]), str(exc))
            self.enrichment.record_failure(job.get("resource_id"), str(exc))
            LOGGER.warning(
                "resource_job_failed",
                extra={
                    "event": "resource_job_failed",
                    "resource_id": job.get("resource_id"),
                    "job_id": job.get("id"),
                    "job_type": job.get("job_type"),
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "status": status,
                    "error_type": type(exc).__name__,
                },
            )
            return
        self.queue.complete(str(job["id"]))
        LOGGER.info(
            "resource_job_complete",
            extra={
                "event": "resource_job_complete",
                "resource_id": job.get("resource_id"),
                "job_id": job.get("id"),
                "job_type": job.get("job_type"),
                "duration_ms": int((time.monotonic() - started) * 1000),
                "status": "complete",
            },
        )

    def _run_loop(self, *, stop_when_idle: bool, max_jobs: Optional[int] = None) -> int:
        completed = 0
        futures: dict[Future, dict] = {}
        with ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="flowinone-resource",
        ) as executor:
            while not self.stop_event.is_set():
                for future, job in list(futures.items()):
                    if not future.done():
                        continue
                    try:
                        future.result()
                    except Exception:
                        pass
                    futures.pop(future, None)
                    completed += 1

                if max_jobs is not None and completed >= max_jobs and not futures:
                    break
                slots = self.max_workers - len(futures)
                if max_jobs is not None:
                    slots = min(slots, max_jobs - completed - len(futures))
                claimed = self.queue.claim(self.owner, limit=slots) if slots > 0 else []
                for job in claimed:
                    futures[executor.submit(self._process, job)] = job

                if stop_when_idle and not claimed and not futures:
                    break
                if not claimed:
                    self.stop_event.wait(0.75)
        return completed

    def run_until_idle(self, max_jobs: Optional[int] = None) -> int:
        """Process ready work and return once the queue has no immediately ready jobs."""
        return self._run_loop(stop_when_idle=True, max_jobs=max_jobs)

    def run_forever(self) -> None:
        self._run_loop(stop_when_idle=False)

    def stop(self) -> None:
        self.stop_event.set()


_WORKER_LOCK = threading.Lock()
_WORKER: Optional[ResourceWorker] = None


def start_background_resource_worker() -> Optional[ResourceWorker]:
    """Start one daemon worker in this process."""
    global _WORKER
    if os.environ.get("FLOWINONE_RESOURCE_WORKER", "1").lower() in {"0", "false", "no"}:
        return None
    with _WORKER_LOCK:
        if _WORKER is not None and not _WORKER.stop_event.is_set():
            return _WORKER
        _WORKER = ResourceWorker()
        thread = threading.Thread(
            target=_WORKER.run_forever,
            name="flowinone-resource-worker",
            daemon=True,
        )
        thread.start()
        return _WORKER


__all__ = ["ResourceWorker", "start_background_resource_worker"]
