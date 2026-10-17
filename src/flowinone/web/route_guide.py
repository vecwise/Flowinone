"""Read-only examples for the HTML route directory; never run maintenance jobs."""

from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from flask import current_app, url_for
from sqlalchemy import text

from src.eagle_api.client import default_client as eagle_client
from src.flowinone import config
from src.flowinone.catalog.browse import CatalogService

from .common import get_feature_flags


def _examples(database, flags):
    examples = {}
    with database.engine.connect() as connection:
        for source in ("local", "eagle"):
            for kind in ("image", "video"):
                rows = connection.execute(text(
                    "SELECT o.source_key,o.source_path,o.detail_uri FROM catalog_origins o "
                    "JOIN catalog_items i ON i.id=o.catalog_item_id "
                    "WHERE o.source_kind=:source AND i.item_type=:kind "
                    "AND o.stale=0 AND i.is_deleted=0 ORDER BY o.last_seen_at DESC LIMIT 20"
                ), {"source": source, "kind": kind}).mappings()
                for row in rows:
                    if source == "eagle":
                        examples[f"eagle_{kind}"] = {"item_id": row["source_key"]}
                        break
                    target = urlsplit(row["detail_uri"] or "")
                    src = parse_qs(target.query).get("src", ["external"])[0]
                    root = config.DB_route_internal if src == "internal" else config.DB_route_external
                    relative = row["source_path"] or unquote(target.path).removeprefix(f"/{kind}/")
                    if not root or not relative or src not in {"external", "internal"}:
                        continue
                    base = Path(root).expanduser().resolve()
                    candidate = (base / relative).resolve()
                    if not candidate.is_relative_to(base) or not candidate.is_file():
                        continue
                    relative = candidate.relative_to(base).as_posix()
                    examples[f"local_{kind}"] = {f"{kind}_path": relative, "src": src}
                    if candidate.parent != base:
                        examples.setdefault("local_folder", {
                            "folder_path": candidate.parent.relative_to(base).as_posix(), "src": src,
                        })
                    break
        resource_id = connection.execute(text(
            "SELECT id FROM resources ORDER BY updated_at DESC LIMIT 1"
        )).scalar()
        if resource_id:
            examples["resource"] = {"resource_id": resource_id}

    if "local_folder" not in examples:
        for src, root in (("external", config.DB_route_external), ("internal", config.DB_route_internal)):
            if not root:
                continue
            try:
                folder = next((path for path in Path(root).expanduser().iterdir()
                               if not path.name.startswith(".") and path.is_dir()), None)
            except OSError:
                continue
            if folder:
                examples["local_folder"] = {"folder_path": folder.name, "src": src}
                break

    saved = CatalogService(database).list_saved_searches(limit=1)
    if saved:
        examples["saved_view"] = {"saved_search_id": saved[0]["id"]}
    if flags.get("eagle"):
        # Request only metadata, not the source gallery (which loads every folder's thumbnail).
        response = eagle_client.library_info()
        folders = (response.get("data") or {}).get("folders", []) if response.get("status") == "success" else []
        if folders:
            examples["eagle_folder"] = {"eagle_folder_id": folders[0]["id"]}
        response = eagle_client.get_smart_folders(limit=1)
        envelope = (response.get("data") or {}) if response.get("status") == "success" else {}
        smart_folders = envelope.get("data", []) if isinstance(envelope, dict) else envelope
        if smart_folders:
            examples["eagle_smart_folder"] = {"smart_folder_id": smart_folders[0]["id"]}
    return examples


def build_route_guide(database):
    flags = get_feature_flags()
    examples = _examples(database, flags)
    rules = {rule.endpoint: rule.rule for rule in current_app.url_map.iter_rules()}

    def entry(endpoint, label, template, *, sample=None, feature=None, values=None,
              note="", fallback="local.content_library", method="GET", route=None):
        params = examples.get(sample) if sample else (values or {})
        available = not feature or flags.get(feature)
        status = "可測試" if available else "來源未就緒"
        if not available:
            note = {"eagle": "先開啟 Eagle 並載入素材庫；未連線時此路由回 404。",
                    "chrome": "先設定可讀取的 Chrome 書籤檔。",
                    "youtube": "先設定可讀取的 Chrome 書籤檔。",
                    "db": "本機索引尚未就緒，先到管理收藏同步。"}[feature]
        if params is None:
            status = "缺少樣本" if available else status
            note = note or "目前沒有可用樣本，請先到來源頁新增或選取項目。"
        link = url_for(endpoint, **params) if params is not None else None
        links = [{"label": "開啟測試", "url": link}] if link else []
        if not link:
            links.append({"label": "前往準備資料", "url": url_for(fallback)})
        if method == "POST":
            status = "需提交表單" if available else status
            links = [{"label": "前往操作頁", "url": url_for("local.content_library") + ("#local-maintenance" if available else "")}]
        return {"endpoint": endpoint, "label": label, "route": route or rules[endpoint],
                "method": method, "template": template, "status": status,
                "note": note, "links": links}

    browse = [
        entry("catalog.navigator_page", "我的收藏", "browse.html", values={"scope": "all"}),
        entry("local.view_source_folders", "本機資料夾總覽", "browse.html", feature="db"),
        entry("local.view_both", "本機資料夾內容", "browse.html", sample="local_folder"),
        entry("chrome.view_chrome_folder", "Chrome 書籤資料夾", "browse.html", feature="chrome", values={"folder_path": "bookmark_bar"}),
        entry("chrome.view_chrome_youtube", "Chrome YouTube 書籤", "browse.html", feature="youtube"),
        entry("eagle.list_all_eagle_folder", "Eagle 資料夾總覽", "browse.html", feature="eagle"),
        entry("eagle.view_eagle_folder", "Eagle 資料夾內容", "browse.html", feature="eagle", sample="eagle_folder"),
        entry("eagle.list_eagle_tags", "Eagle 標籤總覽", "browse.html", feature="eagle"),
        entry("eagle.view_images_by_tag", "Eagle 標籤內容", "browse.html", feature="eagle", values={"target_tag": "design"}, note="以 design 測試；沒有此標籤時顯示空結果。"),
        entry("eagle.list_eagle_smart_folders", "Eagle 智慧資料夾總覽", "browse.html", feature="eagle"),
        entry("eagle.view_eagle_smart_folder", "Eagle 智慧資料夾內容", "browse.html", feature="eagle", sample="eagle_smart_folder"),
        entry("eagle.eagle_stream", "Eagle 串流", "browse.html", feature="eagle"),
        entry("eagle.search_eagle", "Eagle 專屬搜尋", "browse.html", feature="eagle", values={"query": "design"}),
    ]
    browse[0]["links"] = [{"label": label, "url": url_for("catalog.navigator_page", scope="all", view=view)}
                           for label, view in (("視覺模式", "grid"), ("閱讀模式", "list"))]
    detail = [
        entry("media.view_image", "本機圖片詳情", "detail.html", sample="local_image"),
        entry("media.view_video", "本機影片詳情", "detail.html", sample="local_video"),
        entry("eagle.view_eagle_image", "Eagle 圖片詳情", "detail.html", sample="eagle_image", feature="eagle"),
        entry("eagle.view_eagle_video", "Eagle 影片詳情", "detail.html", sample="eagle_video", feature="eagle"),
        entry("resource_library.resource_detail", "Resource 摘要與全文", "detail.html", sample="resource", fallback="resource_library.resource_index"),
        entry("resource_library.resource_versions", "Resource 內容版本與差異", "detail.html", sample="resource", fallback="resource_library.resource_index"),
    ]
    manage = [
        entry("local.content_library", "來源與同步", "manage.html"),
        entry("resource_library.resource_index", "新增與匯入", "manage.html"),
        entry("local.view_item_db", "本機索引", "manage.html", feature="db"),
        entry("local.route_guide", "頁面測試導覽（本頁）", "manage.html"),
        entry("local.ui_lab", "UI Lab 風格比較", "manage.html", note="4 套 UI 共用瀏覽、詳情、管理三種模板。"),
        entry("catalog.duplicate_review_page", "重複圖片檢閱", "manage.html"),
    ]
    legacy = [
        entry("local.index", "首頁轉址", "→ browse.html", note="轉到 /navigator/?scope=all。"),
        entry("chrome.view_chrome_root", "Chrome 根入口", "→ browse.html", feature="chrome", note="轉到 Chrome 書籤列。"),
        entry("local.view_grid", "舊 Grid 入口", "→ browse.html", sample="local_folder", note="轉到 /both/，使用視覺模式。"),
        entry("local.view_slide", "舊 Slide 入口", "→ browse.html", sample="local_folder", note="轉到 /both/，使用閱讀模式。"),
        entry("catalog.navigator_saved_view", "已儲存的常用條件", "→ browse.html", sample="saved_view", fallback="catalog.navigator_page",
              note="先在我的收藏 → 更多篩選，儲存一個常用條件。" if "saved_view" not in examples else "轉到對應的收藏條件。"),
        entry("catalog.navigator_page", "舊素材範圍", "browse.html", values={"scope": "gallery"}, note="與收藏共用路由，只包含本機、Eagle 與書籤。"),
    ]
    debug_enabled = "debug.debug_print" in rules
    legacy.append({"endpoint": "debug.debug_print", "label": "Debug 測試頁", "route": "/debug/",
                   "method": "GET", "template": "test_arg.html", "status": "可測試" if debug_enabled else "未啟用",
                   "note": "僅在 FLOWINONE_DEV_TOOLS 啟用時註冊；一般設定回 404。",
                   "links": [{"label": "開啟測試" if debug_enabled else "測試 404", "url": "/debug/"}]})
    operations = [entry(endpoint, label, "manage.html", method="POST", feature="db", note=f"在本機維護點「{button}」；會實際執行維護。")
                  for endpoint, label, button in (
                      ("local.update_item_db_route", "更新索引結果", "同步本機索引"),
                      ("local.update_thumbnails_route", "補縮圖結果", "補齊縮圖"),
                      ("local.clear_thumbnails_route", "清除縮圖結果", "清除縮圖紀錄"),
                  )]
    return [
        {"id": "browse", "label": "瀏覽", "template": "browse.html", "rows": browse},
        {"id": "detail", "label": "詳情", "template": "detail.html", "rows": detail},
        {"id": "manage", "label": "管理", "template": "manage.html", "rows": manage},
        {"id": "legacy", "label": "轉址與特殊入口", "template": "依目標頁面", "rows": legacy},
        {"id": "operations", "label": "維護操作結果", "template": "manage.html", "rows": operations},
    ]
