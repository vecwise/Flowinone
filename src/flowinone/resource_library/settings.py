"""Environment-backed settings for the local-first Resource Library."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _path_from_env(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser().resolve() if raw else default.resolve()


@dataclass(frozen=True)
class ResourceSettings:
    """All paths and optional providers used by the Resource Library."""

    data_dir: Path
    database_path: Path
    content_dir: Path
    export_dir: Path
    obsidian_vault_path: Optional[Path]
    obsidian_target_folder: str
    llm_base_url: Optional[str]
    llm_api_key: Optional[str]
    llm_model: Optional[str]
    user_context: str
    http_timeout_seconds: int
    max_download_bytes: int

    @classmethod
    def from_environment(cls) -> "ResourceSettings":
        data_dir = _path_from_env("FLOWINONE_DATA_DIR", PROJECT_ROOT / "data")
        vault_raw = os.environ.get("OBSIDIAN_VAULT_PATH", "").strip()
        timeout_raw = os.environ.get("HTTP_TIMEOUT_SECONDS", "30")
        bytes_raw = os.environ.get("MAX_DOWNLOAD_BYTES", "20000000")
        try:
            timeout = max(5, min(int(timeout_raw), 180))
        except ValueError:
            timeout = 30
        try:
            max_bytes = max(1_000_000, min(int(bytes_raw), 100_000_000))
        except ValueError:
            max_bytes = 20_000_000

        return cls(
            data_dir=data_dir,
            database_path=_path_from_env(
                "FLOWINONE_RESOURCE_DB_PATH",
                data_dir / "flowinone.sqlite3",
            ),
            content_dir=_path_from_env(
                "FLOWINONE_RESOURCE_CONTENT_DIR",
                data_dir / "content",
            ),
            export_dir=_path_from_env(
                "FLOWINONE_RESOURCE_EXPORT_DIR",
                data_dir / "exports" / "resources",
            ),
            obsidian_vault_path=Path(vault_raw).expanduser().resolve() if vault_raw else None,
            obsidian_target_folder=(
                os.environ.get("OBSIDIAN_TARGET_FOLDER", "Resources/Digested").strip()
                or "Resources/Digested"
            ),
            llm_base_url=os.environ.get("LLM_BASE_URL", "").strip() or None,
            llm_api_key=os.environ.get("LLM_API_KEY", "").strip() or None,
            llm_model=os.environ.get("LLM_MODEL", "").strip() or None,
            user_context=(
                os.environ.get(
                    "FLOWINONE_USER_CONTEXT",
                    "Flowinone、AI agent、個人知識庫、軟體專案與創作靈感",
                ).strip()
            ),
            http_timeout_seconds=timeout,
            max_download_bytes=max_bytes,
        )

    def ensure_directories(self) -> None:
        """Create only Flowinone-owned directories; never create an Obsidian vault."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.content_dir.mkdir(parents=True, exist_ok=True)
        self.export_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_resource_settings() -> ResourceSettings:
    """Return process-wide settings derived from the current environment."""
    return ResourceSettings.from_environment()


__all__ = ["PROJECT_ROOT", "ResourceSettings", "get_resource_settings"]
