# Flowinone architecture

## Product boundary

```text
Flowinone
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

兩者共享 Flask shell、navigation、design tokens 與 typed reference，但不共享核心 item table，也不互相借用 workflow state。

## Gallery request flow

```text
/gallery/?q=&source=&type=&sort=&seed=&cursor=
  → GalleryQuery allowlist validation
  → selected source adapters
  → flat GalleryItem normalization
  → combined search/filter/sort
  → signed cursor page
  → Jinja page or JSON response
```

Source adapter failure 採部分成功：Eagle 關閉時 Local/Bookmarks 仍可回應。API 不回傳本機 absolute path；folder path 只能成為 bookmark description/tag 或 local relative path 的 server-side detail reference。

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

## Why Flask remains

外部 Gallery spec 推薦 FastAPI/React，但目前 app、viewer、source integration、tests 與部署都在 Flask/Jinja。新的 Gallery package 已建立 API/domain boundary，未來若獨立前端，只需改 consumer，不必先做高風險雙棧 migration。
