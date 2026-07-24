# Changelog

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
- Collections, Draft Notes, Obsidian export/mirrors, and Resource workflow/promotion routes.
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

- Existing Eagle folders/tags/smart folders/stream, Chrome folder browser, local folder views, viewers, Resource Library, Obsidian export, and Entry APIs remain available.
