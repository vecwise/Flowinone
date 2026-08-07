"""Local duplicate and visual-similarity coverage."""

from __future__ import annotations

import shutil

from PIL import Image

from run import create_app
from src.file_handler import item_db
from src.flowinone.catalog.service import CatalogQuery, CatalogService, CatalogSyncService
from src.flowinone.catalog.similarity import CatalogSimilarityService
from src.flowinone.resource_library.database import get_resource_database
from src.flowinone.resource_library.jobs import JobQueue
from src.flowinone.resource_library.worker import ResourceWorker


def _local_catalog(monkeypatch, tmp_path):
    media_root = tmp_path / "media"
    media_root.mkdir()
    original = media_root / "original.png"
    duplicate = media_root / "duplicate.png"
    visually_similar = media_root / "visually-similar.png"
    Image.new("RGB", (32, 24), "red").save(original)
    shutil.copyfile(original, duplicate)
    # This differs at the byte level but its dHash is intentionally equal for
    # a flat field of colour, giving a deterministic perceptual-match fixture.
    Image.new("RGB", (32, 24), "blue").save(visually_similar)

    monkeypatch.setattr(item_db, "ITEM_DB_PATH", str(tmp_path / "items.db"))
    item_db.update_item_database(str(media_root))
    database = get_resource_database(tmp_path / "catalog.db")
    CatalogSyncService(database).sync(("local",))
    items = CatalogService(database).list(
        CatalogQuery.create(scope="gallery", sources=["local"], limit=20)
    )["items"]
    by_title = {item["title"]: item["id"] for item in items}
    return database, by_title


def test_similarity_analysis_identifies_exact_and_visual_matches(monkeypatch, tmp_path):
    database, by_title = _local_catalog(monkeypatch, tmp_path)
    service = CatalogSimilarityService(database)

    first = service.analyze_local_images()
    assert first["candidates"] == 3
    assert first["analyzed"] == 3
    assert first["failed"] == 0
    assert service.analyze_local_images()["skipped"] == 3

    matches = service.similar_images(by_title["original.png"], max_distance=0)
    kinds = {item["title"]: item["match_type"] for item in matches["items"]}
    assert kinds == {
        "duplicate.png": "duplicate",
        "visually-similar.png": "visual",
    }
    status = service.status()
    assert status["analyzed_items"] == 3
    assert status["duplicate_groups"] == 1
    assert status["duplicate_items"] == 2


def test_similarity_job_uses_the_durable_worker_queue(monkeypatch, tmp_path):
    database, by_title = _local_catalog(monkeypatch, tmp_path)
    job = JobQueue(database).queue(
        "catalog_similarity",
        resource_id=None,
        payload={"limit": 20_000, "force": False},
        input_hash="similarity-test",
    )

    assert job["status"] == "pending"
    assert ResourceWorker(database).run_until_idle() == 1
    assert JobQueue(database).get(job["id"])["status"] == "complete"
    matches = CatalogSimilarityService(database).similar_images(
        by_title["original.png"], max_distance=0
    )
    assert matches["analyzed"] is True
    assert matches["items"][0]["match_type"] == "duplicate"


def test_similarity_api_decorates_results_with_safe_launch_targets(monkeypatch, tmp_path):
    database, by_title = _local_catalog(monkeypatch, tmp_path)
    CatalogSimilarityService(database).analyze_local_images()
    app = create_app(
        {
            "TESTING": True,
            "FLOWINONE_RESOURCE_DB_PATH": str(database.path),
            "CHROME_BOOKMARK_PATH": str(tmp_path / "Bookmarks"),
            "FLOWINONE_RESOURCE_LINK_THUMBNAILS": False,
        }
    )

    response = app.test_client().get(
        f'/api/catalog/items/{by_title["original.png"]}/similar-images?max_distance=0'
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["analyzed"] is True
    duplicate = next(item for item in payload["items"] if item["match_type"] == "duplicate")
    assert duplicate["launch_source"] == "local"
    assert duplicate["launch_uri"].startswith("/image/")
