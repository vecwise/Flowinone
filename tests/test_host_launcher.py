"""Lifecycle regressions: no unrelated process killing or partial starts."""

from __future__ import annotations

import sys
import os

import pytest

from scripts.flowinone_host import Host, preflight


pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS launcher")


@pytest.fixture
def host(tmp_path, monkeypatch):
    instance = Host(tmp_path)
    monkeypatch.setattr(instance, "port_busy", lambda: False)
    return instance


def test_occupied_port_leaves_other_services_alone(host, monkeypatch):
    monkeypatch.setattr(host, "state", lambda _: None)
    monkeypatch.setattr(host, "port_busy", lambda: True)
    monkeypatch.setattr(host, "load", lambda _: pytest.fail("must not start"))
    monkeypatch.setattr(host, "unload", lambda _: pytest.fail("must not stop"))
    with pytest.raises(RuntimeError, match="5894"):
        host.start()


@pytest.mark.parametrize("existing_web", [False, True])
def test_failed_worker_start_rolls_back_only_new_services(host, monkeypatch, existing_web):
    states = {"web": "pid = 123\n" if existing_web else None, "worker": None}
    stopped = []
    monkeypatch.setattr(host, "state", states.get)

    def load(name):
        states[name] = "pid = 456\n"
        if name == "worker":
            raise RuntimeError("worker bootstrap failed")

    monkeypatch.setattr(host, "load", load)
    monkeypatch.setattr(host, "unload", stopped.append)
    with pytest.raises(RuntimeError, match="worker bootstrap failed"):
        host.start()
    assert stopped == (["worker"] if existing_web else ["worker", "web"])


def test_repeated_start_reuses_both_services(host, monkeypatch):
    monkeypatch.setattr(host, "state", lambda _: "pid = 123\n")
    monkeypatch.setattr(host, "load", lambda _: pytest.fail("must reuse running service"))
    monkeypatch.setattr(host, "unload", lambda _: pytest.fail("must preserve running service"))
    monkeypatch.setattr(host, "web_ready", lambda: True)
    host.start()


def test_start_detects_worker_exit_and_cleans_up(host, monkeypatch):
    states = {"web": None, "worker": None}
    stopped = []
    monkeypatch.setattr(host, "state", states.get)

    def load(name):
        states[name] = "pid = 123\n" if name == "web" else "last exit code = 1\n"

    monkeypatch.setattr(host, "load", load)
    monkeypatch.setattr(host, "unload", stopped.append)
    with pytest.raises(RuntimeError, match="啟動後退出"):
        host.start()
    assert stopped == ["worker", "web"]


def test_stop_attempts_worker_even_when_web_stop_fails(host, monkeypatch):
    stopped = []

    def unload(name):
        stopped.append(name)
        if name == "web":
            raise RuntimeError("web stop failed")

    monkeypatch.setattr(host, "unload", unload)
    with pytest.raises(RuntimeError, match="web stop failed"):
        host.stop()
    assert stopped == ["web", "worker"]


def test_preflight_passes_selected_roots_to_background_services(tmp_path, monkeypatch):
    import config
    from src.flowinone.resource_library import database

    monkeypatch.setenv("FLOWINONE_DB_ROUTE_EXTERNAL", "/missing/external")
    monkeypatch.setenv("FLOWINONE_DB_ROUTE_INTERNAL", "/missing/internal")

    def select_folder(*, interactive):
        assert interactive is True
        monkeypatch.setattr(config, "DB_route_external", str(tmp_path))
        monkeypatch.setattr(config, "DB_route_internal", str(tmp_path))

    monkeypatch.setattr(config, "ensure_db_routes", select_folder)
    monkeypatch.setattr(database, "ensure_database_current", lambda _: None)
    preflight()
    assert os.environ["FLOWINONE_DB_ROUTE_EXTERNAL"] == str(tmp_path)
    assert os.environ["FLOWINONE_DB_ROUTE_INTERNAL"] == str(tmp_path)
