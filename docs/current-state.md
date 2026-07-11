# Flowinone current state

更新日期：2026-07-11

## 結論

Flowinone 現在有兩個明確的一級產品面：

1. `Gallery`：只負責本機、Eagle、書籤的扁平 item 瀏覽。
2. `Knowledge OS`：負責 Resource、Brief、Wiki Draft、Entry、Project、Decision 與 Output Asset 的知識／工作閉環。

修改前兩者已有不同資料庫與 service，但 UI 入口與舊路由仍交疊：首頁曾同時推薦媒體、Resource 與工作行動；本機、Eagle、Chrome 主要以資料夾樹進入；Gallery 搜尋只查 Eagle。現在根路由維持 `BUILD`，`GALLERY` 成為獨立一級入口，舊資料夾頁只保留為相容／維護工具。

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

## 主要 HTML 路由

| 路由 | 用途 |
|---|---|
| `/gallery/` | Gallery 單頁瀏覽；Local、Eagle、Bookmarks 可複選 |
| `/build/` | 工作恢復與目前 Project，不顯示未讀 feed |
| `/think/`、`/learn/`、`/scan/`、`/recover/` | 情境型 Entry |
| `/write/` | 建立 WRITE Entry 與可追溯 Output Asset |
| `/resources/` | Raw Resource / Brief 工作流 |
| `/notes/` | Wiki Draft 與 Obsidian export |
| `/projects/`、`/entries/` | 長期 Project 與可恢復入口 |

## 主要 API

- `GET /api/gallery/items`、`GET /api/gallery/sources`
- Resource CRUD、tags、enrichment、promote、similar、context API
- Entry / Project CRUD 與 enter API
- `GET /api/retrieval`
- `GET/POST/PATCH /api/output-assets`
- `GET/POST /api/projects/:id/decisions`

## 仍保留的舊功能

舊的 Eagle folder/tag/smart-folder、Chrome folder tree、本機 folder view、grid/slide/both、Eagle stream 與 item DB maintenance 路由沒有刪除。它們屬於 source-specific 或維護介面，不再定義 Gallery 的主要資訊架構。

## 已知限制

- Gallery 目前是 read adapter aggregation：本機最多載入 10,000 個候選、Eagle 每次最多 500 個候選，再做跨來源排序；下一階段才需要 materialized canonical Gallery index。
- Gallery 尚未實作 favorite/event/session/embedding/hover preview；這些是 Gallery Phase 2–4，不應放進 Knowledge OS schema。
- Knowledge retrieval 現在以 Project/Mode metadata、人工 relevance、文字條件與 priority 為主；尚未加入 embedding。
- Notion 仍是定位與未來 connector，沒有雙向同步。

## 驗證基準

`conda run -n py3.11 pytest -q`：62 passed、1 skipped（既有 online smoke test）。
