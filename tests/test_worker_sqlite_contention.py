"""Real independent SQLite writers must not terminate the worker or repeat jobs."""

import sqlite3
import threading

import pytest
from sqlalchemy import event

from src.flowinone.catalog.locking import database_is_locked
from src.flowinone.resource_library.database import ResourceDatabase
from src.flowinone.resource_library.worker import ResourceWorker


def make_worker(tmp_path):
    database = ResourceDatabase(tmp_path / 'worker.db')
    worker = ResourceWorker(database, max_workers=1)
    job = worker.queue.queue('fetch_metadata', resource_id=None)
    # Exercise actual SQLite BUSY without spending the production 30-second wait.
    event.listen(database.engine, 'connect', lambda connection, _: connection.execute('PRAGMA busy_timeout=20'))
    database.engine.dispose()
    return worker, job


def start_worker(worker):
    results, errors = [], []

    def run():
        try:
            results.append(worker.run_until_idle(max_jobs=1))
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, results, errors


def watch_busy(monkeypatch, queue, method, blocked):
    original = getattr(queue, method)

    def call(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        except Exception as exc:
            if database_is_locked(exc):
                blocked.set()
            raise

    monkeypatch.setattr(queue, method, call)


@pytest.mark.parametrize('stop_while_locked', [False, True])
def test_claim_recovers_from_another_sqlite_writer_and_can_stop(tmp_path, monkeypatch, stop_while_locked):
    worker, job = make_worker(tmp_path)
    processed = []
    monkeypatch.setattr(worker.enrichment, 'process_job', lambda claimed: processed.append(claimed['id']))
    blocked = threading.Event()
    watch_busy(monkeypatch, worker.queue, 'claim', blocked)
    holder = sqlite3.connect(worker.database.path)
    holder.execute('BEGIN IMMEDIATE')
    thread, results, errors = start_worker(worker)
    try:
        assert blocked.wait(5), 'worker did not encounter the independent writer'
        assert thread.is_alive()
        if stop_while_locked:
            worker.stop()
            thread.join(2)
            assert not thread.is_alive(), 'backoff ignored the stop request'
        else:
            holder.rollback()
            thread.join(5)
            assert not thread.is_alive()
            assert processed == [job['id']]
            assert worker.queue.get(job['id'])['status'] == 'complete'
        assert not errors
        assert results == ([0] if stop_while_locked else [1])
    finally:
        holder.rollback()
        holder.close()
        worker.stop()
        thread.join(5)
        worker.database.dispose()


@pytest.mark.parametrize('outcome', ['complete', 'fail'])
def test_acknowledgement_retries_without_repeating_the_job(tmp_path, monkeypatch, outcome):
    worker, job = make_worker(tmp_path)
    ready, finish, blocked = threading.Event(), threading.Event(), threading.Event()
    processed = []

    def process(claimed):
        processed.append(claimed['id'])
        ready.set()
        assert finish.wait(5)
        if outcome == 'fail':
            raise ValueError('original processing failure')

    monkeypatch.setattr(worker.enrichment, 'process_job', process)
    watch_busy(monkeypatch, worker.queue, outcome, blocked)
    thread, results, errors = start_worker(worker)
    holder = sqlite3.connect(worker.database.path)
    try:
        assert ready.wait(5)
        holder.execute('BEGIN IMMEDIATE')
        finish.set()
        assert blocked.wait(5)
        assert thread.is_alive()
        holder.rollback()
        thread.join(5)
        assert not thread.is_alive()
        assert not errors
        assert results == [1]
        assert processed == [job['id']]
        recorded = worker.queue.get(job['id'])
        assert recorded['status'] == ('complete' if outcome == 'complete' else 'retry')
        assert recorded['attempts'] == (0 if outcome == 'complete' else 1)
        if outcome == 'fail':
            assert recorded['error_message'] == 'original processing failure'
    finally:
        finish.set()
        holder.rollback()
        holder.close()
        worker.stop()
        thread.join(5)
        worker.database.dispose()


def test_non_contention_database_errors_are_not_retried(tmp_path, monkeypatch):
    worker, _job = make_worker(tmp_path)

    def broken_claim(*args, **kwargs):
        raise sqlite3.OperationalError('no such table: processing_jobs')

    monkeypatch.setattr(worker.queue, 'claim', broken_claim)
    with pytest.raises(sqlite3.OperationalError, match='no such table'):
        worker.run_until_idle()
    worker.database.dispose()
