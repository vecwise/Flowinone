# Renderer migration plan

## Completed: renderer simplification

`0007_renderer_only_cleanup` and `0008_renderer_resource_schema` change Flowinone from a combined Gallery/Knowledge OS application into a resource renderer.

- Homepage → Gallery.
- Navigation → Gallery, Search, Resources, and source-specific pages.
- Runtime Entry/Knowledge OS/Collection/Note/Obsidian code deleted; inactive Resource workflow and personal-note columns removed.
- Workflow APIs and CLI commands deleted.
- Workflow/curation database tables dropped on upgrade.
- Gallery, Catalog, Resource ingestion, source viewers, sidecars, and explainable item relations retained.

Before applying the migration to an existing local DB, make a normal SQLite backup of `data/flowinone.sqlite3`. The migration is intentionally destructive for Entries, Notes, Collections, Projects, Decisions, and Output Assets.

```bash
conda run -n py3.11 flask --app run resources-db-upgrade
```

## Next work, only if it improves rendering

1. Benchmark optional OCR on local media, store versioned artifacts, then include OCR text in Catalog FTS.
2. Benchmark a local face provider for anonymous clusters with manual merge/split/name controls.
3. Improve Eagle incremental sync and show sync progress for large libraries.
4. Tune explainable related-item ranking from local events while retaining reasons and user controls.

## Not planned for this repository

- Knowledge workflow, notes, Obsidian export, project/task management, or Collections.
- Cross-device sync, Raw → Wiki, Obsidian vault indexing, and external knowledge connectors.
- Autoplay, infinite feeds, Graph DB, agent swarm, social features, or a React/FastAPI rewrite.
