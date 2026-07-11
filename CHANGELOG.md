# Changelog

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
