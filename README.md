# Flowinone — multi-source resource renderer

Flowinone is a local-first renderer for visual media, bookmarks, and imported web resources. Its Navigator lets you browse or search across Local files, Eagle, Chrome Bookmarks, and Resources without moving the authoritative source into a new system.

```mermaid
flowchart LR
  local["Local media"] --> catalog["Catalog projection"]
  eagle["Eagle"] --> catalog
  chrome["Chrome Bookmarks"] --> catalog
  resource["Imported URLs"] --> catalog
  catalog --> navigator["Navigator\n素材 · 全部內容"]
  resource --> detail["Resource detail"]
```

## What it does

- **Navigator · 素材** (`/navigator/?scope=gallery`) — flat Local/Eagle/Bookmark browsing with source multi-select, filters, stable random order, scroll restoration, favorites, and bounded sessions.
- **Navigator · 全部內容** (`/navigator/?scope=all`) — SQLite FTS across Local, Eagle, Bookmarks, and Resources, with source/type/tag facets, tag ANY/ALL, and keyset pagination.
- **Resources** — import URLs or Chrome bookmarks; render metadata, thumbnail, summary, extracted article/PDF/transcript text, tags, and related resources.
- **Source-specific pages** — retain Eagle folders/tags/smart folders, Chrome bookmark tree, local folder views, image/video viewers, and maintenance tools.
- **Local sidecars** — portable `.flowinone.json` metadata import/export/audit for local media.

`/gallery/` and `/search/` are compatibility redirects only. They preserve query parameters and redirect to the Navigator's **素材** and **全部內容** scopes respectively; they are not separate user-facing surfaces.

The global navigation search preserves the current scope when used inside Navigator. From Resources or any other non-Navigator page, it opens Navigator in **全部內容** (`scope=all`), so the “搜尋素材與資源” field includes Resources as advertised.

Flowinone is deliberately **not** a knowledge-workflow application. BUILD, THINK, LEARN, SCAN, RECOVER, WRITE, Entries, Projects, Collections, Notes, and Obsidian export are not part of the running app.

Read [architecture](docs/architecture.md), [how to use it](docs/renderer-architecture.md), [schema](docs/schema.md), and [operational workflows](docs/workflows.md).

## Start

Use the existing Conda environment:

```bash
conda activate py3.11
python -m pip install -r requirements.txt
python run.py
```

Visit `http://localhost:5894`; `/` redirects to the Navigator's **素材** scope at `/navigator/?scope=gallery`.

Run background enrichment and bookmark-thumbnail processing in a second terminal:

```bash
conda run -n py3.11 python -m src.flowinone.workers
```

The Flask application factory never starts workers. This keeps Werkzeug reloads
and multi-process WSGI servers from creating duplicate worker sets. The web app
remains browsable without the worker process, but queued thumbnails and Resource
enrichment wait until it is running.

For headless setup, set valid Local media roots in `config.json` and use `FLOWINONE_HEADLESS=1`. `CHROME_BOOKMARK_PATH` defaults to the platform Chrome profile path and may be overridden in app configuration.

## Typical use

1. Open **Navigator · 素材** to browse Local, Eagle, and Bookmarks visually.
2. Switch to **全部內容** in the same Navigator when you do not know which source contains the item or want to include Resources.
3. Use the global search from another page to search **全部內容** by default; when already in Navigator, it keeps the current scope.
4. Open **Resources** for imported URLs and their extracted full text.
5. Add user tags when they make future search better.
6. Sync a source after a large change.

## Maintenance

```bash
conda run -n py3.11 flask --app run resources-db-upgrade
conda run -n py3.11 flask --app run catalog-sync --source all
conda run -n py3.11 flask --app run resources-worker --limit 20
conda run -n py3.11 flask --app run resources-rebuild-fts
conda run -n py3.11 flask --app run catalog-relations-rebuild
conda run -n py3.11 flask --app run sidecars-audit
conda run -n py3.11 flask --app run sidecars-export
conda run -n py3.11 flask --app run sidecars-import
```

`resources-worker` performs metadata, thumbnail, content extraction, and optional AI-summary work. It never writes notes or exports to Obsidian.

## Architecture

- `run.py` — `create_app()` application factory and development-server entry point.
- `routes.py` — compatibility registration entry point only.
- `src/flowinone/web/` — namespaced Local, Chrome, Eagle, and media Blueprints,
  shared Pydantic API contracts, and response validation.
- `src/flowinone/workers.py` — dedicated background-worker process runtime.
- `src/flowinone/gallery/` — retained Gallery read model/API, design lab, and `/gallery/` compatibility redirect.
- `src/flowinone/catalog/` — Navigator HTTP surface plus the rebuildable cross-source Catalog, FTS, facets, cursors, events, sessions, and explainable item relations; it also owns the `/search/` compatibility redirect.
- `src/flowinone/resource_library/` — imported URL database, extraction jobs, Resource UI/API, and optional AI metadata.
- `src/file_handler/` — filesystem, Chrome, Eagle adapters, thumbnails, item DB, and sidecars.
- `migrations/` — Alembic history. `0007_renderer_only_cleanup` and `0008_renderer_resource_schema` remove the former knowledge-workflow tables, fields, and personal-note FTS data.

## Boundaries

Not planned here: cross-device sync, Raw → Wiki, Obsidian integration, OneTab/Keep/Notion/social connectors, autoplay/infinite feeds, Graph DB, agent swarm, or a full React/FastAPI rewrite.

Before upgrading an existing installation, back up `data/flowinone.sqlite3`. Migrations `0007_renderer_only_cleanup` and `0008_renderer_resource_schema` are intentionally destructive for former workflow, curation, and personal-note records.

## License

Distributed under the [MIT License](LICENSE).
