"""Presentation-only UI variants; Catalog queries and APIs remain independent."""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from flask import current_app, render_template, request, url_for
from werkzeug.exceptions import HTTPException


UI_VARIANTS = (
    {"key": "baseline", "label": "目前基準", "number": "00", "tagline": "熟悉的收藏入口",
     "description": "沿用熟悉的配色，以緊密縮圖、精簡標題與收合資訊快速瀏覽。",
     "layouts": ("緊密縮圖網格與精簡搜尋列", "內容與接著看雙欄", "來源卡片與維護分區")},
    {"key": "curator", "label": "視覺策展", "number": "01", "tagline": "讓影像帶路",
     "description": "暖白畫廊配色，縮圖優先、縮短留白，適合比較影像與尋找靈感。",
     "layouts": ("影像優先的緊密網格", "寬幅主體、橫向接著看", "來源展卡與功能分區")},
    {"key": "workbench", "label": "素材工作台", "number": "02", "tagline": "看得多，做得快",
     "description": "冷灰工具介面、側欄篩選與緊湊資訊，適合整理與處理收藏。",
     "layouts": ("側欄篩選與密集網格", "主體與資訊工具並排", "側邊功能列與密集狀態列")},
)
UI_KEYS = frozenset(item["key"] for item in UI_VARIANTS)
# Only HTML pages and their legacy redirects participate. Never decorate assets or APIs.
UI_ENDPOINTS = frozenset({
    "local.index", "local.content_library", "local.route_guide", "local.ui_lab",
    "local.view_source_folders", "local.view_both", "local.view_grid", "local.view_slide",
    "local.view_item_db", "media.view_image", "media.view_video",
    "chrome.view_chrome_root", "chrome.view_chrome_folder", "chrome.view_chrome_youtube",
    "catalog.navigator_page", "catalog.navigator_saved_view", "catalog.duplicate_review_page",
    "resource_library.resource_index", "resource_library.resource_detail", "resource_library.resource_versions",
    "eagle.list_all_eagle_folder", "eagle.view_eagle_folder", "eagle.list_eagle_tags",
    "eagle.view_images_by_tag", "eagle.list_eagle_smart_folders", "eagle.view_eagle_smart_folder",
    "eagle.eagle_stream", "eagle.search_eagle", "eagle.view_eagle_image", "eagle.view_eagle_video",
})


def current_ui():
    value = request.args.get("ui", request.form.get("ui", "baseline"))
    return value if value in UI_KEYS else "baseline"


def is_ui_url(value):
    """Accept only a same-origin, registered HTML page URL."""
    if not value or str(value).startswith(("#", "//")) or "\\" in str(value):
        return False
    try:
        target = urlsplit(str(value))
    except ValueError:
        return False
    if target.scheme and (target.scheme != request.scheme or not target.netloc):
        return False
    if target.netloc and target.netloc != request.host:
        return False
    try:
        endpoint, _ = current_app.url_map.bind_to_environ(request.environ).match(target.path, method="GET")
    except HTTPException:
        return False
    return endpoint in UI_ENDPOINTS


def ui_href(value, variant=None):
    """Keep all query pairs, nested return paths and anchors when selecting a UI."""
    if not is_ui_url(value):
        return value
    if variant is None and "ui" not in request.values:
        return value  # Baseline URLs retain their existing shape.
    selected = variant if variant in UI_KEYS else current_ui()
    target = urlsplit(str(value))
    pairs = [(key, item) for key, item in parse_qsl(target.query, keep_blank_values=True) if key != "ui"]
    pairs = [(key, ui_href(item, selected) if key == "return_to" else item) for key, item in pairs]
    pairs.append(("ui", selected))
    return urlunsplit(target._replace(query=urlencode(pairs)))


def register_ui_variants(app):
    app.jinja_env.filters["ui_href"] = ui_href

    @app.context_processor
    def ui_context():
        def presentation_url_for(endpoint, **values):
            return ui_href(url_for(endpoint, **values))

        return {"ui_key": current_ui(), "ui_variants": UI_VARIANTS,
                "url_for": presentation_url_for}

    @app.after_request
    def preserve_ui_redirect(response):
        if 300 <= response.status_code < 400 and "Location" in response.headers and "ui" in request.values and not request.path.startswith("/api/"):
            response.headers["Location"] = ui_href(response.headers["Location"])
        return response


def render_ui_lab(database):
    from .route_guide import build_route_guide

    groups = build_route_guide(database)
    return_to = request.args.get("return_to", "")
    if not is_ui_url(return_to) or urlsplit(return_to).path == "/ui-lab/":
        return_to = url_for("catalog.navigator_page", scope="all")
    scenarios = []
    for family, label in (("browse", "瀏覽"), ("detail", "詳情"), ("manage", "管理")):
        rows = next(group["rows"] for group in groups if group["id"] == family)
        # Prefer a readable Resource detail, then an available media example.
        ready = [row for row in rows if row["status"] == "可測試" and row["links"]]
        if family == "detail":
            ready.sort(key=lambda row: row["endpoint"] != "resource_library.resource_detail")
        target = ready[0]["links"][0]["url"] if ready else url_for("resource_library.resource_index")
        scenarios.append({"family": family, "label": label, "template": f"{family}.html",
                          "url": target, "ready": bool(ready)})
    return render_template("manage.html", management="lab", title="UI Lab · Flowinone",
                           lab_scenarios=scenarios, lab_return_url=return_to)
