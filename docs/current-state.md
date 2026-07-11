# Flowinone current state

更新日期：2026-07-11（Catalog/Collection/Workflow integration）

## 結論

Flowinone 現在有兩個明確的一級產品面：

1. `Gallery`：只負責本機、Eagle、書籤的扁平 item 瀏覽。
2. `Knowledge OS`：負責 Resource、Brief、Wiki Draft、Entry、Project、Decision 與 Output Asset 的知識／工作閉環。

根路由維持 `BUILD`，`GALLERY` 是獨立入口；`SEARCH` 使用跨來源 Catalog。Catalog 已首次同步約 7,630 個 canonical items，並保留約 14,800 個 Local/Eagle/Bookmark/Resource origins。相同 canonical URL 的 Bookmark 與 Resource 合併顯示但不丟來源。

## 現有模組

| Domain | 主要程式 | 資料擁有權 | 狀態 |
|---|---|---|---|
| Gallery | `src/flowinone/gallery/` | 聚合既有來源，不擁有筆記資料 | 已有單頁來源篩選、搜尋、排序、cursor 與 API |
| Local media | `src/file_handler/item_db.py` | `data/item_db.db` 與原始檔案 | Gallery adapter 只取 image/video，不取 folder |
| Eagle | `src/eagle_api/`、`src/file_handler/eagle_integration.py` | Eagle 為 authoritative source | Gallery adapter 以 API 讀取扁平 item |
| Bookmarks | `src/file_handler/chrome_bookmarks.py` | Chrome Bookmarks JSON | Gallery adapter 遞迴展平，只輸出 bookmark item |
| Resource Library | `src/flowinone/resource_library/` | `data/flowinone.sqlite3` | Raw Resource、Brief 欄位、FTS、工作流、AI artifacts |
| Wiki Draft | Resource Library curation / Obsidian exporter | SQLite + Obsidian Markdown | 已支援來源追溯與衝突保護 |
| Entry / Project | `src/flowinone/entry_system/` | 同一 durable SQLite | BUILD/THINK/LEARN/SCAN/RECOVER/WRITE |
| Knowledge OS | `src/flowinone/knowledge_os/` | Context links、Decision、Output Asset | 已補上 Project/Mode retrieval 與 Layer 3 |
| Catalog | `src/flowinone/catalog/` | 可重建跨來源投影 | FTS、facets、keyset cursor、events、sessions、relations |
| Sidecar | `src/file_handler/sidecars.py` | 本機人工 metadata | dry-run import/export/audit、portable fingerprint |

## 主要 HTML 路由

| 路由 | 用途 |
|---|---|
| `/gallery/` | Gallery 單頁瀏覽；Local、Eagle、Bookmarks 可複選 |
| `/search/` | Local、Eagle、Bookmarks、Resources 統一搜尋 |
| `/build/` | 工作恢復與目前 Project，不顯示未讀 feed |
| `/think/`、`/learn/`、`/scan/`、`/recover/` | 情境型 Entry |
| `/write/` | 建立 WRITE Entry 與可追溯 Output Asset |
| `/resources/` | Raw Resource / Brief 工作流 |
| `/notes/` | Wiki Draft 與 Obsidian export |
| `/projects/`、`/entries/` | 長期 Project 與可恢復入口 |

## 主要 API

- `GET /api/gallery/items`、`GET /api/gallery/sources`
- `GET /api/catalog/items`、facets、item detail、related、events、sessions、sync
- Collection CRUD、Smart/Generated Collection、similar Collection
- `POST /api/entries/:id/transition` 與 typed multi-source `/api/entries/:id/links`
- Resource CRUD、tags、enrichment、promote、similar、context API
- Entry / Project CRUD 與 enter API
- `GET /api/retrieval`
- `GET/POST/PATCH /api/output-assets`
- `GET/POST /api/projects/:id/decisions`

## 仍保留的舊功能

舊的 Eagle folder/tag/smart-folder、Chrome folder tree、本機 folder view、grid/slide/both、Eagle stream 與 item DB maintenance 路由沒有刪除。它們屬於 source-specific 或維護介面，不再定義 Gallery 的主要資訊架構。

## 已知限制與刻意邊界

- Eagle sync 目前一次取 500 個候選；Catalog 其餘來源不再於 request time 全量展平。
- Gallery 已有 favorite/event/session 與 explainable metadata relations；hover preview、perceptual duplicate 與模型型 visual embedding 尚未做。
- Knowledge retrieval 現在以 Project/Mode metadata、人工 relevance、文字條件與 priority 為主；尚未加入 embedding。
- OCR/person schema 與 provider interface 已建立；實際 OCR 套件為可選 extra，人物第一版只允許匿名 cluster 與人工命名。
- 不做跨裝置同步、Raw→Wiki、Notion/Keep/社群 connector 或 Obsidian 雙向同步。

## 驗證基準

`conda run -n py3.11 pytest -q`：68 passed、1 skipped（包含 100k synthetic Catalog contract；online smoke test 預設跳過）。
