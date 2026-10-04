"""Presentation adapters for shared page families; source loaders keep ownership of data."""

from urllib.parse import urlsplit

from flask import render_template, request, url_for

from .common import get_feature_flags

ITEM_TYPE_LABELS = {
    "image": "圖片", "video": "影片", "bookmark": "書籤", "folder": "資料夾",
    "tag": "標籤", "article": "文章", "web_page": "網頁", "pdf": "PDF",
    "github": "GitHub", "social_post": "社群貼文", "file": "檔案", "unknown": "其他",
}
SOURCE_LABELS = {"local": "本機", "eagle": "Eagle", "chrome": "Chrome 書籤"}


def browse_view() -> str:
    return "list" if request.args.get("view") in {"list", "knowledge", "reader", "single", "linear"} else "grid"


def source_cards(data: list[dict], source: str) -> list[dict]:
    """Translate direct-source records to the same card contract as Catalog results."""
    cards = []
    for index, item in enumerate(data):
        kind = item.get("media_type", "image")
        uri = item.get("url") or item.get("detail_url") or "#"
        try:
            domain = urlsplit(uri).hostname or ""
        except ValueError:
            domain = ""
        path = item.get("path_display") or " / ".join(item.get("folder_labels") or [])
        cards.append({
            "id": item.get("id") or str(index), "catalog_item": False,
            "title": item.get("name") or item.get("title") or "未命名",
            "item_type": kind, "launch_uri": uri,
            "launch_source": "bookmarks" if source == "chrome" else source,
            "launch_source_label": SOURCE_LABELS.get(source, source),
            "target_blank": kind == "bookmark", "thumbnail_ref": item.get("thumbnail_route"),
            "thumbnail_is_fallback": False, "site_domain": domain, "site_mark": domain[:2].upper(),
            "card_description": item.get("description"),
            "card_tags": (item.get("tags") or [])[:4], "collection_path": path,
            "collection_label": path.rsplit(" / ", 1)[-1] if path else "",
            "thumbnail_id": item.get("id") if kind == "bookmark" else None,
            "thumbnail_status": item.get("thumbnail_status"), "sources": [],
        })
    return cards


def render_source_browse(*, metadata: dict, data: list[dict]):
    source = request.blueprint or "local"
    pagination = metadata.get("pagination") or {}
    links = [{"label": "返回收藏", "url": url_for("catalog.navigator_page", scope="all")}]
    if get_feature_flags().get("eagle"):
        links.extend({"label": label, "url": url_for(endpoint)} for label, endpoint in (
            ("資料夾", "eagle.list_all_eagle_folder"), ("標籤", "eagle.list_eagle_tags"),
            ("智慧資料夾", "eagle.list_eagle_smart_folders"), ("串流", "eagle.eagle_stream"),
        ))
    return render_template(
        "browse.html", title=metadata.get("name", "來源瀏覽"), metadata=metadata,
        browse={"kind": "source", "heading": metadata.get("name", "來源瀏覽"),
                "description": "瀏覽來源中的資料夾與項目。", "links": links,
                "items": source_cards(data, source), "total": pagination.get("total", len(data)),
                "previous_url": pagination.get("previous_url"), "next_url": pagination.get("next_url")},
        view=browse_view(), item_type_labels=ITEM_TYPE_LABELS,
    )


def render_catalog_browse(**context):
    for item in context["payload"]["items"]:
        item["catalog_item"] = True
        item["sources"] = context["query"].sources
    return render_template(
        "browse.html", browse={"kind": "catalog", "heading": "我的收藏",
        "description": "選個主題，接著逛自己的文章、影片與圖片。", "links": [],
        "items": context["payload"]["items"], "total": context["payload"]["total_estimate"],
        "next_url": context["next_url"], "previous_url": None}, **context,
    )
