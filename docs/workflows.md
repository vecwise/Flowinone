# Renderer workflows

This file is a compact domain-flow reference. For setup, commands, data
freshness, and troubleshooting, use the [Flowinone 使用手冊](renderer-architecture.md).

## Navigator: 素材

1. Open `/navigator/` or `/navigator/?scope=gallery`; **素材** is the default scope.
2. Select Local, Eagle, and/or Bookmarks.
3. Apply search text, type, tag ANY/ALL, date/duration, favorite/unviewed, and sort filters.
4. Navigator queries Catalog with a query-bound keyset cursor.
5. Open a card through the selected origin; return to the same query URL and restored scroll position.

Folders are never items in the **素材** scope. They may appear as source metadata or be opened through a source-specific page.

## Navigator: 全部內容

1. Open `/navigator/?scope=all` or switch the Navigator scope to **全部內容**.
2. Catalog FTS searches title, description, tags, and Resource extracted text.
3. Facets narrow by source, type, and tag; same-URL Records merge only at the canonical item level.
4. The app chooses an available requested origin. Bookmark origins use their original URL; Local/Eagle use controlled server routes; Resource uses Resource detail.

## Search entry points

1. From a Navigator page, use its single page search to keep the active **素材** or **全部內容** scope.
2. From Resources or any other non-Navigator page, use the navigation search to open Navigator with `scope=all`.
3. The non-Navigator default therefore searches Local, Eagle, Bookmarks, and Resources, matching the “搜尋素材與資源” label.

`/gallery/` and `/search/` are temporary compatibility redirects only: they preserve query parameters, map to **素材** (`scope=gallery`) and **全部內容** (`scope=all`) respectively, log usage, and sunset after 2026-12-31. There is no separate Gallery API or read model.

## Import and enrich web resources

1. Add a URL or incrementally import Chrome under `/resources/`.
2. The Resource database deduplicates by canonical URL and retains every bookmark placement in `resource_origins`.
3. Keep `python -m src.flowinone.workers` running to fetch metadata, make a thumbnail, and extract text. Use `resources-worker --limit N` only for a bounded one-shot maintenance run. AI summaries are optional.
4. The Resource is synchronized into Catalog, where it can be found alongside all other sources in Navigator's **全部內容** scope.

## Catalog and sidecar maintenance

- `catalog-sync --source all` rebuilds/updates source projections independently.
- Navigator's source manager enqueues the same operation as a durable job; its optional source watcher first records a baseline, then debounces changes before queueing work. `python -m src.flowinone.workers` must be running to process it.
- `catalog-similarity-rebuild` reads Local image files to create Flowinone-owned SHA-256/dHash artifacts. Exact file matches rank before visual matches; it never writes the source file or source metadata.
- `catalog-relations-rebuild` computes bounded, explainable related-item edges from title, tags, and source metadata.
- `sidecars-export`, `sidecars-import`, and `sidecars-audit` keep portable local-media metadata in `.flowinone.json`; absolute paths and caches are excluded.

## Resource content versions

1. A Resource's forced re-enrichment writes a new text snapshot only when the extracted content hash changes.
2. Open **查看差異** from the Resource detail page and select an earlier and later snapshot. The renderer exposes added/removed line counts and a bounded unified diff.
3. Snapshots remain Flowinone-owned files under `data/content/`; the remote URL remains the authority and can be re-fetched at any time.
