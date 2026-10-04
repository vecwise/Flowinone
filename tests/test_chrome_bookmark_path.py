"""Chrome source selection must use a live store or an explicit fixed path."""

import json
from pathlib import Path
from urllib.parse import unquote_plus

import pytest
from bs4 import BeautifulSoup

from run import create_app
from src.flowinone import config
from src.flowinone.resource_library.database import get_resource_database
from src.flowinone.resource_library.service import ResourceService


@pytest.fixture(params=["Darwin", "Linux", "Windows"])
def chrome_profile(request, monkeypatch, tmp_path):
    monkeypatch.setattr(config.platform, "system", lambda: request.param)
    monkeypatch.setattr(config.os.path, "expanduser", lambda value: value.replace("~", str(tmp_path), 1))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    relative = {
        "Darwin": "Library/Application Support/Google/Chrome/Default",
        "Linux": ".config/google-chrome/Default",
        "Windows": "AppData/Local/Google/Chrome/User Data/Default",
    }[request.param]
    profile = tmp_path / relative
    profile.mkdir(parents=True)
    return profile


def test_detects_live_account_bookmarks_when_local_store_is_missing(chrome_profile):
    account = chrome_profile / "AccountBookmarks"
    account.write_text('{"roots": {}}', encoding="utf-8")
    assert config._detect_chrome_bookmark_path() == str(account)


def test_existing_local_bookmarks_keep_priority(chrome_profile):
    for name in ("Bookmarks", "AccountBookmarks"):
        (chrome_profile / name).write_text('{"roots": {}}', encoding="utf-8")
    assert config._detect_chrome_bookmark_path() == str(chrome_profile / "Bookmarks")


def test_missing_live_store_does_not_import_backup_or_encrypted_files(chrome_profile):
    for name in ("Bookmarks.bak", "Bookmarks.backup-20260708", "EncryptedAccountBookmarks2"):
        (chrome_profile / name).write_text("old or encrypted data", encoding="utf-8")
    assert config._detect_chrome_bookmark_path() == str(chrome_profile / "Bookmarks")


def test_fixed_path_precedence_and_expansion(monkeypatch, tmp_path):
    monkeypatch.delenv("FLOWINONE_CHROME_BOOKMARK_PATH", raising=False)
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    settings = {"CHROME_BOOKMARK_PATH": "Profile 2/AccountBookmarks"}
    assert config._resolve_chrome_bookmark_path(settings) == str(tmp_path / "Profile 2/AccountBookmarks")
    # Explicitly missing paths remain errors instead of selecting another profile.
    override = tmp_path / "missing/Bookmarks"
    monkeypatch.setenv("FLOWINONE_CHROME_BOOKMARK_PATH", str(override))
    assert config._resolve_chrome_bookmark_path(settings) == str(override)
    monkeypatch.setenv("FLOWINONE_CHROME_BOOKMARK_PATH", "~/Profile 3/Bookmarks")
    monkeypatch.setattr(config.os.path, "expanduser", lambda value: value.replace("~", str(tmp_path), 1))
    assert config._resolve_chrome_bookmark_path(settings) == str(tmp_path / "Profile 3/Bookmarks")


def test_account_source_imports_through_form_api_and_change_detection(chrome_profile, monkeypatch, tmp_path):
    monkeypatch.delenv("FLOWINONE_CHROME_BOOKMARK_PATH", raising=False)
    account = chrome_profile / "AccountBookmarks"
    payload = {"roots": {"bookmark_bar": {
        "type": "folder", "name": "Bookmarks bar", "children": [
            {"type": "url", "id": "1", "name": "Account article", "url": "https://example.com/account"},
        ],
    }}}
    account.write_text(json.dumps(payload), encoding="utf-8")
    selected = config._resolve_chrome_bookmark_path({})
    app = create_app({
        "TESTING": True,
        "FLOWINONE_RESOURCE_DB_PATH": str(tmp_path / "resources.db"),
        "FLOWINONE_RESOURCE_LINK_THUMBNAILS": False,
        "CHROME_BOOKMARK_PATH": selected,
    })
    client = app.test_client()
    page = BeautifulSoup(client.get("/resources/").data, "html.parser")
    token = page.select_one('form[action="/resources/import/chrome"] input[name="csrf_token"]')["value"]
    response = client.post("/resources/import/chrome", data={"csrf_token": token, "enqueue": "0"})
    assert "同步完成：新增 1" in unquote_plus(response.headers["Location"])
    repeat = client.post("/resources/import/chrome", data={"csrf_token": token, "enqueue": "0"})
    assert "Chrome 書籤沒有變更" in unquote_plus(repeat.headers["Location"])
    payload["roots"]["bookmark_bar"]["children"].append(
        {"type": "url", "id": "2", "name": "New account article", "url": "https://example.com/new-account"}
    )
    account.write_text(json.dumps(payload), encoding="utf-8")
    updated = client.post("/resources/import/chrome", data={"csrf_token": token, "enqueue": "0"})
    assert "同步完成：新增 1、既有 1、失敗 0" in unquote_plus(updated.headers["Location"])
    api = client.post("/api/imports/chrome")
    assert api.status_code == 200
    assert api.get_json()["duplicates"] == 2
    service = ResourceService(get_resource_database(Path(app.config["FLOWINONE_RESOURCE_DB_PATH"])))
    assert service.repository.get_by_canonical_url("https://example.com/account")["title"] == "Account article"
