import os
import platform

DB_route_external = ""
DB_route_internal = ""

def _detect_chrome_bookmark_path() -> str:
    system = platform.system()
    if system == "Windows":
        local_app_data = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
        return os.path.join(local_app_data, "Google", "Chrome", "User Data", "Default", "Bookmarks")
    if system == "Linux":
        return os.path.expanduser("~/.config/google-chrome/Default/Bookmarks")
    # macOS default
    return os.path.expanduser("~/Library/Application Support/Google/Chrome/Default/Bookmarks")

CHROME_BOOKMARK_PATH = _detect_chrome_bookmark_path()

# Domains whose thumbnails should be fetched via OpenGraph (e.g. specialised sites)
SPECIAL_THUMBNAIL_DOMAINS = []
