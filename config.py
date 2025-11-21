import json
import os
import platform
from json import JSONDecodeError
from pathlib import Path
from typing import Dict, Optional

CONFIG_JSON_PATH = Path(__file__).with_name("config.json")
DEFAULT_CONFIG: Dict[str, str] = {
    "DB_route_external": "",
    "DB_route_internal": "",
}


def _detect_chrome_bookmark_path() -> str:
    system = platform.system()
    if system == "Windows":
        local_app_data = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
        return os.path.join(local_app_data, "Google", "Chrome", "User Data", "Default", "Bookmarks")
    if system == "Linux":
        return os.path.expanduser("~/.config/google-chrome/Default/Bookmarks")
    # macOS default
    return os.path.expanduser("~/Library/Application Support/Google/Chrome/Default/Bookmarks")


def _load_config() -> Dict[str, str]:
    """Load config.json or create it with defaults."""
    if not CONFIG_JSON_PATH.exists():
        CONFIG_JSON_PATH.write_text(
            json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return DEFAULT_CONFIG.copy()

    try:
        data = json.loads(CONFIG_JSON_PATH.read_text(encoding="utf-8"))
    except JSONDecodeError as exc:  # pragma: no cover - malformed file should be fixed by user
        raise RuntimeError("config.json 解析失敗，請修正或刪除後重試。") from exc

    if not isinstance(data, dict):
        raise RuntimeError("config.json 格式錯誤，應為 JSON 物件。")

    # Ensure required keys exist.
    for key, value in DEFAULT_CONFIG.items():
        data.setdefault(key, value)
    return data


_CONFIG_DATA = _load_config()
DB_route_external = _CONFIG_DATA.get("DB_route_external", "")
DB_route_internal = _CONFIG_DATA.get("DB_route_internal", "")


def _update_config_json(external: str, internal: str) -> None:
    """Persist selected directories into config.json."""
    data = _load_config()
    data["DB_route_external"] = os.path.normpath(external)
    data["DB_route_internal"] = os.path.normpath(internal)
    CONFIG_JSON_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


CHROME_BOOKMARK_PATH = _detect_chrome_bookmark_path()

# Domains whose thumbnails should be fetched via OpenGraph (e.g. specialised sites)
SPECIAL_THUMBNAIL_DOMAINS = [
    "xvideos",
    "pornhub",
    "phncdn",
    "youporn",
    "redtube",
    "tube8",
    # "adultdeepfakes",
    "avjoy"
]


def _is_valid_directory(path: str) -> bool:
    """Return True when the path points to an existing directory."""
    return bool(path and os.path.isdir(path))


def _prompt_for_directory(title: str, initial: Optional[str] = None) -> str:
    """Open a GUI dialog for directory selection."""
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox
    except ImportError as exc:  # pragma: no cover - tkinter usually available
        raise RuntimeError("找不到 tkinter，請手動更新 config.json 裡的路徑。") from exc

    root = tk.Tk()
    root.withdraw()

    # Using withdraw keeps the GUI unobtrusive; showinfo gives basic instructions.
    messagebox.showinfo(
        "Flowinone 設定",
        f"請選擇「{title}」資料夾。\n\n選擇完成後會寫回 config.json。",
        parent=root,
    )

    directory = filedialog.askdirectory(
        title=title,
        initialdir=initial or os.path.expanduser("~"),
        mustexist=True,
    )
    root.destroy()

    if not directory:
        raise RuntimeError(f"未選擇「{title}」資料夾，請重新啟動服務並完成設定。")

    return os.path.normpath(directory)


def _ensure_db_routes() -> None:
    """Make sure DB routes exist, prompting the user once if needed.

    External/internal default to the same place; users can still edit config.json manually
    if they want different locations.
    """
    global DB_route_external, DB_route_internal  # noqa: PLW0603

    external = DB_route_external.strip()
    internal = DB_route_internal.strip()

    if _is_valid_directory(external) and _is_valid_directory(internal):
        return

    if os.environ.get("FLOWINONE_HEADLESS", "").lower() in {"1", "true"}:
        raise RuntimeError(
            "DB 路徑尚未完整設定，且目前為 headless 模式。"
            "請直接修改 config.json 或設定環境變數後再啟動。"
        )

    # Prefer whichever side is already valid; otherwise prompt once and share the path.
    selected = external if _is_valid_directory(external) else internal
    if not _is_valid_directory(selected):
        selected = _prompt_for_directory("Media Library")

    DB_route_external = selected
    DB_route_internal = selected

    _update_config_json(DB_route_external, DB_route_internal)


_ensure_db_routes()
