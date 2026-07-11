# Flowinone schema map

## Gallery

目前 Gallery 不建立新的 durable schema，而是標準化三個來源的 read model：

```text
GalleryItem
  id              namespaced source id
  source          local | eagle | bookmarks
  media_type      image | video | bookmark
  title
  thumbnail_url
  tags[]
  ext
  created_at
  description
  original_url
  is_available
```

`folder` 不是合法的 Gallery media type。Local/Eagle item detail 以 server-side source/id 或 relative path 解析，JSON 不暴露 absolute path。

## Knowledge OS four-layer mapping

| Layer | Durable tables / fields |
|---|---|
| 0 Raw Resource | `resources`、`resource_origins`、`resource_contents`、`processing_jobs` |
| 1 Brief Card | `resources.summary_*`、`why_this_matters`、`ai_artifacts`、`resource_tags` |
| 2 Wiki Note | `draft_notes`、`draft_note_sources`、`resource_note_links`、`note_exports` |
| 3 Output Asset | `output_assets`、`asset_sources` |

## Migration 0003

`0003_knowledge_os`：

- 擴充 `entries.mode` check constraint，加入 `write`。
- 新增 `resource_projects(resource_id, project_id, relevance, source)`。
- 新增 `resource_modes(resource_id, mode, relevance, source)`。
- 新增 `decisions`，保存 project-level human judgement。
- 新增 `output_assets` 與 polymorphic `asset_sources` provenance。

## Brief mapping rationale

不另建 `resource_briefs`，因為既有 `resources` 已有 one-line/short/structured summary、why matters、language、enrichment status，`ai_artifacts` 也保存 provider/model/prompt version。重複一張 Brief 表會產生同步與 ownership 問題。

對應如下：

| Knowledge spec | Existing field |
|---|---|
| one_line_summary | `summary_one_line` |
| why_saved | `saved_reason` |
| key_points / possible uses | `summary_structured_json` |
| AI provenance/confidence | `ai_artifacts` content/provider/model |
| personal judgement | `user_note`（人工） |
| project/mode relevance | `resource_projects` / `resource_modes` |

## Migrations 0004–0006

`0004_catalog_workflows` 新增：

- Catalog：`catalog_items`、`catalog_origins`、`catalog_tags`、`catalog_item_tags`、`catalog_fts`、`catalog_sync_state`。
- Workflow：`entry_links`，並回填既有 `source_entry_id`。
- Discovery：`item_relations`、`collection_relations`、`item_user_state`、`catalog_events`、`browse_sessions`。
- Collection：`membership_mode`、lifecycle、query/generation JSON、Catalog item reference 與 relation reason。
- Vision foundation：`catalog_artifacts`、`people`、`item_people`。

`0005_catalog_performance` 加入 source/item、title、captured time 與 view-signal 複合索引。實際 7k+ Bookmark Gallery 查詢由約 5.8 秒降至約 65 ms。

`0006_typed_entry_links` 把最初只能 Entry→Entry 的關聯升級為 `entry_id + linked_type + linked_id + direction`。同一 Entry 因而可以有多個 Entry、Resource、Collection、Gallery item、Note 或 Output Asset source/output；舊 `source_entry_id` 與 transition provenance 均保留。

Catalog identity：URL 使用 canonical URL hash；Eagle 使用 item ID；Local 優先使用 sidecar fingerprint／portable UID。Catalog 是 projection，不取代 `resources` 或 `item_db.db`。
