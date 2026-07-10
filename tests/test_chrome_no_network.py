import json

from flask import Flask

import routes
from src.file_handler import chrome_bookmarks, media_cache
from src.file_handler.thumbnails.store import ThumbnailStore


def test_chrome_page_never_fetches_remote_http(monkeypatch, tmp_path):
    bookmarks_path = tmp_path / "Bookmarks"
    bookmarks_path.write_text(
        json.dumps({
            "roots": {
                "bookmark_bar": {
                    "id": "1",
                    "name": "Bookmarks bar",
                    "type": "folder",
                    "children": [{
                        "id": "2",
                        "name": "A public article",
                        "type": "url",
                        "url": "https://example.com/article",
                    }],
                }
            }
        }),
        encoding="utf-8",
    )
    store = ThumbnailStore(str(tmp_path / "cache.db"), str(tmp_path / "thumbs"))

    monkeypatch.setattr(chrome_bookmarks, "CHROME_BOOKMARK_PATH", str(bookmarks_path))
    monkeypatch.setattr(media_cache, "get_thumbnail_store", lambda: store)
    monkeypatch.setattr(routes, "get_thumbnail_store", lambda: store)
    monkeypatch.setattr(routes, "_compute_feature_flags", lambda: {
        "eagle": False,
        "chrome": True,
        "youtube": True,
        "db": False,
    })

    def fail_network(*args, **kwargs):
        raise AssertionError("Chrome request path attempted remote HTTP")

    monkeypatch.setattr("requests.sessions.Session.get", fail_network)
    monkeypatch.setattr("requests.get", fail_network)

    app = Flask(__name__, template_folder=str(routes.os.path.join(routes.os.path.dirname(__file__), "..", "templates")))
    app.config.update(TESTING=True)
    routes.register_routes(app)

    response = app.test_client().get("/chrome/bookmark_bar/")
    assert response.status_code == 200
    assert b"data-thumbnail-id" in response.data
