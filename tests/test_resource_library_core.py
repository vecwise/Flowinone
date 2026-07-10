from pathlib import Path

import pytest
from sqlalchemy import inspect

from src.flowinone.resource_library.canonical import (
    InvalidResourceURL,
    normalize_resource_url,
    sanitize_markdown_filename,
)
from src.flowinone.resource_library.database import ResourceDatabase
from src.flowinone.resource_library.importers import (
    BookmarkRecord,
    iter_chrome_json_bookmarks,
    iter_netscape_bookmark_html,
)
from src.flowinone.resource_library.jobs import JobQueue
from src.flowinone.resource_library.service import ResourceService


@pytest.fixture
def resource_service(tmp_path):
    database = ResourceDatabase(tmp_path / "resource.db")
    try:
        yield ResourceService(database, link_thumbnail_cache=False)
    finally:
        database.dispose()


def test_migration_creates_domain_and_fts_tables(tmp_path):
    database = ResourceDatabase(tmp_path / "resource.db")
    try:
        tables = set(inspect(database.engine).get_table_names())
        assert {
            "resources",
            "resource_origins",
            "resource_tags",
            "processing_jobs",
            "collections",
            "draft_notes",
            "resource_fts",
            "alembic_version",
        } <= tables
    finally:
        database.dispose()

    reopened = ResourceDatabase(tmp_path / "resource.db")
    try:
        assert "resources" in inspect(reopened.engine).get_table_names()
    finally:
        reopened.dispose()


def test_resource_url_identity_and_filename_safety():
    assert normalize_resource_url("HTTPS://Example.COM/a/?utm_source=x#frag") == "https://example.com/a"
    assert sanitize_markdown_filename('../A:B? "note"') == "A-B- -note.md"
    assert sanitize_markdown_filename("Already.md") == "Already.md"
    with pytest.raises(InvalidResourceURL):
        normalize_resource_url("file:///etc/passwd")


def test_chrome_json_preserves_multiple_folder_placements():
    payload = {
        "roots": {
            "bookmark_bar": {
                "type": "folder",
                "name": "Bookmarks",
                "children": [
                    {
                        "type": "folder",
                        "name": "A",
                        "children": [
                            {"type": "url", "id": "1", "name": "One", "url": "https://example.com"}
                        ],
                    },
                    {
                        "type": "folder",
                        "name": "B",
                        "children": [
                            {"type": "url", "id": "2", "name": "Two", "url": "https://example.com"}
                        ],
                    },
                ],
            }
        }
    }
    records = list(iter_chrome_json_bookmarks(payload))
    assert [record.folder_path for record in records] == ["Bookmarks / A", "Bookmarks / B"]


def test_netscape_html_importer_keeps_nested_folder():
    html = """
    <!DOCTYPE NETSCAPE-Bookmark-file-1>
    <DL><p>
      <DT><H3>Research</H3>
      <DL><p>
        <DT><A HREF="https://example.com/a" ADD_DATE="1700000000">Example</A>
      </DL><p>
    </DL><p>
    """
    records = list(iter_netscape_bookmark_html(html))
    assert len(records) == 1
    assert records[0].folder_path == "Research"
    assert records[0].source == "chrome_html"


def test_duplicate_urls_merge_but_origins_and_fts_remain(resource_service):
    summary = resource_service.import_records(
        [
            BookmarkRecord("https://example.com/a?utm_source=one", "Knowledge Base", "A"),
            BookmarkRecord("https://example.com/a", "Duplicate", "B"),
        ],
        enqueue=False,
        link_thumbnails=False,
    )
    assert summary.created == 1
    assert summary.duplicates == 1
    assert resource_service.repository.stats()["total"] == 1
    page = resource_service.repository.list(query="Knowledge")
    assert page.total == 1
    detail = resource_service.repository.get(page.items[0]["id"])
    assert {origin["folder_path"] for origin in detail["origins"]} == {"A", "B"}


def test_workflow_tags_and_job_retry(resource_service):
    result = resource_service.create_url(
        "https://example.com/resource",
        title="Resource",
        enqueue=False,
    )
    resource_id = result["resource"]["id"]
    updated = resource_service.update_resource(resource_id, {"reading_state": "reading", "priority": 4})
    assert updated["reading_state"] == "reading"
    assert updated["priority"] == 4
    tagged = resource_service.replace_tags(resource_id, ["AI", "Knowledge Base"])
    assert {name.casefold() for name in tagged["tag_names"]} >= {"ai", "knowledge base"}

    queue = JobQueue(resource_service.database)
    job = queue.queue("fetch_metadata", resource_id=resource_id, payload={"url": "https://example.com"})
    claimed = queue.claim("test-owner")
    assert [row["id"] for row in claimed] == [job["id"]]
    assert queue.fail(job["id"], "temporary") == "retry"


def test_invalid_reading_transition_is_rejected(resource_service):
    resource_id = resource_service.create_url(
        "https://example.com/transition", enqueue=False
    )["resource"]["id"]
    resource_service.update_resource(resource_id, {"reading_state": "skimmed"})
    with pytest.raises(ValueError):
        resource_service.update_resource(resource_id, {"reading_state": "inbox"})


def test_find_similar_uses_explainable_tag_and_type_overlap(resource_service):
    first = resource_service.create_url(
        "https://example.com/similar-a", title="Knowledge Architecture", enqueue=False
    )["resource"]
    second = resource_service.create_url(
        "https://example.org/similar-b", title="Knowledge Workflow", enqueue=False
    )["resource"]
    resource_service.create_url(
        "https://unrelated.example/similar-c", title="Cooking", enqueue=False
    )
    resource_service.replace_tags(first["id"], ["knowledge-base"])
    resource_service.replace_tags(second["id"], ["knowledge-base"])
    similar = resource_service.repository.find_similar(first["id"])
    assert similar[0]["id"] == second["id"]
