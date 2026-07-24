# Active schema map

## Source-owned data

| Source | Authority | Renderer use |
| --- | --- | --- |
| Local | Filesystem + `item_db` index | Media cards, paths resolved server-side, sidecar metadata |
| Eagle | Eagle library/API | Media cards and Eagle viewers |
| Chrome | Chrome Bookmarks JSON | Bookmark cards and original URLs |
| Resources | `flowinone.sqlite3` + content files | Imported URL metadata, extracted text, tags, thumbnails |

## Resource tables

- `resources`, `resource_origins`, `resource_contents`
- `tags`, `resource_tags`
- `ai_artifacts`, `processing_jobs`, `resource_fts`

`resources` stores URL identity, source metadata, availability, enrichment output, timestamps, and preview/content references. The running Resource API only permits title and availability edits; reading state, priority, personal-note, promotion, and project/mode fields are not part of the product.

## Catalog projection

```text
catalog_items
  ├── catalog_origins       one canonical item → one or more source records
  ├── catalog_item_tags → catalog_tags
  ├── catalog_fts           title, description, tags, Resource extracted text
  ├── item_user_state       favorite, hidden, open count, last viewed
  ├── catalog_events        open, view, favorite, hide
  ├── browse_sessions       bounded continue-browsing state
  ├── item_relations        explainable related-item edges
  ├── catalog_artifacts     optional OCR/other versioned artifacts
  └── item_people → people  optional anonymous/manual person clusters
```

`catalog_sync_state` records freshness/errors per source. Eagle additionally keeps a
versioned, batch-committed cursor so an interrupted large-library scan can resume
without treating unseen pages as deletions. A failed source leaves its previous
projection intact and does not suppress results from healthy sources.

## Migration history

Migrations `0001`–`0006` are preserved so both empty and existing databases can upgrade through their historical schema. `0007_renderer_only_cleanup` drops workflow/curation tables and obsolete queued export jobs; `0008_renderer_resource_schema` removes inactive Resource workflow/personal-note columns and rebuilds Resource FTS without personal-note text. Both are intentionally irreversible because they delete feature-owned records.
