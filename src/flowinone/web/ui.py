"""Presentation adapters for shared page families; source loaders keep ownership of data."""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from flask import render_template, request, url_for

from .common import get_feature_flags
from .browse_sources import source_preference
from src.flowinone.catalog.media import media_category

ITEM_TYPE_LABELS = {
    "image": "圖片", "video": "影片", "bookmark": "書籤", "folder": "資料夾", "album": "漫畫／圖集",
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
        if kind in {"image", "video"} and uri.startswith("/") and not uri.startswith("//"):
            target = urlsplit(uri)
            pairs = [(key, value) for key, value in parse_qsl(target.query) if key != "return_to"]
            pairs.append(("return_to", request.full_path.rstrip("?")))
            uri = urlunsplit(("", "", target.path, urlencode(pairs), target.fragment))
        try:
            domain = urlsplit(uri).hostname or ""
        except ValueError:
            domain = ""
        path = item.get("path_display") or " / ".join(item.get("folder_labels") or [])
        cards.append({
            "id": item.get("id") or str(index), "catalog_item": False,
            "title": item.get("name") or item.get("title") or "未命名",
            "item_type": kind, "launch_uri": uri,
            "media_category": media_category(kind, uri),
            "launch_source": "bookmarks" if source == "chrome" else source,
            "launch_source_label": SOURCE_LABELS.get(source, source),
            "target_blank": kind == "bookmark", "thumbnail_ref": item.get("thumbnail_route"),
            "thumbnail_is_fallback": False, "site_domain": domain, "site_mark": domain[:2].upper(),
            "card_description": item.get("description"),
            "card_tags": (item.get("tags") or [])[:4], "collection_path": path,
            "collection_label": path.rsplit(" / ", 1)[-1] if path else "",
            "thumbnail_id": item.get("id") if kind == "bookmark" else None,
            "thumbnail_status": item.get("thumbnail_status"), "sources": [],
            "thumbnail_provider": item.get("thumbnail_provider"),
        })
    return cards


def render_source_browse(*, metadata: dict, data: list[dict]):
    source = request.blueprint or "local"
    cards = source_cards(data, source)
    preferred = source_preference()
    excluded = bool(preferred and ("bookmarks" if source == "chrome" else source) not in preferred)
    if excluded:
        cards = []
    categories = {item["media_category"] for item in cards}
    media_scope = next(iter(categories)) if len(categories) == 1 and not any(item["item_type"] in {"folder", "tag"} for item in cards) else ""
    pagination = metadata.get("pagination") or {}
    links = [{"label": "返回收藏", "url": url_for("catalog.navigator_page", scope="all")}]
    if source == "eagle" and get_feature_flags().get("eagle"):
        links.extend({"label": label, "url": url_for(endpoint)} for label, endpoint in (
            ("資料夾", "eagle.list_all_eagle_folder"), ("標籤", "eagle.list_eagle_tags"),
            ("智慧資料夾", "eagle.list_eagle_smart_folders"), ("串流", "eagle.eagle_stream"),
        ))
    return render_template(
        "browse.html", title=metadata.get("name", "來源瀏覽"), metadata=metadata,
        browse={"kind": "source", "heading": metadata.get("name", "來源瀏覽"),
                "description": "瀏覽來源中的資料夾與項目。", "links": links,
                "items": cards, "total": 0 if excluded else pagination.get("total", len(data)),
                "previous_url": None if excluded else pagination.get("previous_url"), "next_url": None if excluded else pagination.get("next_url"),
                "error": metadata.get("error"), "batch_only": metadata.get("category") == "stream"},
        view=browse_view(), item_type_labels=ITEM_TYPE_LABELS, media_scope=media_scope,
    ), metadata.get("status_code", 200)


def render_source_error(message):
    return render_source_browse(metadata={"name": "來源暫時無法使用", "error": message, "status_code": 500}, data=[])


def render_catalog_browse(**context):
    for item in context["payload"]["items"]:
        item["catalog_item"] = True
        item["sources"] = context["query"].sources
    return render_template(
        "browse.html", browse={"kind": "catalog", "heading": "我的收藏",
        "description": "選個主題，接著逛自己的文章、影片與圖片。", "links": [],
        "items": context["payload"]["items"], "total": context["payload"]["total_estimate"],
        "next_url": context["next_url"], "previous_url": None},
        media_scope=context["query"].media or (context["query"].item_type if context["query"].item_type in {"image", "video"} else ""), **context,
    )


def render_media_detail(**context):
    media, metadata = context["media"], context["metadata"]
    origin = context.pop("catalog_origin", None)
    targets = detail_browse_targets(*origin) if origin else []
    context.setdefault("title", f"{metadata.get('name') or media.get('name') or '項目詳情'} · Flowinone")
    return render_template("detail.html", detail={
        "kind": context["media_kind"], "title": metadata.get("name") or media.get("name"),
        "source_label": ITEM_TYPE_LABELS[context["media_kind"]],
        "description": metadata.get("description"), "tags": metadata.get("tags") or [],
        "parent_url": safe_browse_return(request.args.get("return_to")) or safe_browse_return(media.get("parent_url")) or url_for("local.index"),
        "thumbnail_url": media.get("thumbnail_route"), "source_url": media.get("source_url"),
        "mime_type": media.get("mime_type"), "download_url": media.get("download_url"),
        "original_url": media.get("original_url"),
        "browse_targets": targets,
    }, notice=request.args.get("notice"), **context)


def detail_browse_targets(source, key):
    from .database import request_database
    from .browse_sources import current_sources
    from src.flowinone.catalog.targets import BrowseTargetService

    return BrowseTargetService(request_database()).for_origin(source, key, sources=current_sources())


def safe_browse_return(value):
    if not value or len(value) > 4000 or "\\" in value:
        return None
    try:
        target = urlsplit(value)
    except ValueError:
        return None
    if target.scheme or target.netloc or not target.path.startswith("/"):
        return None
    allowed = ("/navigator/", "/both/", "/chrome/", "/chrome_youtube/", "/EAGLE_", "/folders/", "/library/")
    return value if target.path == "/" or target.path == "/search" or target.path.startswith(allowed) else None


def render_resource_detail(**context):
    resource = context["resource"]
    current_context = context.get("current_context") or {}
    related = [
        {"title": item["title"], "url": item["detail_url"] + (
             "?" + urlencode({"return_to": current_context["return_url"]}) if current_context.get("return_url") else ""),
         "thumbnail_route": item.get("thumbnail_url"), "why": item.get("domain") or item["source_type"]}
        for item in context.get("similar_resources", [])
    ]
    if source_preference():
        from .common import catalog_related_for_origin
        related = catalog_related_for_origin("resources", resource["id"], limit=6)
    return render_template("detail.html", detail={
        "kind": "resource", "title": resource["title"],
        "source_label": f"{context['resource_type_labels'].get(resource['source_type'], resource['source_type'])} · {resource.get('domain') or '網站'}",
        "description": resource.get("summary_one_line") or resource.get("description"),
        "tags": resource.get("tag_names") or [], "thumbnail_url": resource.get("thumbnail_url"),
        "parent_url": current_context.get("return_url") or url_for("catalog.navigator_page", scope="all"),
        "original_url": resource.get("original_url"),
        "browse_targets": detail_browse_targets("resources", resource["id"]),
    }, related_items=related, **context)


def management_context(database, facets):
    """The management shell owns source sync/watch/analysis controls."""
    from pathlib import Path
    from src.flowinone.catalog.presentation import SOURCE_LABELS as catalog_labels, bookmark_source_health
    from src.flowinone.catalog.query import CATALOG_SOURCES, CatalogQuery
    from src.flowinone.catalog.similarity import CatalogSimilarityService
    from src.flowinone.catalog.watch import CatalogSourceWatcher
    from src.flowinone.catalog.eagle_library import EagleLibraryService
    from src.flowinone.config import CHROME_BOOKMARK_PATH

    return {
        "query": CatalogQuery.create(scope="all"), "payload": {"facets": facets},
        "catalog_sources": [{"key": source, "label": catalog_labels[source]} for source in CATALOG_SOURCES],
        "source_watch_status": CatalogSourceWatcher(database).status(),
        "similarity_status": CatalogSimilarityService(database).status(),
        "eagle_library": EagleLibraryService(database).status(),
        "bookmark_source_health": bookmark_source_health(Path(CHROME_BOOKMARK_PATH),
            facets["sync"].get("bookmarks", {}), facets["sources"].get("bookmarks", 0)),
        "eagle_available": get_feature_flags().get("eagle"), "maintenance_open": True,
    }
