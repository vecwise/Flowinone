"""Dedicated process runtime for Flowinone background workers."""

from __future__ import annotations

import logging
import signal
import threading
from collections.abc import Callable

from src.file_handler.thumbnails.worker import ThumbnailWorker

from .resource_library.worker import ResourceWorker


LOGGER = logging.getLogger(__name__)


class WorkerRuntime:
    """Own long-running workers outside the Flask/Werkzeug process lifecycle."""

    def __init__(
        self,
        *,
        thumbnail_worker: ThumbnailWorker | None = None,
        resource_worker: ResourceWorker | None = None,
    ) -> None:
        self.thumbnail_worker = thumbnail_worker or ThumbnailWorker()
        self.resource_worker = resource_worker or ResourceWorker()
        self.stop_event = threading.Event()
        self._threads: list[threading.Thread] = []

    def stop(self) -> None:
        self.stop_event.set()
        self.thumbnail_worker.stop()
        self.resource_worker.stop()

    @staticmethod
    def _run(name: str, target: Callable[[], None]) -> None:
        try:
            target()
        except Exception:
            LOGGER.exception("%s stopped unexpectedly", name)

    def run_forever(self) -> None:
        self._threads = [
            threading.Thread(
                target=self._run,
                args=("thumbnail worker", self.thumbnail_worker.run_forever),
                name="flowinone-thumbnail-runtime",
            ),
            threading.Thread(
                target=self._run,
                args=("resource worker", self.resource_worker.run_forever),
                name="flowinone-resource-runtime",
            ),
        ]
        for thread in self._threads:
            thread.start()

        try:
            while not self.stop_event.wait(0.5):
                # Both workers belong to one runtime. If either exits (including
                # a second process losing the thumbnail runtime lease), stop the
                # other one instead of leaving a partial/duplicate worker set.
                if not all(thread.is_alive() for thread in self._threads):
                    LOGGER.warning("worker runtime stopping because one worker exited")
                    break
        finally:
            self.stop()
            for thread in self._threads:
                thread.join(timeout=5)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    runtime = WorkerRuntime()

    def stop_runtime(_signum, _frame) -> None:
        runtime.stop()

    signal.signal(signal.SIGINT, stop_runtime)
    signal.signal(signal.SIGTERM, stop_runtime)
    runtime.run_forever()


if __name__ == "__main__":
    main()
