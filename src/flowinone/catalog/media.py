"""Conservative browse categories; source types and stored records stay intact.

Only content URL patterns qualify, never a site's home, search or topic page.
The same patterns drive SQL filtering (before pagination) and card labels.
"""

import re

from src.file_handler.thumbnails.extensions import local_media_url_rules

MEDIA_LABELS = {"": "全部", "video": "影片", "image": "圖片", "album": "漫畫／圖集", "page": "網頁／其他"}
URL_RULES = {
    "video": (
        ("youtube.com", "/watch?v=_%"), ("www.youtube.com", "/watch?v=_%"),
        ("youtube.com", "/watch?%&v=_%"), ("www.youtube.com", "/watch?%&v=_%"),
        ("youtube.com", "/shorts/%"), ("www.youtube.com", "/shorts/%"),
        ("youtu.be", "/_%"),
    ),
}


def _category_patterns():
    local_rules = local_media_url_rules()
    for category in ("video", "image", "album"):
        rules = (*URL_RULES.get(category, ()), *local_rules.get(category, ()))
        yield category, tuple(f"{scheme}://{host}{path}" for host, path in rules for scheme in ("https", "http"))


def media_category(item_type, url=""):
    kind = str(item_type or "").lower()
    if kind in {"image", "video"}:
        return kind
    if kind in {"album", "comic", "manga", "image_gallery"}:
        return "album"
    for category, patterns in _category_patterns():
        for pattern in patterns:
            regex = re.escape(pattern).replace("%", ".*").replace("_", ".")
            if re.fullmatch(regex, str(url or "").lower()):
                return category
    return "page"


def media_category_sql():
    """Use the same trusted rules as card labels, escaping SQL literals."""
    cases = ["WHEN i.item_type='image' THEN 'image'", "WHEN i.item_type='video' THEN 'video'",
             "WHEN i.item_type IN ('album','comic','manga','image_gallery') THEN 'album'"]
    for category, patterns in _category_patterns():
        matches = " OR ".join("lower(i.original_url) LIKE '" + pattern.replace("'", "''") + "'" for pattern in patterns)
        if matches:
            cases.append(f"WHEN ({matches}) THEN '{category}'")
    return "CASE " + " ".join(cases) + " ELSE 'page' END"
