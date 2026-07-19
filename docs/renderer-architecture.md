# How to use Flowinone

## Daily use

1. Start at `/navigator/`, whose default **素材** scope (`scope=gallery`) visually browses Local, Eagle, and Chrome Bookmark items. Select one or several sources, then filter by text, type, tag, sort, or a fixed random seed.
2. Open a card. Local and Eagle media use the built-in viewer; bookmarks open their original URL. The selected origin is respected even if several origins share one Catalog item.
3. Switch the same Navigator to **全部內容** (`/navigator/?scope=all`) when the source is unknown or the target may be a web Resource. This scope uses the cross-source Catalog and supports source/type/tag facets, tag ANY/ALL, date/duration filters, favorites, and stable keyset pagination.
4. Use the global navigation search from any non-Navigator page to search **全部內容** by default. When used on Navigator, it preserves the current **素材** or **全部內容** scope.
5. Use `/resources/` for imported URLs and Chrome bookmarks that need extracted article/PDF/transcript text. A Resource detail page shows source placements, metadata, summaries, extracted text, tags, related Resources, and enrichment status.
6. Add or correct user tags when they improve future retrieval. Run source or Catalog sync after a large change in local files, Eagle, or bookmarks.

`/gallery/` and `/search/` are compatibility redirects, not separate destinations. Existing links keep their query parameters and arrive at the Navigator's **素材** and **全部內容** scopes respectively.

## Operational commands

```bash
conda run -n py3.11 flask --app run resources-db-upgrade
conda run -n py3.11 flask --app run catalog-sync --source all
conda run -n py3.11 flask --app run resources-worker --limit 20
conda run -n py3.11 flask --app run catalog-relations-rebuild
```

`resources-worker` handles local metadata, thumbnail, content extraction, and optional AI summary jobs. It does not export notes or write to Obsidian.

## What Flowinone deliberately does not do

- It does not turn Navigator items into an inspiration board or a task system.
- It does not maintain a knowledge workflow, projects, entries, notes, or Obsidian exports.
- It does not sync across devices, index an Obsidian vault, or connect OneTab, Keep, Notion, or social platforms.
- It does not autoplay or create an infinite recommendation feed.
