# Flowinone — 快速接手指南

Flowinone 把 **本機圖片／影片、Eagle、Chrome 書籤、匯入的網址** 放到同一個介面瀏覽與搜尋。技術主體仍是 **Python + Flask + Jinja HTML template + SQLite**，互動功能再用少量 JavaScript 呼叫 API。

這份 README 的目標：讓你快速知道 **有什麼能用、如何驗證、結果由哪段程式造成、要改哪裡**。完成第一輪後，你應該能自己追完一個「操作 → route → 資料 → 畫面」的流程。

- 想先用起來：看 [功能清單](#功能清單) 與 [啟動](#啟動)。
- 想動手學會：開 [動手接手練習](docs/hands-on.md)，每題都有 action、預期結果、原因與程式位置。
- 想確認程式行為：看 [測試](#測試)。
- 想定位問題或修改：看 [從結果追原因](#從結果追原因) 與 [程式入口地圖](#程式入口地圖)。

## 先接回你熟悉的 Flask

以前可能一個 route 裡同時查資料、整理資料、render HTML。現在只是把工作分到幾個檔案：

```text
瀏覽器的網址／按鈕
  → Blueprint 裡的 route：接收 request
  → Service：執行功能，需要時呼叫 Repository 或直接寫 SQL
  → SQLite／來源 adapter：取得資料
  → render_template(...)：把資料交給 HTML template
  → 瀏覽器顯示結果
```

| 看到的名詞 | 先這樣理解 |
| --- | --- |
| Blueprint | 把相關 Flask routes 放在一起，例如 Navigator 或 Eagle |
| Service | 處理一件功能的 Python 類別，例如搜尋、匯入、同步 |
| Repository | 集中放資料庫讀寫；Catalog 的部分 SQL 直接在 Service |
| Catalog | 四個來源的統一索引，讓你不用每次開頁面都重新掃描來源 |
| Resource | 匯入 Flowinone 的一筆網址，可附帶擷取全文、標籤與內容版本 |
| Origin | 同一個項目從哪裡來；例如同一網址既是 Chrome 書籤，也是 Resource |
| Worker / Job | 另一支 Python 程式，領取資料庫裡的待辦工作並執行 |
| Migration / FTS | 資料庫結構升級紀錄／全文搜尋索引 |

## 功能清單

以下路徑都接在 `http://127.0.0.1:5894` 後面。

| 功能 | 怎麼用 | 成功時會看到什麼／限制 |
| --- | --- | --- |
| 素材瀏覽 | 開 `/navigator/?scope=gallery`，勾本機、Eagle、書籤，選類型或 tag | 篩選後的素材卡片；這個範圍不包含 Resources |
| 全部內容搜尋 | 切「全部內容」或開 `/navigator/?scope=all`，輸入關鍵字 | 四來源的搜尋結果與命中片段；擷取完成的 Resource 全文也能被找到 |
| 收藏、未看、隨機探索 | 卡片按「收藏」；進階篩選選未看，或按「隨機探索」 | 收藏狀態、篩選結果；同一個 seed 維持同一隨機順序 |
| 儲存搜尋、回到瀏覽位置 | 設定條件後按「儲存目前搜尋」；用「快速開啟」選已存條件 | 保存來源與查詢條件；最近瀏覽與返回位置另由 session 管理 |
| 更新來源索引 | Navigator →「來源管理」→「同步變更」 | 各來源顯示同步狀態與筆數；Web 會排 job，需要 worker |
| 自動偵測來源變化 | 「來源管理」→「啟用自動偵測」 | 先建立基準，後續變化穩定後才排同步；預設關閉，需要 worker |
| 本機相同／相似圖片 | 先同步本機索引與 Catalog，再按「分析本機圖片」；圖片卡片按「找相似」 | SHA-256 判斷完全相同檔案，dHash 比較視覺相似；分析只支援本機圖片 |
| 重複圖片檢閱 | 開 `/catalog/duplicate-review/`，選參考圖片、開啟、顯示位置或複製路徑 | 只記錄 Flowinone 的參考選擇，不會刪除或搬動來源檔案 |
| 匯入網址 | 開 `/resources/` 加入 URL；也可匯入 Chrome、JSON／HTML 書籤檔 | 建立 Resource、去除重複 URL，並排入擷取工作 |
| 網頁／PDF／影片內容擷取 | 開著 worker，在 Resource 詳情看工作狀態、metadata 與全文 | 依來源擷取網頁、PDF、YouTube 字幕或 GitHub 內容；實際可用性取決於來源 |
| AI 摘要與標籤 | 設定 LLM provider 後，在 Resource 詳情勾選 AI 再擷取 | 可選功能；需設定 `LLM_BASE_URL`、`LLM_MODEL`，依 provider 需求加 `LLM_API_KEY`；可用本機或外部服務 |
| Resource 標籤、相關內容、版本差異 | 詳情頁修改 tag、查看相關資源；「重新擷取」後看「查看差異」 | 只有擷取文字內容改變才產生新的文字快照；比較需要至少兩個版本 |
| 來源專屬頁面 | `/library/` 進來源總覽；`/folders/`、`/chrome/`、`/EAGLE_folders/`、`/EAGLE_tags/`、`/EAGLE_smart_folders/`、`/EAGLE_stream/` | 保留來源自己的資料夾／樹狀／標籤瀏覽；Eagle 需開啟且 API 可連線 |
| 本機索引、圖片／影片 viewer | `/item_db` 按「同步索引」或「補縮圖」；從卡片開啟 viewer | 顯示本機索引與媒體詳情；索引更新後還要同步 Catalog |
| 可攜 metadata（sidecar） | 用下方維護指令 audit／export／import `.flowinone.json` | metadata 隨本機素材保存；import／export 預設 dry-run |

Navigator 的頁內搜尋保留目前範圍；其他頁面導覽列的搜尋會進「全部內容」。舊 `/gallery/`、`/search/` 是相容轉址，預計 2026-12-31 後移除；已沒有獨立的 `/api/gallery/*`。

OCR／人物資料有基礎介面，尚不是完整的日常瀏覽功能。舊 Entries、Projects、Collections、Notes、BUILD／THINK／LEARN 等工作流與筆記匯出功能已移除；跨裝置同步、Notion 等新來源、無限 feed 與 React／FastAPI 全面改寫不在目前產品範圍。

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
conda run -n py3.11 python -m pip install -r requirements.txt
conda run -n py3.11 flask --app run resources-db-upgrade
conda run -n py3.11 flask --app run flowinone-doctor
```

預期：升級指令顯示 `resource database is at head`；doctor 的兩個媒體 root 與主資料庫檢查顯示 `OK`。doctor 不代表 Eagle、Chrome 或網頁抓取已驗證成功。

升級會先備份既有主 SQLite；舊版 migration `0007`、`0008` 會移除工作流／筆記資料，升級舊庫前請確認備份。平常啟動不需要每次重裝依賴或升級資料庫。

### 平常使用：macOS 雙擊開／關

完成首次設定後，在 Finder 打開專案資料夾：

- 雙擊 **[啟動 Flowinone.command](啟動%20Flowinone.command)**：同時啟動 Web 和 Worker，確認服務回應後自動打開瀏覽器。
- 雙擊 **[停止 Flowinone.command](停止%20Flowinone.command)**：一次停止這組 Web 和 Worker。

啟動完成後可以關掉終端機視窗；關瀏覽器不會停止服務。重複按啟動會沿用已啟動的程序並開啟網頁，重複按停止也沒關係。可以在桌面建立這兩個檔案的 Finder「替身」，保留原檔在專案裡。

快捷入口使用既有 Conda `py3.11`，透過 macOS 管理背景程序，不需要管理員權限；只在當次登入執行，不設定開機自動啟動。素材路徑未設定或失效時，啟動入口會先開啟資料夾選擇視窗，選好後儲存供下次使用。首次安裝依賴與資料庫升級仍依上方步驟完成。預設關閉 debug/reloader。

若原本用下方手動方式啟動，請先在兩個終端機按 `Ctrl+C`，再改用快捷入口。停止入口只管理它啟動的程序；5894 埠被其他程序佔用時會提示，不會直接殺掉該程序。

啟動失敗可查看 `.flowinone_local/host/web.log`、`worker.log`。也可在終端機查狀態：

```bash
conda run -n py3.11 python scripts/flowinone_host.py status
```

### 開發或其他平台：手動開兩個終端機

終端機 A：Web，負責頁面與 API。

```bash
conda run --no-capture-output -n py3.11 python run.py
```

終端機 B：Worker，負責擷取、縮圖、排程同步與圖片分析。

```bash
conda run --no-capture-output -n py3.11 python -m src.flowinone.workers
```

開 `http://127.0.0.1:5894`，首頁會轉到 Navigator 的「素材」。終端機各按 `Ctrl+C` 可停止對應程序。

Web 不會自動啟動 worker。只開 A 可以讀取已有資料，但排隊工作會等 B 啟動才執行。兩個程序需要使用相同資料路徑設定；預設都用 repo 的 `data/`，可透過 `FLOWINONE_DATA_DIR` 覆寫。

Chrome 預設讀平台的 Default profile 書籤檔；Resources 的 Chrome 匯入表單可指定其他路徑。Eagle 來源需要 Eagle app 正在執行。此服務只設計給本機 loopback 使用，沒有多使用者認證，請勿透過 LAN／proxy／tunnel 對外開放。

### 第一次開起來沒有素材？

1. 到 `/item_db` 按「同步索引」，把本機檔案寫進 `item_db.db`。
2. 回 Navigator →「來源管理」→「同步變更」，開著 worker 等來源同步完成。
3. 重新整理 Navigator，先只勾本機來源驗證。

這是兩個步驟：**檔案 → 本機索引 → Catalog → 畫面**。一般 Catalog 同步讀取既有本機索引，不會替你重新掃描磁碟。自動偵測到 Local 變化所排入的 job 才會先要求更新本機索引。

## 第一次接手的四個實驗

完整可複製指令與逐步操作在 [動手接手練習](docs/hands-on.md)。尚未啟動 app 時，先做其中第 5～7 題；已開 Web 時從第 1 題開始。第 6 題還能用 debugger 暫停在 worker 執行前後，親眼比較同一個 job 的狀態。

每次只改一個條件，先寫下預期，再操作並觀察。下面的收藏、儲存搜尋與匯入會保存資料；可用測試用素材或已存在的 Resource 練習。

| 實驗 | 操作 | 預期結果 | 你驗證的因果 |
| --- | --- | --- | --- |
| 1. 一次搜尋怎麼變成 HTML | 在 Navigator 只選本機，搜一個已同步的檔名；再清空搜尋 | URL 的 `q`／`source` 改變，卡片隨結果改變 | query string → `_navigator_query()` → `CatalogService.list()` → `navigator.html` |
| 2. 一個動作如何被記住 | 收藏一張卡片，重新整理，再切收藏篩選；最後取消收藏 | 收藏在重新整理後保留，取消後不再出現在收藏篩選 | JavaScript POST event → SQLite user state → 下次查詢 |
| 3. Web 與 worker 怎麼分工 | 在 worker 閒置時以 `Ctrl+C` 停止 B；於 Resource 詳情按「重新擷取」，觀察後再啟動 B | Web 仍能開頁面；job 先等待，worker 啟動後才執行或回報失敗 | 建立 job ≠ 完成工作；失敗應查 error，而非持續重按 |
| 4. 來源變化為何沒立刻顯示 | 先關閉自動偵測，在測試媒體資料夾加入一張圖；同步本機索引，再同步 Catalog | 每一步完成後分別查 `/item_db` 與 Navigator | 能判斷資料停在磁碟、本機索引或 Catalog 哪一層 |

第 3 個實驗會抓取你選的 URL；先不用 AI。網路抓取失敗也能驗證 worker 確實領取了工作，但不代表內容擷取成功。完成後確認 B 已重新啟動。

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
| 舊路徑相容、同步按鈕重試 | `tests/test_navigator_compatibility.py`、`tests/test_navigator_sync_browser.py` |

較完整的本機回歸（排除顯式標記的 public-site smoke 與效能測試）：

```bash
conda run -n py3.11 pytest -q -m "not online and not performance"
```

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
| 新 Resource 在「素材」搜不到 | URL 是否為 `scope=gallery`？ | 切 `scope=all`；素材範圍原本就不含 Resources |
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
| 啟動與 route 註冊 | [run.py](run.py) → [routes.py](routes.py) |
| Navigator 搜尋／篩選 | [catalog/blueprint.py](src/flowinone/catalog/blueprint.py) 的 `navigator_page()` → [catalog/service.py](src/flowinone/catalog/service.py) 的 `CatalogQuery`／`CatalogService.list()` → [navigator.html](templates/navigator.html) |
| 同步按鈕 | [navigator_sync.js](static/js/navigator_sync.js) → `api_sync()` → [jobs.py](src/flowinone/resource_library/jobs.py) → [worker.py](src/flowinone/resource_library/worker.py) → `CatalogSyncService` |
| URL 匯入／全文擷取 | [Resource Blueprint](src/flowinone/resource_library/blueprint.py) → [ResourceService](src/flowinone/resource_library/service.py)／[Repository](src/flowinone/resource_library/repository.py) → jobs → [EnrichmentService](src/flowinone/resource_library/enrichment.py)／[Extractors](src/flowinone/resource_library/extractors.py) |
| Local／Chrome／Eagle 瀏覽 | [web/](src/flowinone/web/) 的對應 route → [file_handler/](src/file_handler/) 的對應來源函式 → template |
| Worker 為何會跑／不跑 | [workers.py](src/flowinone/workers.py) 的 `WorkerRuntime` → Resource worker、[Thumbnail worker](src/file_handler/thumbnails/worker.py)、[Source watcher](src/flowinone/catalog/watch.py) |
| 樣式與畫面互動 | [templates/](templates/) → [static/css/](static/css/)／[static/js/](static/js/) |

### 資料究竟放哪裡？

| 位置（預設） | 保存什麼 | 能否直接重建？ |
| --- | --- | --- |
| 原始媒體、Eagle library、Chrome Bookmarks | 來源本體 | 是上游資料，要先保留；Catalog 同步不等於備份來源 |
| `data/item_db.db` | 本機檔案索引，也可能含 sidecar 匯入 metadata | 檔案索引可重掃；自訂 metadata 需保留 sidecar |
| `data/cache.db`、`data/thumbnails/` | 縮圖、cache 與縮圖 jobs | 通常可重抓；cache／job 狀態會重新建立 |
| `data/flowinone.sqlite3` | Resources、Catalog、user state、saved search、jobs 等 | Catalog 的來源投影可重建；收藏、搜尋、Resource 標籤等需要備份，不能整個 DB 當 cache 刪除 |
| `data/content/` | 擷取內容與歷史快照 | 遠端可能已改變，重抓不能保證還原歷史 |
| 媒體資料夾的 `.flowinone.json` | 可攜 metadata | 與素材一起保留 |

主 SQLite 由 SQLAlchemy／Alembic 管理；item／cache DB 由 Python `sqlite3` 管理。根目錄的 `file_handler.py` 是相容轉接，實作在 `src/file_handler/`。

## 維護指令：按目的挑一個

以下表格的命令，都加上前綴 `conda run -n py3.11 flask --app run`。例如：

```bash
conda run -n py3.11 flask --app run catalog-sync --source bookmarks
```

| 命令尾段 | 何時用／影響 |
| --- | --- |
| `flowinone-doctor` | 檢查 root 與主 DB schema；路徑應先設定好 |
| `resources-db-upgrade` | 安裝／升級 schema；先自動備份既有主 DB |
| `catalog-sync --source all` | 四來源投影同步；CLI 直接執行，不需等待 worker；本機仍讀既有 item index |
| `catalog-sync --source eagle --full-rescan` | Eagle checkpoint 不適用時重掃；需 Eagle 可連線 |
| `resources-sync --path "/absolute/path/to/bookmarks.html" --format html` | 匯入書籤為 Resources 並排擷取工作；用 `--no-enqueue` 可只匯入 |
| `resources-worker --limit 20` | 處理最多 20 個當下可執行的共用 jobs 後退出；也可能處理 Catalog jobs，不會啟動 Thumbnail worker／watcher |
| `resources-retry-failed` | 重排 failed jobs，之後仍需 worker 處理 |
| `resources-rebuild-fts` | 重建 Resource 全文索引；Catalog 全文投影另經 Resource source sync 更新 |
| `catalog-relations-rebuild` | 依 metadata 重算可解釋的相關項目 |
| `catalog-similarity-rebuild` | 直接分析已進 Catalog 的本機圖片；不修改原始圖片 |
| `sidecars-audit "/absolute/path/to/media"` | 檢查 sidecar |
| `sidecars-export "/absolute/path/to/media"` | 預覽匯出；加 `--apply` 才寫 manifest |
| `sidecars-import "/absolute/path/to/media"` | 預覽匯入；加 `--apply` 才更新本機索引，之後再同步 Catalog |

## 什麼時候算接手成功？

- [ ] 能啟停 Web／Worker，說出少開其中一個會發生什麼。
- [ ] 能從 Navigator 的 `q` 追到 Python query，再找到 template 裡的卡片迴圈。
- [ ] 能跑一個測試，解釋它的輸入、操作與 `assert`。
- [ ] 能分辨「沒進本機索引」「沒同步 Catalog」「被篩選掉」「job 尚未完成」。
- [ ] 想改一個畫面或功能時，能先指出 route、資料來源與驗證方式。

下一步的小練習：把一個 template 的提示文字改成自己看得懂的說法，重啟 Web 確認，再還原那一處文字。當你能預測修改會影響哪個畫面，就已經開始掌握這個 repo。

## 文件入口

| 想知道什麼 | 看這份 |
| --- | --- |
| 照指令／點擊練習，預測結果，再追到程式碼 | [動手接手：8 個操作練習](docs/hands-on.md) |
| 目前產品有哪些／哪些已移除 | [現況摘要](docs/current-state.md) |
| 模組如何連起來、接下來讀哪個檔案 | [程式碼架構圖](docs/repo-code-map.md) |
| 執行時拓撲、資料權威與邊界 | [現況架構](docs/architecture.md) |
| 安裝、日常操作、同步與故障排除 | [使用手冊](docs/renderer-architecture.md) |
| table 與資料關係／操作流程 | [資料 schema](docs/schema.md)、[操作流程](docs/workflows.md) |
| 待解決問題與後續改造 | [架構與 UI 修正計畫](docs/migration-plan.md)（計畫不等於已實作） |
| 一次改動改了什麼、如何驗證 | [Commit 改動紀錄](docs/commit-change-log.md) |

## License

Distributed under the [MIT License](LICENSE).
