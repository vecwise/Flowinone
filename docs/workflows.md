# Renderer workflows

## Browse Gallery

1. Open `/gallery/`.
2. Select Local, Eagle, and/or Bookmarks.
3. Apply search text, type, tag ANY/ALL, date/duration, favorite/unviewed, sort, and view mode.
4. The Gallery service queries Catalog with a signed query-bound keyset cursor.
5. Open a card through the selected origin; return to the same query URL and restored scroll position.

Folders are never Gallery items. They may appear as source metadata or be opened through a source-specific page.

## Cross-source search

1. Open `/search/` or use the global search field.
2. Catalog FTS searches title, description, tags, and Resource extracted text.
3. Facets narrow by source, type, and tag; same-URL Records merge only at the canonical item level.
4. The app chooses an available requested origin. Bookmark origins use their original URL; Local/Eagle use controlled server routes; Resource uses Resource detail.

## Import and enrich web resources

1. Add a URL or incrementally import Chrome under `/resources/`.
2. The Resource database deduplicates by canonical URL and retains every bookmark placement in `resource_origins`.
3. Run `resources-worker` to fetch metadata, make a thumbnail, and extract text. AI summaries are optional.
4. The Resource is synchronized into Catalog, where it can be found alongside all other sources.

## Catalog and sidecar maintenance

- `catalog-sync --source all` rebuilds/updates source projections independently.
- `catalog-relations-rebuild` computes bounded, explainable related-item edges from title, tags, and source metadata.
- `sidecars-export`, `sidecars-import`, and `sidecars-audit` keep portable local-media metadata in `.flowinone.json`; absolute paths and caches are excluded.
