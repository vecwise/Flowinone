# Flowinone 架構與 UI 修正計畫

> 稽核日期：2026-07-24；第一階段實作日期：2026-07-24。這份文件保留原始問題與驗收標準，並追蹤實際完成程度。

## 0. 實作狀態

| 範圍 | 狀態 | 本次結果 |
| --- | --- | --- |
| P0 檔案／HTTP／debug | 已完成 | media route 限制於 allowlist roots 且只接受圖片 extension；mutation 改 POST；Host/Origin/CSRF gate；只 bind `127.0.0.1`；debug/dev routes 預設不註冊 |
| P0 migration lifecycle | 已完成 | production request 只檢查 Alembic revision，不自動升級；CLI 先用 SQLite backup API 備份並對來源／備份執行 `integrity_check` |
| P1 bootstrap / paths | 已完成 | import config 不開 GUI、不寫檔；item/cache/resource/content 均從絕對 `FLOWINONE_DATA_DIR` 派生；新增 `flowinone-doctor` |
| P1 durable sync / health | 已完成 | Web 預設 enqueue 既有 leased `processing_jobs` 並回 202；worker 執行 Catalog sync；1 秒 polling；相同 payload 在 active 期間不重複排程；UI 顯示 worker heartbeat |
| P1 Gallery 收旂 | 部分完成 | `/gallery/` 僅 redirect，design lab 改為 dev-only；Gallery compatibility API/service 仍保留，待 caller 監測後刪除 |
| P2 query / UI | 已完成本階段 | Navigator origins 改為一次 batch query；主搜尋與結果前移；進階篩選收合；移除 350 ms auto-submit；收藏補 loading/error/ARIA；375 px 單欄 |
| P2 service 拆分 / facets cache | 待處理 | `catalog/service.py` 仍過大；exact count/facets 仍未 cache；Gallery compatibility read model 仍在 |
| P3 release | 待處理 | dependency lock、wheel package-data、clean-install smoke test 仍需獨立階段 |

## 1. 尖銳結論

Flowinone 的核心方向是成立的：來源仍各自權威、Catalog 是可重建 projection、server-rendered UI 簡單、Resource 抓取已有 SSRF/redirect/大小限制，測試也覆蓋了不少 domain 行為。

初次稽核時，它比較像「功能豐富的單機管理工具」，還不到「可安心安裝、長期維護的 app」：

- 安全邊界有實際洞，不只是理論上的 hardening 欠缺。
- 啟動、設定、migration、worker 與 sync lifecycle 沒有清楚分開。
- Catalog 已成為核心，舊 Gallery 路徑仍平行存在，架構語言與程式責任沒有收斂。
- UI 有一致的元件底子，卻把最重要的內容推到首屏之外，並存在兩套視覺語言與多套名詞。
- 文件很多，但重複說產品邊界，反而缺少「實際拓撲、資料權威、故障時怎麼判斷」的單一真相。

本次已完成前三項的高風險部分與主要 UI 修正；單一 read model、Catalog service 拆分與 reproducible packaging 仍是後續主線。結論仍然是不應重寫 React/FastAPI，而是沿現有 domain 邊界收旂。

## 2. 問題清單與修正

### P0 — 必須先停血

#### P0.1 任意本機檔案讀取

**原始證據**：稽核時 `/serve_image/<path:image_path>` 未驗證 root，`/serve_image/etc/hosts` 可取得 HTTP 200。現已加入 resolved-path containment、extension allowlist 與負向測試；raw path 改成 opaque media ID 仍是後續 hardening。

**影響**：只要服務可被其他 origin/LAN/tunnel 存取，就可能外洩本機檔案。

**修正**：

1. 移除 raw filesystem path route，改用不透明 media ID。
2. 由 repository 將 ID 解成 canonical path；使用 `Path.resolve()` 後，以 `is_relative_to()` 驗證 allowlist root。
3. 拒絕 symlink escape、目錄、未知 extension 與非 allowlist MIME。
4. 預設只 bind `127.0.0.1`，加上負向 security tests。

**驗收**：合法 root 內資產為 200；`/etc/hosts`、`..`、encoded traversal、symlink escape 一律 404/403，測試不得讀到內容。

#### P0.2 GET 副作用與缺少 CSRF/origin 防護

**證據**：`/update_db`、`/update_thumbnails`、`/clear_thumbnails`、`/open_path/` 使用 GET 觸發寫檔、刪除或開啟 Finder；Resource/Catalog 的 HTML form POST 也沒有完整 CSRF/origin gate。

**影響**：惡意網頁、預抓、crawler 或誤點可能觸發 localhost app 的副作用；`clear_thumbnails` 尤其不該是可預抓的 URL。

**修正**：所有 mutation 改 POST/DELETE；加入 CSRF token、`Origin`/`Host` allowlist、SameSite cookie；破壞性操作要明確確認，response 後用 PRG pattern。

**驗收**：GET 永不改狀態；無/錯 token 或錯 origin 回 403；重送頁面不會重複 mutation。

#### P0.3 啟動時自動套用破壞性 migration

**證據**：`ResourceDatabase` 建立時會執行 Alembic upgrade；文件同時警告 `0007`、`0008` 會刪除舊 workflow 資料。

**影響**：單純啟動或 import app 就可能不可逆地改 schema，和「先備份、手動 upgrade」的操作承諾矛盾。

**修正**：runtime 僅檢查 schema revision；不符合就 fail fast 並列出明確指令。migration CLI 先建立帶 timestamp 的 SQLite backup、驗證 `integrity_check`，再 upgrade。

**驗收**：app startup 不執行 DDL；舊 schema 啟動會給可操作錯誤；backup 或 migration 失敗時不啟動 web。

### P1 — 讓 runtime 可預測

#### P1.1 Catalog sync 佔住 HTTP request

**證據**：`POST /api/catalog/sync` 在 Flask request 內完成整批同步；前端每 100 ms poll status；狀態與 lock 只在 process memory。

**影響**：大型 Eagle library 會造成長 request、proxy timeout；多 worker process 看見不同狀態；10 req/s polling 沒有必要。

**修正**：建立持久化 `sync_jobs`，HTTP 只 enqueue 並回 `202 + job_id`；獨立 worker 以 lease 執行；前端 1–2 秒 polling 或 SSE，顯示 source、processed/total、checkpoint、last error、cancel/retry。

**驗收**：request 在 500 ms 內返回；web restart 後仍可查 job；同來源不重複執行；失敗可從 checkpoint 重試。

#### P1.2 設定 import 有 UI/寫檔副作用，資料路徑不一致

**證據**：`config.py` import 時可能開 Tkinter 並寫 `config.json`；`flowinone.sqlite3` 使用 root-resolved settings，但 `item_db.db` 與 `cache.db` 使用 cwd-relative `data/`。

**影響**：測試、CLI、headless 和 package import 不可預測；從錯誤目錄啟動會產生另一套 DB。

**修正**：建立純 `Settings.load()`；startup validation 與 interactive setup 分成獨立 command；所有 data/cache/content path 從同一個絕對 `FLOWINONE_DATA_DIR` 派生。

**驗收**：import 不開 UI、不寫檔；任意 cwd 啟動都解析到同一資料目錄；缺設定給結構化錯誤。

#### P1.3 Catalog 與 Gallery 兩條 read path 並存

**證據**：使用者已只看 Navigator，但 `src/flowinone/gallery/service.py`、Gallery API 與 design lab 仍保留獨立來源 flattening/read model；模板/CSS/命名仍大量使用 Gallery 語言。

**影響**：相同來源邏輯會漂移，開發者不清楚哪一層是權威；測試與維護成本成倍增加。

**修正**：先標記 Gallery API deprecated，確認沒有 caller 後刪除舊 service/API；需要的 design lab 移到明確的 dev-only module；`/gallery/` 僅保留一個有期限的 redirect。

**驗收**：所有 Navigator query 只走 Catalog；repo 不再有第二套來源 flattening；相容 route 有移除日期與 telemetry/log。

#### P1.4 debug 預設與註冊邏輯不安全

**證據**：`FLOWINONE_DEBUG` 缺省值為 `1`，`/debug/` 無條件註冊。

**修正**：debug 預設 false；debug Blueprint 只在 explicit development config 註冊；production-like 啟動拒絕 Flask debugger。

**驗收**：無環境變數時 `/debug/` 為 404，且不啟動 reloader/debugger。

### P2 — 架構可維護性與查詢效能

#### P2.1 Catalog service 是 God object

**證據**：`src/flowinone/catalog/service.py` 約 1,477 行；查詢、facets、sync orchestration、retry、checkpoint、來源 adapter、SQLite error policy 集中在一個模組。

**修正方向**：拆為 `CatalogQueryRepository`、`CatalogWriter`、`SyncCoordinator`、每來源 `SourceReader` adapter 與 `SyncJobRepository`。邊界以 domain DTO/Protocol 表達，不要先引入新 framework。

**驗收**：query 路徑不 import source adapters；source sync contract 可用 fake adapter 測試；單一模組不再同時管 HTTP lifecycle、來源 I/O 與 SQL。

#### P2.2 列表頁存在 N+1 與重複聚合

**證據**：Navigator 先查主列表，再針對結果/來源逐筆 `get_origin`；每次列表也做 exact count 與全域 facets。48 張卡可能追加數十次 SQL query。

**修正**：主查詢一次回傳 selected origins；count/facets 依 filter hash 短期 cache，或只在 filter drawer 展開時取得；用真實資料量建立 query-count 與 p95 budget。

**驗收**：一頁 query count 不隨卡片數線性增加；10 萬 items 的常用 filter p95 有明確門檻並在 benchmark/CI 追蹤。

#### P2.3 三套 SQLite lifecycle

**問題**：主 DB 用 Alembic，item/cache DB 在 runtime 中自行改 schema；backup、diagnostic、locking 與 recovery 策略不一致。

**修正**：短期建立統一 `flowinone doctor`、backup 與 schema version 表；中期決定將 item/cache 合併到主 DB，或明確保留三 DB 但共用 migration abstraction。不要維持無版本的隱式 ALTER。

### P2 — UI / UX 與資訊架構

#### P2.4 第一屏看不到主要工作

**原始實測**：375×812 上 Navigator 結果約在 y=2143，Resources 約在 y=1192。實作後分別提前到約 y=852 與 y=747；兩頁皆無水平 overflow，Navigator 在 375 px 使用單欄卡片。

**影響**：一個內容密集工具卻先展示品牌說明，使用者每次都要滾過相同內容才能工作。

**修正**：Navigator 首屏改為 compact command bar（scope、search、source、sync status）+ results；最近瀏覽收成可橫滑的一列或側欄，空/未命名 sessions 不顯示。Resources 把匯入表單改成單一 primary CTA + drawer/modal，stats 降為次要。

**驗收**：1366×768 與 375×812 進站都看得到第一排結果；關鍵 filter 在 1 次 interaction 內可達；不需滾過 hero。

#### P2.5 兩套視覺語言與命名

**證據**：Navigator/Resources 是 neutral dashboard，`/library/` 卻是 serif/editorial、大留白頁；Local/本機/Local DB、Eagle/EAGLE、Bookmarks/書籤、Resources/網頁資源混用，卡片還顯示 raw source key 與 ISO timestamp。

**修正**：建立一套 content-dense workbench shell、spacing/type/token；來源名稱採「本機、Eagle、書籤、網頁資源」；技術 key 只留在 diagnostic；時間 localized；`/library/` 要嘛併入 Navigator 的 source drawer，要嘛套用同一 shell。

**驗收**：所有一級頁共用 header、字體、spacing、status pattern；UI copy glossary 有測試或 snapshot；使用者不會看到 `local · eagle` 之類內部 key。

#### P2.6 搜尋互動對 IME 與錯誤狀態不友善

**證據**：Navigator 在 input 350 ms 後整頁 submit，未處理 `compositionstart/end`；checkbox/select 一變就 reload。收藏按鈕缺 `aria-pressed`、loading/disabled 與可見錯誤。

**修正**：組字期間禁止 submit；文字搜尋以 Enter 或較長 debounce + request cancellation；filter 先在 drawer 選擇再套用；保留焦點與 scroll。收藏使用 optimistic state，但失敗要 rollback/toast，並補 `aria-pressed`。

**驗收**：注音/拼音輸入不會中途送出；慢速/失敗 request 有 loading/error；鍵盤操作後焦點仍合理。

#### P2.7 行動裝置可用但密度失衡

**實測**：沒有水平 overflow 是優點；但大量文字為 `0.68–0.78rem`，部分真實 control 高度約 26–30 px，低於觸控與可讀性目標。兩欄卡片在 375 px 只剩約 163.5 px 寬。

**修正**：mobile body/meta 最小字級與 line-height token；互動 target 至少 44×44；窄螢幕提供 single-column/list view 或使用者 density toggle；尊重現有 reduced-motion 與 focus ring。

**驗收**：axe + keyboard + 375 px 實機檢查；主要 controls 44×44；文字不需 zoom；長標題不遮住動作。

#### P2.8 worker 與 sync 狀態不可判讀

**證據**：Resources 只顯示 pending 數，沒有 worker heartbeat；sync UI 依賴高頻 polling，失敗/進度資訊不足。

**修正**：統一 Operational status component，顯示 worker online/offline、last heartbeat、queue、current job、last error、retry；來源總覽與 Navigator 共用。

### P3 — 工程衛生、文件與發佈

- `requirements.txt` 與 `pyproject.toml` 重複且未鎖版，還含 placeholder GitHub URL；先選一個 dependency source，產生 reproducible lock/constraints。
- package-data 宣告與 repo root 的 `templates/`、`static/` 位置不一致；本次實際建立 wheel 後，wheel 內沒有任何 `templates/` 或 `static/`。應修正 layout/打包設定，並建立安裝後可 render 頁面的 wheel smoke test。
- `.flowinone_local` 內仍有重要 provider/tests 但被 gitignore；正式功能與測試必須搬回 tracked source，或明確刪除。
- 多份 docs 重複產品邊界。以 `architecture.md`、`renderer-architecture.md`、本文件分別作為「現況架構、使用手冊、改造計畫」，其餘文件只描述專題並鏈回權威文件。
- 稽核 baseline 為 `81 passed, 1 skipped`；本次新增 security、migration lifecycle、durable sync、worker heartbeat 與 origin query-count regression tests後，最後驗證為 `90 passed, 1 skipped`，另有 5 個 browser JavaScript tests。Packaging 與自動 axe 仍待補齊。

## 3. 執行計畫

| 階段 | 估計 | 內容 | 完成定義 |
| --- | ---: | --- | --- |
| 0. Safety hotfix | 1–2 天 | 封鎖任意檔案讀取；mutation 改 POST + CSRF/origin；debug 預設關閉 | security regression tests 全過，只 bind loopback |
| 1. Safe bootstrap | 2–4 天 | migration 與 startup 分離；統一路徑；純設定 loader；backup/doctor command | import 無副作用，任意 cwd 指向同一 data dir |
| 2. Durable operations | 3–5 天 | sync job 持久化、worker lease/checkpoint、worker heartbeat/status UI | web restart 後工作可追蹤、重試，不再長 request |
| 3. Architecture convergence | 5–8 天 | 拆 Catalog service；移除第二套 Gallery read path；query batching/cache | 單一 read model，列表無 N+1，有 query/perf budget |
| 4. UI information architecture | 4–6 天 | compact workbench、首屏結果、統一名詞/視覺、IME/a11y/loading | 桌機與手機首屏可工作，鍵盤/IME/axe 驗收 |
| 5. Reproducible release | 3–5 天 | dependency lock、wheel assets、tracked tests、CI smoke/security/perf | clean checkout 可安裝、migration、啟動與 render |

### 實作原則

1. 階段 0 完成前不要加新來源或 OCR/face 等功能。
2. 每個階段先補失敗測試，再做最小修正；不混入技術棧重寫。
3. schema 或資料路徑變更都要有 backup、rollback 與 `doctor` 驗證。
4. Catalog 保持 projection；來源 metadata 更新仍由來源 API/adapter 負責。
5. UI 改造先修資訊階層與狀態，再處理裝飾與動畫。

## 4. 已完成且應保留的方向

- 首頁、Gallery、Search 已收斂到 Navigator scope；舊 URL 以 redirect 相容。
- 原來源仍是 authority，Catalog 可重建且保留 origins。
- Resource HTTP fetch 已有 public network validation、redirect revalidation、byte/redirect cap。
- API contract 已使用 Pydantic 驗證，Local/Eagle/Bookmark/Resource 的核心同步與 retry 有測試。
- UI 已有 skip link、可見 focus、form label、lazy image dimensions、reduced-motion，mobile 沒有水平 overflow。

這些是應保留的基礎，不應在改造中被重寫掉。
