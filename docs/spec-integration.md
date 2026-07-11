# 外部規格整合與衝突裁決

本文件對照：

- `from網頁flowinone_gallery_browsing_spec.md`
- `from網頁flowinone_knowledge_os_codex_spec.md`
- 修改前的 Flowinone 程式與 README

## Gallery：新規格與既有實作

| 主題 | 新 Gallery 規格 | 修改前 Flowinone | 採用決策 |
|---|---|---|---|
| Domain 邊界 | Gallery 不含筆記／閱讀狀態 | 首頁卡片混合 media、Resource、工作入口 | 新規格優先；Gallery 獨立 package、page、API |
| 來源切換 | 同一 Query State 的 source filter | Local、Eagle、Chrome 各自路由 | 新規格加上使用者要求：同頁 checkbox 複選 |
| Item 形態 | image/video item | folder 與 item 混合 | 使用者要求優先：Gallery 永不輸出 folder item |
| Bookmarks | 原規格主要描述 image/video | 既有 Chrome bookmark wall | 使用者要求優先：bookmark 是第三種扁平 item，不帶 knowledge state |
| Search/filter/sort | URL 是唯一真實來源 | 多個互不相通頁面 | 新規格優先 |
| 技術棧 | 建議 FastAPI + React | Flask + Jinja + SQLAlchemy | 既有實作優先；先在同一 Flask app 建立清楚 domain，避免雙棧重寫 |
| 儲存 | 建議 canonical Gallery schema | Local DB、Eagle、Chrome 各自 authoritative | 目前保留 source adapter；資料量與事件需求出現後再 materialize |
| 舊 folder UI | 非主要 IA | 已有完整工具 | 保留但降為相容／維護入口，不刪功能 |

### Gallery 最終判斷

新 md 在產品邊界、Query State、扁平 item 與 browse loop 上較好；既有程式在來源整合、媒體播放與目前 Flask 部署上較成熟。最佳整合不是二選一，而是「新 md 定義產品 contract，既有 adapter 與 viewer 繼續提供來源能力」。

## Knowledge OS：新規格與既有實作

| 新規格概念 | 既有對應 | 差距 | 本次處理 |
|---|---|---|---|
| Layer 0 Raw Resource | `resources`、origins、contents | 已存在 | 保留 |
| Layer 1 Brief Card | summary fields、structured JSON、AIArtifact | 未獨立命名，但資料已存在 | 不建立重複 `resource_briefs`；以欄位 mapping 文件化 |
| Layer 2 Wiki Note | `draft_notes`、sources、exports | 已存在 | 保留衝突安全 Obsidian export |
| Layer 3 Output Asset | 無 | 缺失 | 新增 `output_assets`、`asset_sources` 與 WRITE UI/API |
| Project-linked retrieval | Project 與 Resource 分離 | 缺 Resource↔Project/Mode | 新增 `resource_projects`、`resource_modes` 與 retrieval API |
| Human decision | `user_note` 有人工內容，無 Decision Log | 缺 Project-level decision | 新增 `decisions` |
| Modes | 五個 mode | 缺 WRITE | 新增 WRITE constraint、route、UI |
| AI 不覆蓋人工判斷 | `user_note` 與 user tags 已分離 | 需要明確 contract | 保留並文件化；Decision 只由明確 user/API write 修改 |
| Hybrid search | FTS5 + explainable similar | 無 embedding | 先保留現有可靠 FTS；embedding 延後 |

### Knowledge OS 最終判斷

新 md 的四層模型、情境 retrieval、WRITE 與 Decision 較完整；既有 schema 對 Raw/Brief/Wiki 的實作已經更成熟。建立第二套 Resource/Brief/Note 表會造成雙真實來源，因此保留既有表，只有缺失的 context/decision/output 層透過 migration 增補。

## 兩個 Domain 的合法交會點

Gallery item 不會自動成為 Resource 或 Note。合法交會只能是明確的 typed reference／user action：

1. 在媒體詳細頁建立 Entry。
2. 把 Eagle/local item 加入 Inspiration Collection。
3. 從 Collection 建立 Wiki Draft。
4. 從 Resource、Note、Decision、Entry 建立有 provenance 的 Output Asset。

禁止把 Gallery 的 description/tags 當成使用者筆記，也禁止把 Resource 的閱讀狀態加入 Gallery filter。

## 本次刻意不做

- FastAPI/React 平行重寫
- Graph DB 或 Agent swarm
- embedding、CLIP 與完整推薦模型
- favorite/event/session schema
- Notion 雙向同步
- 把全部 Local/Eagle item 複製成 Knowledge Resource

這些不是遺漏，而是依兩份規格的分階段原則，避免在可靠 browse loop 與 knowledge loop 之前增加第二套基礎設施。
