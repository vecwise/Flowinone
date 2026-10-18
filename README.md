# Flowinone — 快速接手指南

Flowinone 把 **本機圖片／影片、Eagle、Chrome 書籤、Flowinone 匯入的網址 Resources** 放到同一個介面瀏覽與搜尋。技術主體是 **Python + Flask + Jinja HTML template + SQLite**，互動功能用少量 JavaScript 呼叫 API。完整三張圖見 [現行架構](docs/architecture.md)。

這份 README 的目標：讓你快速知道 **有什麼能用、如何驗證、結果由哪段程式造成、要改哪裡**。完成第一輪後，你應該能自己追完一個「操作 → route → 資料 → 畫面」的流程。

- 第一次讀：本頁 → [現行架構](docs/architecture.md) → [動手接手練習](docs/hands-on.md)。先認識四個來源，再看資料流和 Web／Worker，最後用一個操作驗證。
- 要安裝、日常使用或排錯：查 [使用手冊](docs/renderer-architecture.md)；要驗證程式行為：看本頁 [測試](#測試) 與 [從結果追原因](#從結果追原因)。

## 四個資料來源，一份 Catalog

| Catalog 來源 | 原始資料在哪裡 | 如何進入統一搜尋 |
| --- | --- | --- |
| Local | 設定的本機圖片／影片資料夾，可附 `.flowinone.json` | 先掃描到 `item_db.db`，再同步到 Catalog |
| Eagle | Eagle library／執行中的 Eagle local API | 經 Eagle adapter 同步 |
| Chrome Bookmarks | Chrome Default profile 的 `Bookmarks` JSON | 解析書籤檔並同步；不安全或非網頁 URL 略過 |
| Resources | Flowinone 主 SQLite 中自行匯入的 URL 與 `data/content/` | 建立／更新 Resource 後投影到 Catalog |

```text
Local 檔案 → item_db.db ─┐
Eagle API ───────────────┼→ Catalog（主 SQLite）→ 我的收藏（Navigator）
Chrome Bookmarks ────────┤
匯入 URL → Resources ────┘
                └→ processing_jobs → Worker 擷取 → data/content/ → Catalog 全文索引
```

「我的收藏」預設包含四個來源，統一從主題、搜尋、類型與排序瀏覽；來源與其他條件收在「更多篩選」。舊 `scope=gallery` 連結仍只查 Local、Eagle、Chrome，可在更多篩選改成全部收藏。`/resources/` 提供新增網址、匯入與處理工具；閱讀內容仍由 Resource 詳情呈現，Navigator 讀 Catalog 投影。同一 URL 可同時有 Chrome 與 Resource 兩個 origin，但在 Catalog 合成一個 item。網頁／PDF 文字、字幕和可選 AI 摘要是 Resource 的內容，並非額外 Catalog 來源。三張可追程式與儲存層的圖見 [現行架構](docs/architecture.md)。

找程式碼時，先看 `src/` 的應用邏輯；`scripts/` 是匯入、維護與 macOS 啟停的執行入口。根目錄的 `run.py` 供 Flask 與手動啟動使用，`config.py`、`routes.py` 只保留舊匯入相容性；設定與路由實作已在 `src/flowinone/`。

## 功能清單

以下路徑都接在 `http://127.0.0.1:5894` 後面。

| 功能 | 怎麼用 | 成功時會看到什麼／限制 |
| --- | --- | --- |
| 收藏瀏覽 | 開 `/navigator/`，選主題、類型或排序 | 四來源的收藏卡片；可切換視覺／閱讀，卡片優先顯示描述或全文片段 |
| 收藏搜尋 | 在「我的收藏」輸入關鍵字 | 四來源的搜尋結果與命中片段；擷取完成的 Resource 全文也能被找到 |
| 收藏、未看、隨機探索 | 卡片按「收藏」；進階篩選選未看，或按「隨機探索」 | 收藏狀態、篩選結果；同一個 seed 維持同一隨機順序 |
| 常用條件、回到瀏覽位置 | 「更多篩選」→「儲存為常用條件」；用常用條件或快速開啟重訪 | 保存來源與查詢條件；最近瀏覽與返回位置另由 session 管理 |
| 更新來源索引 | 「管理收藏」→「來源與同步」→「同步變更」 | 各來源顯示同步狀態與筆數；Web 會排 job，需要 worker |
| 自動偵測來源變化 | 「來源與同步」→「啟用自動偵測」 | 先建立基準，後續變化穩定後才排同步；預設關閉，需要 worker |
| 本機相同／相似圖片 | 先同步本機索引與 Catalog，再按「分析本機圖片」；圖片卡片按「找相似」 | SHA-256 判斷完全相同檔案，dHash 比較視覺相似；分析只支援本機圖片 |
| 重複圖片檢閱 | 「設定與維護」→「重複圖片檢閱」，選參考圖片、開啟、顯示位置或複製路徑 | 只記錄 Flowinone 的參考選擇，不會刪除或搬動來源檔案 |
| 匯入網址 | 開 `/resources/` 加入 URL；也可匯入 Chrome、JSON／HTML 書籤檔 | 建立 Resource、去除重複 URL，並排入擷取工作 |
| 網頁／PDF／影片內容擷取 | 開著 worker，在 Resource 詳情看工作狀態、metadata 與全文 | 依來源擷取網頁、PDF、YouTube 字幕或 GitHub 內容；實際可用性取決於來源 |
| AI 摘要與標籤 | 設定 LLM provider 後，在 Resource 詳情勾選 AI 再擷取 | 可選功能；需設定 `LLM_BASE_URL`、`LLM_MODEL`，依 provider 需求加 `LLM_API_KEY`；可用本機或外部服務 |
| Resource 標籤、相關內容、版本差異 | 詳情頁修改 tag、查看相關資源；「重新擷取」後看「查看差異」 | 只有擷取文字內容改變才產生新的文字快照；比較需要至少兩個版本 |
| 來源專屬頁面 | `/library/` 進來源總覽；`/folders/`、`/chrome/`、`/EAGLE_folders/`、`/EAGLE_tags/`、`/EAGLE_smart_folders/`、`/EAGLE_stream/` | 保留來源自己的資料夾／樹狀／標籤瀏覽；Eagle 需開啟且 API 可連線 |
| 本機索引、圖片／影片 viewer | `/item_db` 按「同步索引」或「補縮圖」；從卡片開啟 viewer | 顯示本機索引與媒體詳情；索引更新後還要同步 Catalog |
| 可攜 metadata（sidecar） | 用下方維護指令 audit／export／import `.flowinone.json` | metadata 隨本機素材保存；import／export 預設 dry-run |

正式 UI 現在共用三個頁面模板：`browse.html`（瀏覽）、`detail.html`（詳情，包含 Resource 版本差異）、`manage.html`（管理，包含重複圖片檢閱）。日常瀏覽只有「視覺／閱讀」兩個呈現模式；主導覽為「我的收藏／管理收藏」，另有「頁面測試」入口。`/route-guide/` 沿用管理模板，按頁型列出前端 route、模板與使用現有資料的測試連結，標示來源離線或缺少樣本；維護 POST 僅導向原本操作頁。頁型與路由見 [共用頁型說明](docs/ui-consolidation.md)。

Navigator 的頁內搜尋保留目前條件；其他頁面導覽列的搜尋會進全部收藏。瀏覽請使用 `/navigator/`；舊 `/gallery/`、`/search/` 與 `/api/gallery/*` 已移除。橫向與知識模式已整併；舊 `view=shelf` 對應視覺，`view=knowledge`／`reader`／`single`／`linear` 對應閱讀，連結和瀏覽偏好保留原本查詢條件。Resource 詳情先顯示摘要與全文，標籤編輯、擷取狀態與版本差異收在「資源管理」。

首頁 `/` 轉到 `/navigator/?scope=all`，實際讀取路徑是 `CatalogQuery` → `CatalogService.list()` → `browse.html`；預設按最近加入排序，「隨機探索」才使用有 seed 的隨機排序。「今天想看什麼」沿用 Catalog tag facets，最多檢查 6 個候選主題，依目前來源、搜尋、類型、資料夾與瀏覽狀態等條件排除空主題，顯示符合條件的筆數。未選主題的第一批結果最多預覽 3 個主題、每個 3 筆；主題與預覽連結保留 Navigator 條件，預覽項目會進入該主題並定位到卡片。選定主題或載入下一批後只保留主題入口，避免重複顯示預覽。

單標籤查詢使用既有 tag/item 索引；只有要顯示的 3 個主題讀取預覽，其餘入口只讀取符合條件的筆數並沿用 browse metadata 快取。離線 Eagle 只在內容查詢時略過，主題連結仍保留使用者勾選的來源。點主題會切換標籤並清除 cursor；預覽連結必要時把批次大小提高到 3，確保目標卡片可見。手工驗證見 [主題探索操作流程](docs/hands-on.md#9-主題探索如何驗證)。

這是標籤式探索，沒有語意分類、個人化模型或新資料表。候選順序沿用全 Catalog 的標籤筆數，優先略過覆蓋九成以上項目的通用標籤；若全部都是通用標籤則保留它們。只檢查前 6 個候選，因此符合目前條件的冷門標籤可能未出現；未標記或尚未同步的內容仍由原本的結果列表瀏覽，同一項目也可能出現在多個主題。

OCR／人物資料有基礎介面，尚不是完整的日常瀏覽功能。舊筆記／專案工作流與筆記匯出功能已移除；目前也沒有跨裝置同步。

## 啟動

以下命令都在 repo 根目錄執行，使用既有 Conda 環境 `py3.11`。

### 首次設定或升級

先確認 repo 根目錄的 `config.json` 指向存在的媒體資料夾。兩個路徑可以相同，請把範例換成你的實際路徑；已有設定就保留：

```json
{
  "DB_route_external": "/absolute/path/to/media",
  "DB_route_internal": "/absolute/path/to/media"
}
```

`external` 是目前本機索引預設掃描的 root；`internal` 也供來源瀏覽／媒體存取使用。未設定時，互動啟動會嘗試跳出資料夾選擇視窗；無桌面環境可用 `FLOWINONE_HEADLESS=1`，路徑不完整會直接報錯。

```bash
conda run -n py3.11 python -m pip install -e '.[dev]'
conda run -n py3.11 flask --app run resources-db-upgrade
conda run -n py3.11 flask --app run flowinone-doctor
```

預期：升級指令顯示 `resource database is at head`；doctor 的兩個媒體 root 與主資料庫檢查顯示 `OK`。doctor 不代表 Eagle、Chrome 或網頁抓取已驗證成功。

升級會先備份既有主 SQLite；舊版 migration `0007`、`0008` 會移除工作流／筆記資料，升級舊庫前請確認備份。平常啟動不需要每次重裝依賴或升級資料庫。

### 平常使用：macOS 雙擊啟動

完成首次設定後，在 Finder 打開專案資料夾：

雙擊 **[啟動 Flowinone.command](啟動%20Flowinone.command)**，會在同一個終端機啟動 Web 和 Worker，檢查 Chrome 書籤是否可讀，確認服務回應後自動打開瀏覽器。

**使用期間請保持這個終端機視窗開啟；關閉視窗或按 `Ctrl+C`，Web 和 Worker 會一起停止。** 關閉瀏覽器不會停止服務。重複啟動會提示回到原本的終端機，不會建立第二組服務。可以在桌面建立啟動檔的 Finder「替身」，保留原檔在專案裡。

快捷入口啟用既有 Conda `py3.11`，直接在終端機執行；服務隨這個終端機結束，不會安裝背景服務，也不要求額外授予 Python 完整磁碟存取權。素材路徑未設定或失效時，啟動入口會先開啟資料夾選擇視窗，選好後儲存供下次使用。首次安裝依賴與資料庫升級仍依上方步驟完成。預設關閉 debug/reloader。

若原本用下方手動方式啟動，請先在兩個終端機按 `Ctrl+C`，再改用快捷入口。5894 埠被其他程序佔用時會提示，不會直接殺掉該程序。任何一個服務意外退出時，啟動入口會停止另一個服務並顯示錯誤。

啟動與停止狀態會直接顯示在這個終端機，詳細記錄位於 `.flowinone_local/host/web.log`、`worker.log`。若要從已開啟的終端機執行同一個入口，可執行 `./啟動\ Flowinone.command`。

若 `worker.log` 出現 Chrome 書籤的 `Operation not permitted`，代表背景程序讀取被拒。縮圖 Worker 會保留既有佇列工作、每 60 秒重試書籤讀取，不會因此讓整組服務退出；讀取成功前不會更新書籤掃描時間。

Chrome 書籤仍直接讀取設定路徑的 JSON。啟動時顯示 `Chrome 書籤讀取成功（… 筆）` 即代表當次終端機程序可讀取；若仍被 macOS 拒絕，會顯示實際錯誤，不會自動變更系統權限。

### 開發或其他平台：手動開兩個終端機

終端機 A：Web，負責頁面與 API。

```bash
conda run --no-capture-output -n py3.11 python run.py
```

終端機 B：Worker，負責擷取、縮圖、排程同步與圖片分析。

```bash
conda run --no-capture-output -n py3.11 python -m src.flowinone.workers
```

開 `http://127.0.0.1:5894`，首頁會轉到「我的收藏」。終端機各按 `Ctrl+C` 可停止對應程序。

Web 不會自動啟動 worker。只開 A 可以讀取已有資料，但排隊工作會等 B 啟動才執行。兩個程序需要使用相同資料路徑設定；預設都用 repo 的 `data/`，可透過 `FLOWINONE_DATA_DIR` 覆寫。

Chrome 來源預設讀平台的 Default profile `Bookmarks` 檔；若它不存在，會讀同資料夾的 `AccountBookmarks` JSON，不會使用備份或加密檔。Resources 匯入、Chrome 瀏覽、Catalog 同步與背景偵測共用這個路徑。要固定使用其他 profile 或書籤檔，可在 `config.json` 加入 `"CHROME_BOOKMARK_PATH": "/完整路徑/AccountBookmarks"`，或設定優先於它的 `FLOWINONE_CHROME_BOOKMARK_PATH` 環境變數；修改後重新啟動 Web 與 Worker。若 `Bookmarks` 和 `AccountBookmarks` 都存在，預設保留 `Bookmarks`；可用上述設定指定帳戶書籤。若只要把其他書籤檔匯入 **Resources**，可用 `resources-sync --path`，不會改變 Chrome 來源路徑。Eagle 來源需要 Eagle app 正在執行。此服務只設計給本機 loopback 使用，沒有多使用者認證，請勿透過 LAN／proxy／tunnel 對外開放。

### Chrome guid/id 對應與回填

書籤身分對應採用 Chrome profile、local/account 儲存區和 guid（id 作為備援），保留既有 Catalog／Resources 主鍵。migration 本身不回填資料。下方的已檢閱名單模式只建立核准的 binding；另可明確啟用整個 profile 的 Chrome 同步模式。共用 row 網址分歧、目標網址已有其他 row、切換 profile／儲存區或有擷取任務正在執行時，會記錄衝突，不會拆分或合併。`catalog-sync` 回傳 `identity_matched`、`identity_conflicts`、`identity_excluded`；Resources 匯入回傳 `conflicts` 和 `skipped`。

### Chrome 作為書籤主來源

啟用後，Flowinone 會同步目前設定的整個 Chrome profile，包括新增書籤、網址、標題、資料夾和刪除；原先只限已核准名單的範圍不再適用。Chrome 不會被改寫。停止 Web／Worker 後執行：

```bash
conda run -n py3.11 python -B scripts/chrome_mirror.py enable --compact
```

這會直接升級並套用，不建立備份或回收站。往後重新啟動 Worker，Chrome 變動會經預設 30 秒輪詢及 8 秒 debounce 排入同步；停用「來源自動偵測」時則需手動同步。未設定全來源自動偵測時，mirror 模式只自動監看 Chrome。也可用同一指令的 `sync` 手動套用、`status` 查詢數量及縮圖覆蓋率；`--compact` 會 VACUUM 主 DB 和縮圖 DB，僅在服務已停止時使用。

指定其他 `--database` 時，必須同時指定獨立的 `--cache-database` 與 `--cache-dir`，避免用測試資料清理正式快取。

刪掉一個書籤會移除其 GUID binding 和來源；最後一個書籤消失、且沒有手動匯入、Eagle 等獨立來源時，實際刪除內容 row、收藏、標籤關聯、搜尋資料、相關工作和無人使用的快取檔案。同一書籤產生的 Resources 投影不算獨立來源。共用 row 還有書籤時會保留，若剩下的書籤換網址可沿用原 row；其他網址衝突仍需另行處理。資料夾衍生標籤隨移動更新，使用者標籤保留。

啟用時會將兩個 DB 設為 incremental auto-vacuum；刪除後合併搜尋索引、回收空頁並嘗試截斷 WAL。其他讀寫程序佔用時會延後空間回收，資料刪除本身不會回復。

`chrome_bookmark_nodes` 保存每個現存 URL 節點，因此它的數量應和 Chrome URL 書籤數一致；資料夾不計入。非 HTTP(S) 項目也保存在這張清單中，但不建立 Resource 或抓縮圖。`bookmark_bindings` 只包含可匯入項目，Catalog／Resources 則可能因重複網址共用 row。這幾種數字不能直接互相比較。衝突項目的清單仍保存 Chrome 目前網址，內容 row 保留最後接受的網址並標示 conflict。

來源讀取失敗、JSON 結構不完整、GUID/id 重複、或來源在同步期間改變時不套用刪除。縮圖清理只刪 Flowinone 自己管理且無引用的檔案，不刪外部原圖。

### 已核准的網址擁有者衝突整併

一般同步仍不合併衝突。針對已逐組檢閱、兩個 Chrome GUID 現在指向同一 canonical URL 的衝突，可明確整併到目標網址既有的 Catalog／Resource row。選擇檔需列明保留與退役的 row ID、兩個 binding／GUID／Chrome id、網址、資料夾、標籤聯集及描述；不能只提供網址清單。先建立唯讀計畫：

```bash
conda run -n py3.11 python -B scripts/bookmark_consolidate.py preview \
  --database /path/to/flowinone.sqlite3 \
  --bookmarks /path/to/Default/AccountBookmarks \
  --selection /path/to/proposed-plan.json --output /path/to/new-consolidation-plan.json
```

核准後先停止 Web／Worker，再以該計畫的 `approval_digest` 明確套用；不升級 schema、不建立備份，也不改寫 Chrome：

```bash
conda run -n py3.11 python -B scripts/bookmark_consolidate.py apply \
  --database /path/to/flowinone.sqlite3 \
  --bookmarks /path/to/Default/AccountBookmarks \
  --plan /path/to/new-consolidation-plan.json --confirm-plan DIGEST \
  --cache-database /path/to/cache.db --cache-dir /path/to/thumbnails \
  --output /path/to/new-consolidation-result.json
```

指令在 `BEGIN IMMEDIATE` 交易內重查計畫；資料、來源或 Chrome 在執行前／期間改變，整批拒絕或回滾。只支援互不重疊、各有一個 Chrome binding 的 row 對，若有個人狀態／瀏覽事件、獨立來源、內容擷取、工作或人工關聯，需另外規劃，不會自行取捨。保留雙方 binding、來源 ID、資料夾與既有網址歷程，追加一次已接受的網址修改；標籤取聯集、資料夾標籤依兩個現存位置重建，不同非空描述保留雙方內容。目標 row 的 Chrome 標題作為主標題，另一來源標題仍保留；若主 GUID 日後被刪，會使用剩下的來源標題。

資料交易提交後重算自動推薦，再依全 DB 與現存 Chrome 網址引用清理縮圖。回報的 `data_committed` 區分資料整併是否已完成；若後續清理失敗，不應重套已提交的 row 整併，可重跑正常 mirror 同步回收快取。仍被任一書籤或其他內容引用的圖片會保留，因此內容 row 減少不一定同時減少縮圖檔案。

### 已檢閱名單回填模式

先用唯讀預檢報告的 `all-bookmarks.json` 作為基準；其中只有 `classification=matched` 的書籤會列入回填。獨立預覽指令不啟動 Flask、不升級資料庫、不排入網路工作，也不寫 Chrome 書籤。它將資料庫和已提交 WAL 讀入記憶體，查詢只發生在記憶體副本：

```bash
conda run -n py3.11 python -B scripts/bookmark_bindings.py preview \
  --database /path/to/flowinone.sqlite3 \
  --bookmarks /path/to/Default/AccountBookmarks \
  --baseline /path/to/all-bookmarks.json \
  --output /path/to/new-preview.json
```

檢閱預覽中的 counts、bindings、conflicts 與 approval_digest。正式套用前停止 Web／Worker，避免舊同步程序同時寫入。**只有確認寫入後**才使用以下命令；它會先再次驗證預覽，建立資料庫備份、升級 schema，再於單一寫入交易中回填。來源檔、既有 row、收藏、標籤或來源關聯若已改變，必須重新預覽；修改 JSON 也會使核對失敗。

```bash
conda run -n py3.11 python -B scripts/bookmark_bindings.py apply \
  --database /path/to/flowinone.sqlite3 \
  --bookmarks /path/to/Default/AccountBookmarks \
  --baseline /path/to/all-bookmarks.json \
  --plan /path/to/new-preview.json --confirm-plan DIGEST_FROM_REVIEWED_PREVIEW
```

套用時只新增來源登記、binding 與來源紀錄，將已被取代的舊書籤來源標為歷史；不刪除或重建內容 row，不改內容網址、收藏或標籤。成功後可重新啟動 Web／Worker。重跑先產生新預覽，已綁定的書籤不會再次新增。往後網址變更會更新原 row 並保存歷史；Resources 首次收錄的 original_url 保留，canonical_url 反映目前網址。綁定中的網址不會被背景 metadata 抓取自行改寫，舊待處理工作會在網址更新時取消。

migration 0013 尚未套用時，新版 Web／Worker 仍會遵循既有 schema 檢查；不要在檢閱回填前以啟動程式代替 migration。需要復原已回填的資料時使用套用前備份，migration 不允許直接丟棄已有 binding 的表。

### 第一次開起來沒有素材？

1. 到 `/item_db` 按「同步索引」，把本機檔案寫進 `item_db.db`。
2. 回「管理收藏」→「來源與同步」→「同步變更」，開著 worker 等來源同步完成。
3. 重新整理我的收藏，在更多篩選先只勾本機來源驗證。

這是兩個步驟：**檔案 → 本機索引 → Catalog → 畫面**。一般 Catalog 同步讀取既有本機索引，不會替你重新掃描磁碟。自動偵測到 Local 變化所排入的 job 才會先要求更新本機索引。

## 測試

### 先跑一組能看懂的基線

```bash
conda run -n py3.11 pytest -q tests/test_app_architecture.py tests/test_resource_library_core.py
```

這組檢查 app factory／API 輸入輸出，以及 Resource 去重、匯入、標籤與 job 行為。測試使用臨時資料庫或替代依賴，不需要先啟動 Web／Worker。看到 `passed` 表示這些案例成立；不代表你機器上的 Eagle、Chrome 路徑與真實網路一定正常。

### 一次只追一個行為

```bash
conda run -n py3.11 pytest -vv tests/test_security_and_runtime.py::test_catalog_sync_can_run_as_a_durable_worker_job
```

打開 [這個測試](tests/test_security_and_runtime.py)，先看三件事：準備了什麼資料、呼叫了什麼函式、最後的 `assert` 預期什麼。再追到 [ResourceWorker._process](src/flowinone/resource_library/worker.py)。用 `-vv` 看案例名稱，失敗時沿 traceback 找到第一個屬於本 repo 的位置。

| 想驗證的功能 | 對應測試檔（接在 `pytest -q` 後面） |
| --- | --- |
| 啟動、API 格式、安全、worker 分離 | `tests/test_app_architecture.py`、`tests/test_security_and_runtime.py` |
| Catalog 搜尋、合併來源、收藏、session、saved search、sidecar | `tests/test_catalog_next_phase.py` |
| Eagle client 與增量同步 | `tests/test_eagle_api_v2.py`、`tests/test_eagle_catalog_sync.py` |
| 自動偵測來源 | `tests/test_catalog_source_watch.py` |
| 相同／相似圖片、重複檢閱 | `tests/test_catalog_similarity.py` |
| Resource 匯入、頁面、內容擷取與版本 | `tests/test_resource_library_core.py`、`tests/test_resource_routes.py`、`tests/test_resource_enrichment.py` |
| 縮圖、安全 URL、provider、Chrome 不在 request 抓網路 | `tests/test_thumbnail_store.py`、`tests/test_thumbnail_urls.py`、`tests/test_thumbnail_providers.py`、`tests/test_thumbnail_extensions.py`、`tests/test_chrome_no_network.py` |
| Navigator 路徑、同步按鈕重試 | `tests/test_navigator_compatibility.py`、`tests/test_navigator_sync_browser.py` |

較完整的本機回歸（排除顯式標記的 public-site smoke 與效能測試）：

```bash
conda run -n py3.11 pytest -q -m "not online and not performance"
```

重構時先固定以下行為：首頁／Navigator／Catalog JSON 用 `test_app_architecture.py`、`test_navigator_compatibility.py`；Resource 頁面與 API 用 `test_resource_routes.py`；Eagle 增量續傳與故障恢復用 `test_eagle_catalog_sync.py`；同步工作狀態與重試用 `test_catalog_next_phase.py`、`test_security_and_runtime.py`。這些測試都在上面的本機回歸內。

效能基線獨立執行，避免一般回歸每次建立 10 萬筆資料：

```bash
conda run --no-capture-output -n py3.11 pytest -q -s -m performance
```

`test_navigator_home_100k_topics_query_contract` 另外量測含 6 個候選主題的完整首頁 request，檢查只載入 3 組預覽、快取後不重算 exact totals；同樣使用首次 `2.5s`、快取後 `0.75s` 的門檻。這組使用合成資料，不包含真實來源連線延遲。

`test_navigator_sync_browser.py` 需要 Node.js，缺少時會 skip；它用模擬 DOM／fetch 驗證 JavaScript 控制流程，並未開真正瀏覽器。因此畫面布局與真實來源仍需做上面的手動實驗。

需要驗證實際公開網站縮圖時才跑：

```bash
FLOWINONE_ONLINE_SMOKE=1 conda run -n py3.11 pytest -q -m online
```

這會連外，結果受網站與網路影響。測試失敗先區分「程式行為不符」與「外部來源不可用」。

## 從結果追原因

開瀏覽器開發工具的 Network，重做一次操作，記下 **request URL、method、HTTP status、response**。頁面錯誤看終端機 A，背景擷取／同步錯誤看終端機 B。

| 現象 | 先看什麼證據 | 往哪裡追／下一步 |
| --- | --- | --- |
| Navigator 沒本機素材 | `/item_db` 是否有該檔案？來源同步有沒有完成？ | 無索引：`item_db.py`；有索引沒卡片：`CatalogSyncService._sync_local()`、scope／filter |
| 新 Resource 在舊連結搜不到 | URL 是否為 `scope=gallery`？ | 回我的收藏或使用更多篩選的「包含所有收藏」；舊素材範圍不含 Resources |
| 匯入成功但沒全文／縮圖 | Resource 詳情 jobs 是 pending、running、retry 還是 failed？ | pending 查 worker；failed 查 error、`enrichment.py`／`extractors.py`；縮圖另有 `ThumbnailWorker` |
| 同步按下去收到 202 | response 的 job id，再看 `/api/catalog/sync/jobs/<job_id>` | 202 只代表接受排程；配合 `/api/catalog/sync/status` 查看來源進度 |
| Eagle 卡片消失／同步失敗 | Eagle app 與 local API 是否可用？ | `eagle_integration.py` → `eagle_api/client.py`；Navigator 會略過目前不可用的 Eagle 來源 |
| API 回 400 | JSON 中的 `error`／`details`、欄位名稱與型別 | `web/schemas.py`、`web/api.py`；例如字串 `"false"` 不等於 JSON 的 `false` |
| 表單回 403 | 是否從同一個本機網址開頁面？表單 token 是否存在？ | `web/security.py` 的 Host、Origin、CSRF；重新從頁面提交表單 |
| 頁面正常，按鈕沒反應 | Console error、Network 是否送出 request？ | 對應 `static/js/`；已送出就按 response 查後端 |
| 改了 template 沒看到變化 | 編輯的是哪頁的 template？Web 是否重啟？ | 預設 debug 關閉，重啟 A、重新整理；看 `render_template()` 指定哪個檔案 |
| SQLite schema 版本錯誤 | `flowinone-doctor` 與錯誤訊息 | 用 `resources-db-upgrade` 顯式升級；一般 request 不會自動升級 |
| 重新擷取後沒有新版本 | 文字內容的 hash 是否改變？ | `enrichment.py` → `versions.py`；內容沒變可沒有新文字快照 |

## 程式入口地圖

從畫面相關的 route 開始，只讀當次操作會經過的函式。

| 我想理解／修改 | 閱讀順序 |
| --- | --- |
| 啟動與 route 註冊 | [run.py](run.py) → [app.py](src/flowinone/app.py) → [registration.py](src/flowinone/web/registration.py)；根目錄 [routes.py](routes.py) 只保留舊匯入相容性 |
| Navigator 搜尋／篩選 | [catalog/blueprint.py](src/flowinone/catalog/blueprint.py) 的 `navigator_page()` → [catalog/query.py](src/flowinone/catalog/query.py) 的 `CatalogQuery` → [catalog/browse.py](src/flowinone/catalog/browse.py) 的 `CatalogService.list()` → [browse.html](templates/browse.html) |
| 同步按鈕 | [navigator_sync.js](static/js/navigator_sync.js) → [catalog/api.py](src/flowinone/catalog/api.py) 的 `api_sync()` → [jobs.py](src/flowinone/resource_library/jobs.py) → [worker.py](src/flowinone/resource_library/worker.py) → [catalog/sync.py](src/flowinone/catalog/sync.py) 的 `CatalogSyncService`；Eagle 續傳在 [eagle_sync.py](src/flowinone/catalog/eagle_sync.py) |
| URL 匯入／全文擷取 | [Resource UI／API](src/flowinone/resource_library/blueprint.py)（JSON route 在 [api.py](src/flowinone/resource_library/api.py)）→ [ResourceService](src/flowinone/resource_library/service.py)／[Repository](src/flowinone/resource_library/repository.py) → jobs → [EnrichmentService](src/flowinone/resource_library/enrichment.py)／[Extractors](src/flowinone/resource_library/extractors.py)；提交後由 [projection.py](src/flowinone/resource_library/projection.py) 刷新 Catalog |
| Local／Chrome／Eagle 瀏覽 | [web/](src/flowinone/web/) 的對應 route → [file_handler/](src/file_handler/) 的對應來源函式 → template |
| Worker 為何會跑／不跑 | [workers.py](src/flowinone/workers.py) 的 `WorkerRuntime` → Resource worker、[Thumbnail worker](src/file_handler/thumbnails/worker.py)、[Source watcher](src/flowinone/catalog/watch.py) |
| 樣式與畫面互動 | [templates/](templates/) → [static/css/](static/css/)／[static/js/](static/js/) |

### 資料與維護

資料庫、縮圖與歷史內容的保存位置和備份性質見 [現行架構](docs/architecture.md#3-資料架構權威投影與儲存位置)；同步、升級、重建與 sidecar 指令集中在 [使用手冊](docs/renderer-architecture.md#5-維護指令)。

## License

Distributed under the [MIT License](LICENSE).

## 書籤縮圖：共用流程與本機擴充

新增 Chrome 書籤由 Thumbnail Worker 每 60 秒檢查並排入持久佇列；批次補圖也使用同一個 Worker、下載器、圖片驗證與快取。Web 與 Worker 需一起啟動。

```bash
conda run -n py3.11 flask --app run thumbnails-sync --missing
conda run -n py3.11 flask --app run thumbnails-sync --missing --retry-failed --limit 20
```

`--missing` 也包含網站佔位圖。`--retry-failed` 立即重試缺圖項目並保留好封面；`--force` 則連已有封面也更新。已有 Worker 執行時，批次只排隊，由該 Worker 使用相同流程處理；命令的 `processed` 只計算當次執行的工作。

Git 追蹤通用 metadata 解析、佇列、下載邊界、圖片處理與擴充介面。網域特例、個人資料來源與對應測試放在 Git 忽略的 `.flowinone_local/`，預設載入 `.flowinone_local/thumbnail_provider.py`；也可用 `FLOWINONE_LOCAL_THUMBNAIL_PROVIDER` 指定本機檔案。沒有該檔案時仍可解析標準頁面 metadata。

本機擴充可提供以下 hooks（均非必填）：

- `classify_url(url)`：回傳來源分類，無匹配時回傳 `None`。
- `canonicalize_url(url)`：提供來源專用的穩定網址；遷移既有來源時需維持原有正規化結果，以沿用縮圖 ID。
- `resolve(url, fetch_html)`：回傳 `ProviderResult`，包含候選圖片網址、最小寬度與快取期限。
- `resolve_sources(url, media_id, store)`：回傳本機素材或既有 metadata 的候選 `ProviderResult`；來源路徑與索引只在本機擴充內處理。
- `fetch_image(url, page_url, client)`：選擇本機連線策略；使用傳入的 `client.fetch_bytes(...)`，可指定 `headers` 與 `session_factory`，仍共用每網域限流、逐次 redirect 檢查和 byte 上限。無覆寫時回傳 `None`。

`ProviderResult(provider, image_urls=(), local_paths=(), min_width=80, ttl_days=30)` 是共用資料介面；原有只提供 `provider` 和 `image_urls` 的擴充仍可使用。素材重用或圖片下載失敗後會嘗試下一候選；更新失敗保留既有好封面，並依佇列規則退避重試。

本機擴充在 Worker 啟動時載入，修改規則後需重新啟動 Worker。公開測試使用假網域與臨時擴充檔，會隔離開發者本機規則與個人資料。
