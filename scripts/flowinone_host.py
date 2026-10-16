"""Run Web and Worker for exactly the lifetime of their foreground terminal."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import http.client
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time


ROOT = Path(__file__).resolve().parents[1]
URL = "http://127.0.0.1:5894"


class Host:
    def __init__(self, root: Path = ROOT):
        self.root = root.resolve()
        self.runtime = self.root / ".flowinone_local" / "host"
        self.runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.processes: dict[str, subprocess.Popen] = {}
        self.stop_event = threading.Event()
        self.stop_signal: int | None = None

    @contextmanager
    def handle_signals(self):
        def request_stop(signum, _frame):
            self.stop_signal = signum
            self.stop_event.set()

        previous = {
            signum: signal.signal(signum, request_stop)
            for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
        }
        try:
            yield
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)

    def command(self, component: str) -> list[str]:
        arguments = (
            [str(self.root / "run.py")]
            if component == "web" else ["-m", "src.flowinone.workers"]
        )
        return [sys.executable, "-u", *arguments]

    def load(self, component: str) -> None:
        environment = dict(os.environ, FLOWINONE_DEBUG="0", FLOWINONE_HEADLESS="1")
        with (self.runtime / f"{component}.log").open("ab", buffering=0) as log:
            self.processes[component] = subprocess.Popen(
                self.command(component),
                cwd=self.root,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                # Keep Terminal's session and privacy attribution. Separate
                # process groups let us also stop each service's subprocesses.
                process_group=0,
            )

    @staticmethod
    def port_busy() -> bool:
        with socket.socket() as connection:
            connection.settimeout(0.5)
            return connection.connect_ex(("127.0.0.1", 5894)) == 0

    @staticmethod
    def web_ready() -> bool:
        connection = http.client.HTTPConnection("127.0.0.1", 5894, timeout=1)
        try:
            connection.request("GET", "/static/css/main_styles.css")
            return connection.getresponse().status == 200
        except (OSError, http.client.HTTPException):
            return False
        finally:
            connection.close()

    def check_processes(self) -> None:
        for name, process in self.processes.items():
            code = process.poll()
            if code is not None:
                raise RuntimeError(f"{name} 已退出（結束碼 {code}），請查看記錄。")

    def run(self, *, open_browser: bool = True) -> None:
        if self.port_busy():
            raise RuntimeError(
                "5894 埠已被使用；若 Flowinone 已在另一個終端機執行，"
                "請回到該視窗使用，或先關閉該視窗再啟動。"
            )
        with self.handle_signals():
            try:
                for component in ("web", "worker"):
                    if self.stop_event.is_set():
                        return
                    self.load(component)
                deadline = time.monotonic() + 30
                healthy_since = None
                while not self.stop_event.is_set():
                    self.check_processes()
                    if self.web_ready():
                        healthy_since = healthy_since or time.monotonic()
                        if time.monotonic() - healthy_since >= 2:
                            break
                    else:
                        healthy_since = None
                    if time.monotonic() >= deadline:
                        raise RuntimeError("等候啟動逾時，請查看記錄。")
                    self.stop_event.wait(0.25)
                if self.stop_event.is_set():
                    return
                print(f"Flowinone 已啟動（Web + Worker）：{URL}", flush=True)
                print("請保持這個終端機視窗開啟；關閉視窗或按 Ctrl+C 會停止所有服務。", flush=True)
                print(f"記錄位置：{self.runtime}", flush=True)
                if open_browser:
                    subprocess.run(["/usr/bin/open", URL], check=False)
                while not self.stop_event.wait(0.25):
                    self.check_processes()
            finally:
                self.stop()
                if self.stop_signal != signal.SIGHUP:
                    print("Flowinone 已停止（Web + Worker）。", flush=True)

    @staticmethod
    def signal_process_group(process: subprocess.Popen, signum: int) -> None:
        try:
            os.killpg(process.pid, signum)
        except ProcessLookupError:
            pass

    def stop(self) -> None:
        # Only groups created by this invocation are touched; no saved PIDs or
        # process-name matching. Terminate both before waiting on either one.
        processes = list(self.processes.values())
        for process in processes:
            self.signal_process_group(process, signal.SIGTERM)
        deadline = time.monotonic() + 5
        for process in processes:
            try:
                process.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                pass
        for process in processes:
            # Also reap descendants if their service leader already exited.
            self.signal_process_group(process, signal.SIGKILL)
        for process in processes:
            process.wait(timeout=5)
        self.processes.clear()


def preflight() -> None:
    # Validate before starting a worker which could otherwise upgrade a DB.
    sys.path.insert(0, str(ROOT))
    from src.flowinone import config
    from src.flowinone.resource_library.database import ensure_database_current
    from src.flowinone.resource_library.settings import ResourceSettings

    config.ensure_db_routes(interactive=True)
    ensure_database_current(ResourceSettings.from_environment().database_path)
    os.environ["FLOWINONE_DB_ROUTE_EXTERNAL"] = config.DB_route_external
    os.environ["FLOWINONE_DB_ROUTE_INTERNAL"] = config.DB_route_internal


def report_chrome_bookmarks() -> None:
    from src.file_handler.chrome_bookmarks import iter_chrome_bookmark_records
    from src.file_handler.models import BookmarkError

    try:
        count = sum(1 for _ in iter_chrome_bookmark_records())
    except BookmarkError as error:
        print(f"Chrome 書籤暫時無法讀取：{error}；其他來源仍可使用。", flush=True)
    else:
        print(f"Chrome 書籤讀取成功（{count:,} 筆）。", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Flowinone 終端機啟動入口")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if sys.platform != "darwin":
        print("此快捷入口適用 macOS；其他平台請使用 README 的手動啟動指令。")
        return 1
    if not sys.stdin.isatty():
        print("請在終端機執行，或雙擊「啟動 Flowinone.command」。", file=sys.stderr)
        return 1
    import fcntl

    os.chdir(ROOT)
    host = Host()
    try:
        # Hold the lock until the terminal's entire service set has stopped.
        with (host.runtime / "control.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("Flowinone 已在另一個終端機執行，請回到該視窗使用。") from None
            preflight()
            report_chrome_bookmarks()
            host.run(open_browser=not args.no_browser)
    except (Exception, KeyboardInterrupt) as error:
        print(f"操作未完成：{error}\n記錄位置：{host.runtime}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
