# Renderer workflows

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

## Global navigation search

1. From a Navigator page, submit the global search to keep the active **素材** or **全部內容** scope.
2. From Resources or any other non-Navigator page, submit the same field to open Navigator with `scope=all`.
3. The non-Navigator default therefore searches Local, Eagle, Bookmarks, and Resources, matching the “搜尋素材與資源” label.

`/gallery/` and `/search/` are compatibility redirects only: they preserve query parameters and map to **素材** (`scope=gallery`) and **全部內容** (`scope=all`) respectively.

## Import and enrich web resources

1. Add a URL or incrementally import Chrome under `/resources/`.
2. The Resource database deduplicates by canonical URL and retains every bookmark placement in `resource_origins`.
3. Keep `python -m src.flowinone.workers` running to fetch metadata, make a thumbnail, and extract text. Use `resources-worker --limit N` only for a bounded one-shot maintenance run. AI summaries are optional.
4. The Resource is synchronized into Catalog, where it can be found alongside all other sources in Navigator's **全部內容** scope.

## Catalog and sidecar maintenance

- `catalog-sync --source all` rebuilds/updates source projections independently.
- `catalog-relations-rebuild` computes bounded, explainable related-item edges from title, tags, and source metadata.
- `sidecars-export`, `sidecars-import`, and `sidecars-audit` keep portable local-media metadata in `.flowinone.json`; absolute paths and caches are excluded.
