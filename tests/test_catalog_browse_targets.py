"""Browse existing relationships through one Navigator without copying members."""

import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from bs4 import BeautifulSoup
import pytest
from sqlalchemy import text

from run import create_app
from src.flowinone.catalog.artifacts import PersonService
from src.flowinone.catalog.browse import CatalogService
from src.flowinone.catalog.query import CatalogQuery
from src.flowinone.catalog.sync import CatalogSyncService
from src.flowinone.catalog.targets import BrowseTargetService
from src.flowinone.resource_library.database import get_resource_database
from src.flowinone.web.browse_sources import COOKIE_NAME


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    signature = hashlib.sha256(b"library-A").hexdigest()
    monkeypatch.setattr("src.flowinone.catalog.blueprint.is_eagle_available", lambda: True)
    monkeypatch.setattr("src.flowinone.catalog.targets.get_eagle_catalog_folders",
                        lambda: (signature, {"board": "室內參考", "other": "其他"}))
    monkeypatch.setattr("src.flowinone.web.common.compute_feature_flags",
                        lambda: {"eagle": True, "chrome": False, "youtube": False, "db": False})
    app = create_app({"TESTING": True, "FLOWINONE_CATALOG_SYNC_INLINE": False,
                      "FLOWINONE_RESOURCE_DB_PATH": str(tmp_path / "targets.db"),
                      "FLOWINONE_RESOURCE_LINK_THUMBNAILS": False})
    db = get_resource_database(Path(app.config["FLOWINONE_RESOURCE_DB_PATH"]), migrate=True)
    sync, ids = CatalogSyncService(db), []
    with db.write_transaction() as conn:
        for index, folders in enumerate((["board", "other"], [{"id": "board"}], ["elsewhere"], [])):
            source = "local" if index == 3 else "eagle"
            ids.append(sync._upsert(
                conn, identity_key=f"{source}:{index}", source_kind=source, source_key=str(index),
                item_type="image", title=f"Photo {index}", detail_uri=f"/EAGLE_image/{index}/",
                metadata={"folders": folders}, tags=(("木質 & warm:色", "source"),),
                captured_at=f"2026-10-{index + 1:02d}",
            ))
        # A second origin must not let a folder target leak through when Eagle
        # is excluded from the selected sources.
        sync._upsert(conn, identity_key="eagle:0", source_kind="resources", source_key="merged",
                     item_type="image", title="Photo 0", detail_uri="/resources/merged/")
        conn.execute(text(
            "INSERT INTO catalog_sync_state(source_kind,source_signature,status,item_count,synced_at) "
            "VALUES('eagle',:signature,'complete',3,'2026-10-19')"
        ), {"signature": signature})
        tag = conn.execute(text("SELECT id FROM catalog_tags LIMIT 1")).scalar()
    people = PersonService(db)
    person, namesake = people.create("A"), people.create("A")
    people.link(person["id"], ids[0])
    people.link(person["id"], ids[3])
    people.link(namesake["id"], ids[1])
    return app, app.test_client(), db, ids, {
        "tag": f"tag:{tag}", "person": f"person:{person['id']}",
        "namesake": f"person:{namesake['id']}", "folder": f"eagle_folder:{signature}:board",
    }


def destination(target, **values):
    return "/navigator/?" + urlencode({"target": target, **values})


@pytest.mark.parametrize("kind,positions", [("tag", (0, 1, 2, 3)), ("person", (0, 3)),
                                           ("namesake", (1,)), ("folder", (0, 1))])
def test_targets_filter_before_count_and_keyset_pagination(catalog, kind, positions):
    _app, client, db, ids, targets = catalog
    query = CatalogQuery.create(target=targets[kind], limit=1, sort="title")
    service = CatalogService(db)
    seen = []
    while True:
        page = service.list(query)
        assert page["total_estimate"] == len(positions)
        seen.extend(item["id"] for item in page["items"])
        if not page["next_cursor"]:
            break
        query = CatalogQuery.create(**{**query.public_dict(), "cursor": page["next_cursor"]})
    assert seen == [ids[index] for index in positions]
    api = client.get("/api/catalog/items", query_string={"target": targets[kind], "sort": "title"})
    assert api.status_code == 200
    assert [item["id"] for item in api.json["items"]] == seen
    assert service.matching_ids(query, ids) == set(seen)


def test_item_exposes_named_destinations_without_carrying_old_filters(catalog):
    _app, client, _db, ids, targets = catalog
    response = client.get(f"/api/catalog/items/{ids[0]}?source=eagle&q=old&folder=old")
    assert response.status_code == 200
    entries = response.json["browse_targets"]
    assert {entry["kind"] for entry in entries} == {"tag", "person", "eagle_folder"}
    assert {entry["label"] for entry in entries} == {"木質 & warm:色", "A", "室內參考", "其他"}
    for entry in entries:
        params = parse_qs(urlsplit(entry["url"]).query)
        assert params["source"] == ["eagle"]
        assert not {"q", "folder", "tags", "cursor", "context"} & params.keys()
        page = client.get(entry["url"], follow_redirects=True)
        assert page.status_code == 200
        soup = BeautifulSoup(page.data, "html.parser")
        assert soup.select_one("h1").get_text(strip=True) == entry["label"]
        assert soup.select_one('form.navigator-query input[name="target"]')["value"] == entry["target"]
    assert targets["person"] in {entry["target"] for entry in entries}


def test_search_sort_sources_and_clear_keep_target_semantics(catalog):
    _app, client, _db, ids, targets = catalog
    response = client.get(destination(targets["person"], q="Photo", sort="title", limit=1))
    soup = BeautifulSoup(response.data, "html.parser")
    assert "人物：A" in soup.get_text()
    assert soup.select_one('form.navigator-query input[name="target"]')["value"] == targets["person"]
    assert soup.select_one('.catalog-sort input[name="target"]')["value"] == targets["person"]
    next_page = client.get(soup.select_one(".gallery-pagination a")["href"])
    assert ids[3] in next_page.get_data(as_text=True)
    chip = next(link for link in soup.select(".catalog-filter-chip") if "人物：" in link.get_text())
    assert "target" not in parse_qs(urlsplit(chip["href"]).query)
    assert parse_qs(urlsplit(chip["href"]).query)["q"] == ["Photo"]


def test_folder_membership_respects_stale_hidden_and_source_selection(catalog):
    _app, client, db, ids, targets = catalog
    service = CatalogService(db)
    assert service.list(CatalogQuery.create(target=targets["folder"], sources=["resources"]))["total_estimate"] == 0
    client.set_cookie(COOKIE_NAME, "resources")
    response = client.get(destination(targets["folder"]), follow_redirects=True)
    assert "目前來源未包含 EAGLE" in response.get_data(as_text=True)
    assert not BeautifulSoup(response.data, "html.parser").select("[data-navigator-item]")
    entries = client.get(f"/api/catalog/items/{ids[0]}").json["browse_targets"]
    assert not any(entry["kind"] == "eagle_folder" for entry in entries)
    with db.write_transaction() as conn:
        conn.execute(text("UPDATE catalog_origins SET stale=1 WHERE source_kind='eagle' AND catalog_item_id=:id"), {"id": ids[0]})
    service.record_event(ids[1], "hide")
    assert service.list(CatalogQuery.create(target=targets["folder"]))["total_estimate"] == 0


def test_folder_reference_is_bound_to_library_and_missing_names_are_safe(catalog, monkeypatch):
    _app, client, db, _ids, targets = catalog
    wrong = targets["folder"].replace(hashlib.sha256(b"library-A").hexdigest(), "b" * 64)
    assert client.get(destination(wrong)).status_code == 404
    assert not CatalogService(db).list(CatalogQuery.create(target=wrong))["items"]
    monkeypatch.setattr("src.flowinone.catalog.targets.get_eagle_catalog_folders",
                        lambda: ("b" * 64, {"board": "Wrong library name"}))
    assert BrowseTargetService(db).describe(targets["folder"])["label"] == "board"
    monkeypatch.setattr(CatalogService, "_eagle_allowed", lambda _self: False)
    assert not CatalogService(db).list(CatalogQuery.create(target=targets["folder"]))["items"]


def test_people_links_invalidate_cached_counts_and_keep_identity(catalog):
    _app, client, db, ids, targets = catalog
    service = CatalogService(db)
    query = CatalogQuery.create(target=targets["person"])
    assert service.list(query)["total_estimate"] == 2
    people = PersonService(db)
    person_id = targets["person"].split(":")[1]
    people.link(person_id, ids[2])
    people.link(person_id, ids[2], source="imported")
    assert service.list(query)["total_estimate"] == 3
    people.rename(person_id, "New name")
    soup = BeautifulSoup(client.get(destination(targets["person"])).data, "html.parser")
    assert soup.select_one("h1").get_text(strip=True) == "New name"
    assert service.list(CatalogQuery.create(target=targets["namesake"]))["total_estimate"] == 1


@pytest.mark.parametrize("target", ["person", "unknown:a", "person:", "eagle_folder:board", "tag:a\n", "tag:" + "x" * 1000])
def test_malformed_targets_are_rejected(catalog, target):
    _app, client, _db, ids, _targets = catalog
    assert client.get(destination(target)).status_code == 400
    assert client.get("/api/catalog/items", query_string={"target": target}).status_code == 400
    assert client.get(f"/api/catalog/items/{ids[0]}/related", query_string={"target": target}).status_code == 400
    assert client.post("/api/catalog/sessions", json={"query": {"target": target}}).status_code == 400
    assert client.post("/api/catalog/saved-searches", json={"label": "Bad", "query": {"target": target}}).status_code == 400


def test_missing_person_and_tag_do_not_open_all_items(catalog):
    _app, client, _db, _ids, _targets = catalog
    for target in ("person:missing", "tag:missing"):
        assert client.get(destination(target)).status_code == 404
        assert client.get("/api/catalog/items", query_string={"target": target}).json["items"] == []


def test_target_survives_sessions_saved_searches_and_cursor_identity(catalog):
    _app, client, db, _ids, targets = catalog
    service = CatalogService(db)
    query = CatalogQuery.create(target=targets["person"], limit=1)
    cursor = service.list(query)["next_cursor"]
    with pytest.raises(ValueError, match="cursor"):
        service.list(CatalogQuery.create(target=targets["tag"], cursor=cursor))
    session = client.post("/api/catalog/sessions", json={"query": query.public_dict()})
    assert session.status_code == 201
    assert session.json["query"]["target"] == targets["person"]
    saved = client.post("/api/catalog/saved-searches", json={"label": "A", "query": query.public_dict()})
    assert saved.status_code == 201
    assert saved.json["query"]["target"] == targets["person"]
    response = client.get(f"/navigator/views/{saved.json['id']}/", follow_redirects=True)
    payload = BeautifulSoup(response.data, "html.parser").select_one("[data-navigator-query-payload]")
    assert json.loads(payload["data-navigator-query-payload"])["target"] == targets["person"]


def test_full_item_page_exposes_the_same_targets_as_preview(catalog, monkeypatch):
    _app, client, _db, ids, targets = catalog
    monkeypatch.setattr("src.flowinone.web.eagle.get_eagle_image_details", lambda _id: (
        {"name": "Photo 0", "category": "eagle-image", "tags": ["木質 & warm:色"], "path": "/EAGLE_image/0/"},
        {"name": "Photo 0", "source_url": "/static/default_thumbnail.svg", "thumbnail_route": "/static/default_thumbnail.svg"},
    ))
    response = client.get("/EAGLE_image/0/")
    assert response.status_code == 200
    soup = BeautifulSoup(response.data, "html.parser")
    actual = {parse_qs(urlsplit(link["href"]).query)["target"][0]
              for link in soup.select(".navigator-browse-group a")}
    expected = {entry["target"] for entry in client.get(f"/api/catalog/items/{ids[0]}").json["browse_targets"]}
    assert actual == expected
    assert targets["person"] in actual and targets["folder"] in actual
