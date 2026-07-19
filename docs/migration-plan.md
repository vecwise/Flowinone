# Renderer migration plan

## Completed: renderer simplification

`0007_renderer_only_cleanup` and `0008_renderer_resource_schema` change Flowinone from a combined Gallery/Knowledge OS application into a resource renderer.

- Homepage → Navigator's **素材** scope (`/navigator/?scope=gallery`).
- Navigation → Navigator, Resources, and source-specific pages.
- Runtime Entry/Knowledge OS/Collection/Note/Obsidian code deleted; inactive Resource workflow and personal-note columns removed.
- Workflow APIs and CLI commands deleted.
- Workflow/curation database tables dropped on upgrade.
- Navigator, Catalog, Resource ingestion, source viewers, sidecars, and explainable item relations retained.

Before applying the migration to an existing local DB, make a normal SQLite backup of `data/flowinone.sqlite3`. The migration is intentionally destructive for Entries, Notes, Collections, Projects, Decisions, and Output Assets.

```bash
conda run -n py3.11 flask --app run resources-db-upgrade
```

## Completed: Gallery and Search merged into Navigator

- `/navigator/` is the single user-facing browse and retrieval surface.
- **素材** (`scope=gallery`) keeps the former flat Local/Eagle/Bookmark browsing behavior.
- **全部內容** (`scope=all`) keeps cross-source retrieval over Local, Eagle, Bookmarks, and Resources.
- Global navigation search preserves the active scope inside Navigator and defaults to **全部內容** on non-Navigator pages, so Resources are not silently excluded.
- Route/template tests cover both Navigator scope preservation and the non-Navigator `scope=all` default.
- `/gallery/` and `/search/` remain only as compatibility redirects to those scopes and preserve incoming query parameters.
- This consolidation changes navigation and URLs only. It does not add sources, workflows, or any other product scope.

## Next work, only if it improves rendering

1. Benchmark optional OCR on local media, store versioned artifacts, then include OCR text in Catalog FTS.
2. Benchmark a local face provider for anonymous clusters with manual merge/split/name controls.
3. Improve Eagle incremental sync and show sync progress for large libraries.
4. Tune explainable related-item ranking from local events while retaining reasons and user controls.

## Not planned for this repository

- Knowledge workflow, notes, Obsidian export, project/task management, or Collections.
- Cross-device sync, Raw → Wiki, Obsidian vault indexing, and external knowledge connectors.
- Autoplay, infinite feeds, Graph DB, agent swarm, social features, or a React/FastAPI rewrite.
