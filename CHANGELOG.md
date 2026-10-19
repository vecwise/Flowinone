# Changelog

## Unreleased

### Added

- Shared desktop browse, detail and management templates with three UI variants.
- Persistent source filters, media categories, thumbnail shapes and continuous image/video browsing.
- Searchable Eagle folder albums, primary-library protection and previewed catalog cleanup.
- Bookmark thumbnail retries with progress reporting and shared cache processing.

### Changed

- Keep site-specific media rules in optional machine-local extensions.
- Streamline documentation around current behavior and maintenance commands.

### Fixed

- Reduce SQLite writer contention and recover background jobs after transient locks.
- Preserve source context, cached covers and browse position across navigation.

## 2026-10-18

### Added

- Explicit, digest-checked consolidation of reviewed Chrome URL-owner conflicts. Preserve the existing target row, both GUID bindings/origins, folder placements, tag unions, descriptions and URL histories; reject changed previews or pairs with unreviewed user data, independent origins or work. Normal sync still does not merge conflicts.
- Scoped Chrome guid/id bindings that retain existing Catalog and Resources IDs when bookmark URLs change. Shared rows with diverging URLs or an occupied destination are held for review without automatic splitting or merging.
- Standalone read-only backfill previews, explicit plan-digest confirmation, backup before migration, and transactional application. Source changes or edited previews require revalidation.

### Fixed

- Retain extra bookmark-origin metadata across regular synchronization, including retired-row and original-URL provenance. Preserve the selected target GUID's display title while it survives.
- Refresh Chrome folder placements and source titles even while an accepted content URL is held by a collision.

## 2026-10-04

### Fixed

- Detect Chrome's live `AccountBookmarks` JSON when `Default/Bookmarks` is absent, restoring imports, browsing, and background sync. Allow a shared fixed source through `CHROME_BOOKMARK_PATH` in `config.json` or `FLOWINONE_CHROME_BOOKMARK_PATH` in the environment.

### Changed

- Unified browsing as 我的收藏, including all four sources by default, with topics, search, type, and sort as the main controls.
- Moved source sync and duplicate review into 設定與維護; kept advanced filters and saved conditions behind secondary controls.
- Kept grid and list layouts, mapping legacy shelf and knowledge links/preferences to them without losing filters.
- Made `/resources/` an add/import page with collapsible processing tools; prioritized content on cards and Resource detail pages, with extraction and versions under 資源管理.

## 2026-09-28

### Changed

- Split Catalog query, browse, source sync, Eagle checkpointing, page routes, JSON APIs, and CLI commands by responsibility.
- Centralized Resource-to-Catalog refreshes and shared request database resolution.
- Use `pyproject.toml` as the dependency source; install development dependencies with `pip install -e '.[dev]'` in the existing `py3.11` Conda environment.

### Removed

- Removed the `/gallery/` and `/search/` compatibility redirects. Use `/navigator/?scope=gallery` or `/navigator/?scope=all`.

## 2026-07-26

### Changed

- Consolidated all browser reads on Navigator and the Catalog projection.
- Kept `/gallery/` and `/search/` as logged compatibility redirects with a 2026-12-31 sunset.

### Removed

- Deleted the duplicate Gallery models, service, JSON API schemas, `/api/gallery/*` routes, and dev-only Gallery Lab.

## 2026-07-23

### Changed

- Replaced the import-time Flask singleton with a `create_app()` application factory.
- Split Local, Chrome, Eagle, and media HTTP adapters into namespaced Blueprints.
- Added strict Pydantic request/query contracts and validated response schemas for JSON APIs.
- Moved continuous thumbnail and Resource processing into a dedicated worker runtime.

### Fixed

- Prevented Werkzeug reloads and multi-process web servers from starting duplicate worker sets.
- Released the thumbnail worker's SQLite runtime lease on clean shutdown so it can restart immediately.

## 2026-07-18

### Changed

- Simplified Flowinone into a multi-source resource renderer; `/` now opens Gallery.
- Kept Gallery, Catalog/Search, Resource extraction, source viewers, sidecars, and explainable related items.

### Removed

- BUILD/THINK/LEARN/SCAN/RECOVER/WRITE, Entry, Project, Decision, and Output Asset runtime.
- Collections, Draft Notes, note export/mirrors, and Resource workflow/promotion routes.
- Migrations `0007_renderer_only_cleanup` and `0008_renderer_resource_schema` remove the corresponding tables, legacy workflow fields, and personal-note FTS data after a user backup.

## 2026-07-11

### Added

- Independent Gallery domain with flat Local/Eagle/Bookmark adapters.
- `/gallery/`, `/api/gallery/items`, and `/api/gallery/sources`.
- URL-driven multi-source checkboxes, item-type filter, search, allowlisted sort, stable random seed, signed cursor, empty/error states, and scroll restoration.
- WRITE Entry mode and `/write/` Output Asset workbench.
- Project/Mode Resource context, project-linked retrieval, Decision Log, Output Asset provenance, and migration `0003_knowledge_os`.
- Architecture, schema, workflow, current-state, migration, and external-spec integration documentation.

### Changed

- Gallery is now a first-level navigation destination instead of relying on source-specific folder pages.
- BUILD can show only explicitly project-linked Resources and human Decisions; it still does not show an unread feed.
- Resource detail can link a Resource to Project/Mode contexts and create WRITE Entries.

### Preserved

- Existing Eagle folders/tags/smart folders/stream, Chrome folder browser, local folder views, viewers, Resource Library, note export, and Entry APIs remain available.
