# Current state

Updated: 2026-07-24

This page is the concise product-state snapshot. For the actual runtime,
storage, route, and authority model, use [Flowinone 現況架構](architecture.md);
for setup and daily operation, use the [使用手冊](renderer-architecture.md).

## Product boundary

Flowinone is a multi-source resource renderer. The root route redirects to `/navigator/?scope=gallery`, the Navigator's **素材** scope.

| Domain | Status | Notes |
| --- | --- | --- |
| Navigator | Active | One user-facing surface with **素材** (`scope=gallery`) for flat Local/Eagle/Bookmark browsing and **全部內容** (`scope=all`) for retrieval across those sources plus Resources; source multi-select, filters, deterministic random order, scroll restore, favorites and bounded sessions |
| Catalog | Active | Rebuildable SQLite projection over Local, Eagle, Bookmarks, and Resources; FTS, facets, tags, keyset cursor, origin-aware opening |
| Resources | Active | URL/Chrome import, deduplication, thumbnail/content extraction, optional AI summaries, tags, related Resources |
| Sidecar | Active | Portable local-media metadata import/export/audit and move relinking |
| Item relations | Active | Explainable metadata-based related items; no opaque recommendation feed |
| OCR/People | Foundation only | Artifact/person data interfaces remain optional; no provider is required for normal browsing |

`/gallery/` and `/search/` remain only as compatibility redirects. They preserve incoming query parameters and send users to `/navigator/` with `scope=gallery` (**素材**) and `scope=all` (**全部內容**) respectively.

Navigator uses one page-level, scope-aware search. The navigation search is hidden there to remove duplication; on every non-Navigator page it defaults to `scope=all`.

The web app now enqueues UI-triggered Catalog synchronization as durable leased jobs. The standalone worker processes them and publishes a persisted heartbeat; production HTTP requests never apply Alembic migrations implicitly.

Last audit observed approximately 7,630 canonical Catalog items and 14,800 origins, including roughly 7,128 Resources and 7,193 Chrome bookmarks. Catalog is used because request-time flattening at this size is no longer appropriate.

## Removed in migrations `0007_renderer_only_cleanup` and `0008_renderer_resource_schema`

- BUILD, THINK, LEARN, SCAN, RECOVER, and WRITE Entry modes.
- Entries, Projects, Decisions, Output Assets, and their provenance APIs.
- Manual, Smart, and Generated Collections.
- Draft Notes, personal-note workflow, and Obsidian export/mirror code.
- Resource reading-state, priority, archive, promotion, personal-note storage, and project/mode UI/API flows.

Migration `0008_renderer_resource_schema` removes the inactive Resource workflow and personal-note columns. Renderer routes only expose resource metadata that supports browsing, display, and retrieval.
