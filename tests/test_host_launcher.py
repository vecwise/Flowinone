"""Foreground lifecycle regressions, including a real controlling terminal."""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import select
import signal
import sys
import time

import pytest

if sys.platform == "darwin":
    import pty

from scripts import flowinone_host
from scripts.flowinone_host import Host, preflight, report_chrome_bookmarks


pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS launcher")


@pytest.fixture
def host(tmp_path, monkeypatch):
    instance = Host(tmp_path)
    monkeypatch.setattr(instance, "port_busy", lambda: False)
    monkeypatch.setattr(instance, "web_ready", lambda: True)
    monkeypatch.setattr(instance, "command", lambda _: [
        sys.executable, "-c", "import time; time.sleep(60)",
    ])
    return instance


def test_occupied_port_leaves_other_services_alone(host, monkeypatch):
    monkeypatch.setattr(host, "port_busy", lambda: True)
    monkeypatch.setattr(host, "load", lambda _: pytest.fail("must not start"))
    with pytest.raises(RuntimeError, match="5894"):
        host.run(open_browser=False)
    assert host.processes == {}


def test_failed_worker_start_reaps_web(host, monkeypatch):
    started = []
    load = host.load

    def load_component(name):
        if name == "worker":
            raise RuntimeError("worker spawn failed")
        load(name)
        started.append(host.processes[name])

    monkeypatch.setattr(host, "load", load_component)
    with pytest.raises(RuntimeError, match="worker spawn failed"):
        host.run(open_browser=False)
    assert started[0].poll() is not None
    assert host.processes == {}


def test_exited_worker_stops_web_even_with_zero_exit_code(host, monkeypatch):
    started = []
    load = host.load
    command = host.command
    monkeypatch.setattr(host, "command", lambda name: (
        [sys.executable, "-c", "pass"] if name == "worker" else command(name)
    ))

    def load_component(name):
        load(name)
        started.append(host.processes[name])

    monkeypatch.setattr(host, "load", load_component)
    with pytest.raises(RuntimeError, match="worker 已退出"):
        host.run(open_browser=False)
    assert all(process.poll() is not None for process in started)
    assert host.processes == {}


def _read_until(fd, expected, timeout=10):
    output = b""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        readable, _, _ = select.select([fd], [], [], 0.1)
        if readable:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            output += chunk
            if expected.encode() in output:
                return output.decode(errors="replace")
    pytest.fail(f"terminal did not show {expected!r}: {output.decode(errors='replace')}")


def _wait_exited(pid, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result, status = os.waitpid(pid, os.WNOHANG)
        if result:
            return os.waitstatus_to_exitcode(status)
        time.sleep(0.05)
    pytest.fail("supervisor survived terminal shutdown")


@contextmanager
def _terminal_supervisor(tmp_path):
    runner = tmp_path / "runner.py"
    runner.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
        "from pathlib import Path\n"
        "from scripts.flowinone_host import Host\n"
        f"host = Host(Path({str(tmp_path)!r}))\n"
        "def command(name):\n"
        f"    path = str(Path({str(tmp_path)!r}) / (name + '.pid'))\n"
        "    child = 'import os, time; from pathlib import Path; ' + "
        "f'Path({path!r}).write_text(str(os.getpid())); time.sleep(60)'\n"
        "    return [sys.executable, '-u', '-c', child]\n"
        "host.command = command\n"
        "host.port_busy = lambda: False\n"
        "host.web_ready = lambda: True\n"
        "host.run(open_browser=False)\n",
        encoding="utf-8",
    )
    pid, master = pty.fork()
    if pid == 0:
        os.execv(sys.executable, [sys.executable, str(runner)])
    child_pids = []
    reaped = False
    try:
        _read_until(master, "Flowinone 已啟動")
        child_pids = [int((tmp_path / f"{name}.pid").read_text()) for name in ("web", "worker")]
        yield pid, master, child_pids
        exit_code = _wait_exited(pid)
        reaped = True
        assert exit_code == 0
        for child in child_pids:
            with pytest.raises(ProcessLookupError):
                os.kill(child, 0)
    finally:
        try:
            os.close(master)
        except OSError:
            pass
        if not reaped:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.waitpid(pid, 0)
        for child in child_pids:
            try:
                os.killpg(child, signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.parametrize("shutdown", ["close-terminal", "ctrl-c", "terminate"])
def test_terminal_shutdown_reaps_both_services(tmp_path, shutdown):
    with _terminal_supervisor(tmp_path) as (pid, master, children):
        assert all(os.getsid(child) == pid for child in children)
        if shutdown == "close-terminal":
            os.close(master)  # Kernel sends SIGHUP when the controlling terminal closes.
        elif shutdown == "ctrl-c":
            os.write(master, b"\x03")
        else:
            os.kill(pid, signal.SIGTERM)


@pytest.mark.parametrize("stubborn_leader", [False, True])
def test_shutdown_also_kills_stubborn_service_subprocesses(host, tmp_path, monkeypatch, stubborn_leader):
    descendant_file = tmp_path / "descendant.pid"
    descendant_code = (
        "import os, signal, time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"Path({str(descendant_file)!r}).write_text(str(os.getpid())); time.sleep(60)"
    )
    service_code = (
        "import signal, subprocess, sys, time; "
        + ("signal.signal(signal.SIGTERM, signal.SIG_IGN); " if stubborn_leader else "")
        + f"subprocess.Popen([sys.executable, '-c', {descendant_code!r}]); time.sleep(60)"
    )
    monkeypatch.setattr(host, "command", lambda _: [sys.executable, "-c", service_code])
    host.load("worker")
    process = host.processes["worker"]
    try:
        deadline = time.monotonic() + 3
        while not descendant_file.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        descendant = int(descendant_file.read_text())
        host.stop()
        assert process.poll() is not None
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                os.kill(descendant, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            pytest.fail("service descendant survived shutdown")
    finally:
        Host.signal_process_group(process, signal.SIGKILL)
        process.wait(timeout=3)


def test_non_terminal_start_never_launches_services(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["flowinone_host.py", "--no-browser"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(flowinone_host, "preflight", lambda: pytest.fail("requires terminal"))
    assert flowinone_host.main() == 1
    assert "請在終端機執行" in capsys.readouterr().err


def test_preflight_passes_selected_roots_to_child_services(tmp_path, monkeypatch):
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


def test_chrome_check_reads_configured_file(tmp_path, monkeypatch, capsys):
    from src.file_handler import chrome_bookmarks

    bookmarks = tmp_path / "AccountBookmarks"
    bookmarks.write_text('{"roots":{"bookmark_bar":{"type":"folder","children":['
                         '{"type":"url","name":"Article","url":"https://example.com"}'
                         ']}}}', encoding="utf-8")
    monkeypatch.setattr(chrome_bookmarks, "CHROME_BOOKMARK_PATH", str(bookmarks))
    report_chrome_bookmarks()
    assert "Chrome 書籤讀取成功（1 筆）" in capsys.readouterr().out
