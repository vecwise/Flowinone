# Flowinone architecture

## Product boundary

```text
Flowinone
├── Catalog Projection (rebuildable)
│   ├── canonical items + multi-source origins
│   ├── FTS / facets / keyset query
│   └── relations / events / browse sessions
├── Gallery Domain
│   ├── Local media adapter
│   ├── Eagle adapter
│   ├── Bookmark adapter
│   └── Flat browse query + cards + existing viewers
└── Knowledge OS Domain
    ├── Raw Resource
    ├── Brief fields / AI artifacts
    ├── Wiki Draft / Obsidian export
    ├── Entry + Project + Decision
    └── Output Asset + provenance
```

Gallery 與 Knowledge OS 不共享 workflow state；兩者可讀同一個可重建 Catalog projection。Catalog 不是來源資料庫：Eagle、filesystem、Chrome 與 Resource DB 仍各自 authoritative。

## Gallery request flow

```text
/gallery/?q=&source=&type=&tags=&tag_mode=&sort=&seed=&cursor=
  → GalleryQuery allowlist validation
  → Catalog FTS / filter / facet
  → SQLite keyset cursor page
  → Jinja page or JSON response
```

Catalog sync 採部分成功：Eagle 關閉時 Local/Bookmarks/Resources 仍可更新。API 不回傳本機 absolute path；detail 只使用受控 URI。Gallery event、favorite 與 Browse Session 只屬於 Gallery，不污染 Resource 閱讀狀態。

## Knowledge loop

```text
Resource (raw)
  → extracted content + Brief
  → Project / Mode relevance
  → BUILD / LEARN / SCAN retrieval
  → DraftNote (Wiki draft)
  → WRITE Entry
  → OutputAsset
      └── AssetSource provenance
```

Decision 是 Project 的人工資料。AI 可以產生建議，但沒有自動覆寫 Decision、`user_note` 或 user tags 的 service 路徑。

## Ownership rules

- Eagle：Eagle library 為 authoritative。
- Local media：filesystem 為 authoritative；`item_db.db` 是索引。
- Chrome bookmarks：Chrome JSON 為 authoritative；Resource import 是 durable snapshot/workflow。
- Resource metadata、Entry、Project、Decision、Output Asset：`flowinone.sqlite3` 為 authoritative。
- Wiki：Draft 在 SQLite；匯出後 Obsidian Markdown 是人工長期資產，重匯出有衝突保護。
- Catalog：`flowinone.sqlite3` 內的衍生查詢投影，可從所有來源重建。
- Local sidecar：`.flowinone.json` 保存可攜、人工 metadata；DB 保存查詢索引與 cache。

## Why Flask remains

外部 Gallery spec 推薦 FastAPI/React，但目前 app、viewer、source integration、tests 與部署都在 Flask/Jinja。新的 Gallery package 已建立 API/domain boundary，未來若獨立前端，只需改 consumer，不必先做高風險雙棧 migration。
