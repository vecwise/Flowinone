"""One-click macOS lifecycle control, using launchd instead of saved PIDs."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import os
from pathlib import Path
import plistlib
import re
import socket
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
URL = "http://127.0.0.1:5894"


class Host:
    def __init__(self, root: Path = ROOT):
        self.root = root.resolve()
        self.runtime = self.root / ".flowinone_local" / "host"
        self.runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
        digest = hashlib.sha256(os.fsencode(self.root)).hexdigest()[:16]
        self.label = f"local.flowinone.{digest}"
        self.domain = f"gui/{os.getuid()}"

    def target(self, component: str) -> str:
        return f"{self.domain}/{self.label}.{component}"

    @staticmethod
    def launchctl(*arguments: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["/bin/launchctl", *arguments], capture_output=True, text=True,
            timeout=35,
        )

    def state(self, component: str) -> str | None:
        result = self.launchctl("print", self.target(component))
        if result.returncode:
            # Do not silently treat a missing GUI session as a stopped service.
            domain = self.launchctl("print", self.domain)
            if domain.returncode:
                raise RuntimeError("無法連線到 macOS 登入工作階段，請在本機桌面執行。")
            return None
        return result.stdout

    @staticmethod
    def running(state: str | None) -> bool:
        return bool(state and re.search(r"^\s*pid = \d+\s*$", state, re.MULTILINE))

    def unload(self, component: str) -> None:
        if self.state(component) is None:
            return
        result = self.launchctl("bootout", "--wait", self.target(component))
        if result.returncode and self.state(component) is not None:
            raise RuntimeError(f"無法停止 {component}：{result.stderr.strip()}")
        deadline = time.monotonic() + 30
        while self.state(component) is not None:
            if time.monotonic() >= deadline:
                raise RuntimeError(f"{component} 仍在停止中，請稍後再按一次停止。")
            time.sleep(0.2)

    def load(self, component: str) -> None:
        arguments = (
            [str(self.root / "run.py")]
            if component == "web" else ["-m", "src.flowinone.workers"]
        )
        # Preserve application settings and Conda's executable search path.
        # Temporary plists are private and removed after launchd reads them.
        extra_keys = {
            "PATH", "HOME", "TMPDIR", "HTTP_TIMEOUT_SECONDS", "MAX_DOWNLOAD_BYTES",
            "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
            "http_proxy", "https_proxy", "all_proxy", "no_proxy",
        }
        environment = {
            key: value for key, value in os.environ.items()
            if key.startswith(("FLOWINONE_", "LLM_", "CONDA_")) or key in extra_keys
        }
        environment.update(FLOWINONE_DEBUG="0", FLOWINONE_HEADLESS="1")
        log = str(self.runtime / f"{component}.log")
        payload = {
            "Label": f"{self.label}.{component}",
            "ProgramArguments": [sys.executable, "-u", *arguments],
            "WorkingDirectory": str(self.root),
            "EnvironmentVariables": environment,
            "RunAtLoad": True,
            "ExitTimeOut": 20,
            "StandardOutPath": log,
            "StandardErrorPath": log,
        }
        # No LaunchAgents installation: this only runs for the current login.
        with tempfile.NamedTemporaryFile(dir=self.runtime, suffix=".plist") as file:
            plistlib.dump(payload, file)
            file.flush()
            result = self.launchctl("bootstrap", self.domain, file.name)
        if result.returncode:
            raise RuntimeError(f"無法啟動 {component}：{result.stderr.strip()}")

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

    def start(self) -> None:
        web_running = self.running(self.state("web"))
        if not web_running and self.port_busy():
            raise RuntimeError(
                "5894 埠已被其他程序使用；若先前手動啟動過 Flowinone，"
                "請先回原終端機按 Ctrl+C，再雙擊啟動。"
            )
        started = []
        try:
            for component in ("web", "worker"):
                state = self.state(component)
                if self.running(state):
                    continue
                if state is not None:
                    self.unload(component)
                # Include failed/partially completed bootstrap in rollback.
                started.append(component)
                self.load(component)
            deadline = time.monotonic() + 30
            healthy_since = None
            while time.monotonic() < deadline:
                states = [self.state(name) for name in ("web", "worker")]
                if any(state and "last exit code =" in state and not self.running(state)
                       for state in states):
                    raise RuntimeError("Web 或 Worker 啟動後退出，請查看下方記錄。")
                if all(self.running(state) for state in states) and self.web_ready():
                    healthy_since = healthy_since or time.monotonic()
                    if time.monotonic() - healthy_since >= 2:
                        print(f"Flowinone 已啟動（Web + Worker）：{URL}")
                        return
                else:
                    healthy_since = None
                time.sleep(0.25)
            raise RuntimeError("等候啟動逾時，請查看下方記錄。")
        except BaseException:
            for component in reversed(started):
                try:
                    self.unload(component)
                except Exception as error:
                    print(f"清理未完成：{error}", file=sys.stderr)
            raise

    def stop(self) -> None:
        errors = []
        for component in ("web", "worker"):
            try:
                self.unload(component)
            except Exception as error:
                errors.append(str(error))
        if errors:
            raise RuntimeError("\n".join(errors))
        print("Flowinone 已停止（Web + Worker）。")

    def status(self) -> None:
        for component in ("web", "worker"):
            status = "執行中" if self.running(self.state(component)) else "已停止"
            print(f"{component}: {status}")


def preflight() -> None:
    # Validate before starting a worker which could otherwise upgrade a DB.
    sys.path.insert(0, str(ROOT))
    from src.flowinone import config
    from src.flowinone.resource_library.database import ensure_database_current
    from src.flowinone.resource_library.settings import ResourceSettings

    # Let the foreground launcher show the existing folder picker when needed;
    # launchd children must never wait for an invisible configuration dialog.
    config.ensure_db_routes(interactive=True)
    ensure_database_current(ResourceSettings.from_environment().database_path)
    os.environ["FLOWINONE_DB_ROUTE_EXTERNAL"] = config.DB_route_external
    os.environ["FLOWINONE_DB_ROUTE_INTERNAL"] = config.DB_route_internal


def main() -> int:
    parser = argparse.ArgumentParser(description="Flowinone macOS 一鍵啟停")
    parser.add_argument("action", choices=("start", "stop", "status"))
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if sys.platform != "darwin":
        print("此快捷入口適用 macOS；其他平台請使用 README 的手動啟動指令。")
        return 1
    import fcntl

    os.chdir(ROOT)
    host = Host()
    try:
        with (host.runtime / "control.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("另一個啟動／停止操作正在執行，請等它完成。") from None
            if args.action == "start":
                preflight()
                host.start()
                if not args.no_browser:
                    subprocess.run(["/usr/bin/open", URL], check=False)
                print("可以關閉這個終端機視窗。要結束服務，雙擊「停止 Flowinone.command」。")
            elif args.action == "stop":
                host.stop()
            else:
                host.status()
    except (Exception, KeyboardInterrupt) as error:
        print(f"操作未完成：{error}\n記錄位置：{host.runtime}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
