from pathlib import Path

import pytest

from src.flowinone.resource_library.curation import CollectionService, DraftNoteService
from src.flowinone.resource_library.database import ResourceDatabase
from src.flowinone.resource_library.obsidian import ObsidianConflict, ObsidianExporter
from src.flowinone.resource_library.service import ResourceService
from src.flowinone.resource_library.settings import ResourceSettings


def settings_for(tmp_path: Path, vault: Path) -> ResourceSettings:
    return ResourceSettings(
        data_dir=tmp_path / "data",
        database_path=tmp_path / "resource.db",
        content_dir=tmp_path / "data" / "content",
        export_dir=tmp_path / "data" / "exports" / "resources",
        obsidian_vault_path=vault,
        obsidian_target_folder="Resources/Digested",
        llm_base_url=None,
        llm_api_key=None,
        llm_model=None,
        user_context="test",
        http_timeout_seconds=30,
        max_download_bytes=2_000_000,
    )


def test_collection_can_mix_resource_and_eagle_and_create_synthesis(tmp_path):
    database = ResourceDatabase(tmp_path / "resource.db")
    resources = ResourceService(database, link_thumbnail_cache=False)
    resource = resources.create_url(
        "https://example.com/article", title="Article", enqueue=False
    )["resource"]
    collections = CollectionService(database)
    collection = collections.create("Visual research")
    collection = collections.add_item(
        collection["id"], source_kind="resource", source_id=resource["id"]
    )
    collection = collections.add_item(
        collection["id"],
        source_kind="eagle",
        source_id="eagle-item-1",
        title="Reference image",
        url="eagle://item/eagle-item-1",
        annotation="Useful composition",
    )
    assert {item["source_kind"] for item in collection["items"]} == {"resource", "eagle"}

    notes = DraftNoteService(database)
    note = notes.create_from_collection(collection, "Synthesis")
    assert note["note_type"] == "synthesis"
    assert note["source_count"] == 2
    assert resources.repository.get(resource["id"])["promoted"]
    database.dispose()


def test_literature_note_and_obsidian_export_are_idempotent_and_safe(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    settings = settings_for(tmp_path, vault)
    database = ResourceDatabase(settings.database_path)
    resources = ResourceService(database, link_thumbnail_cache=False)
    resource = resources.create_url(
        "https://example.com/literature", title="Literature Source", enqueue=False
    )["resource"]
    resources.repository.update(
        resource["id"],
        {"user_note": "My own observation"},
    )
    notes = DraftNoteService(database)
    note = notes.create_literature_note(resource["id"])
    same_note = notes.create_literature_note(resource["id"])
    assert same_note["id"] == note["id"]

    exporter = ObsidianExporter(database, settings)
    result = exporter.export_note(note["id"])
    path = Path(result["path"])
    assert path.is_file()
    rendered = path.read_text(encoding="utf-8")
    assert 'type: "literature_note"' in rendered
    assert resource["id"] in rendered
    assert exporter.export_note(note["id"])["path"] == str(path)

    path.write_text("user changed this note", encoding="utf-8")
    with pytest.raises(ObsidianConflict):
        exporter.export_note(note["id"])
    database.dispose()


def test_resource_markdown_mirror_is_derived(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    settings = settings_for(tmp_path, vault)
    database = ResourceDatabase(settings.database_path)
    resource = ResourceService(database, link_thumbnail_cache=False).create_url(
        "https://example.com/mirror", title="Mirror", enqueue=False
    )["resource"]
    result = ObsidianExporter(database, settings).export_resource_mirror(resource["id"])
    path = Path(result["path"])
    assert path.is_file()
    assert "reading_state:" in path.read_text(encoding="utf-8")
    database.dispose()


def test_collection_rejects_script_urls(tmp_path):
    database = ResourceDatabase(tmp_path / "resource.db")
    collections = CollectionService(database)
    collection = collections.create("Safe collection")
    with pytest.raises(ValueError):
        collections.add_item(
            collection["id"],
            source_kind="bookmark",
            source_id="unsafe",
            title="Unsafe",
            url="javascript:alert(1)",
        )
    database.dispose()
