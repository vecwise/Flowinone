# Flowinone 使用手冊

先讀 [README](../README.md) 掌握四個資料來源；需要知道資料流、程式與儲存關係時看 [現行架構](architecture.md)。本頁集中放啟動、日常操作、同步與維護指令。

## 1. 使用前準備

- macOS / 本機環境，repo 位於目前專案目錄。
- 使用既有 Conda environment `py3.11`；不要另建 venv，也不要使用 system Python。
- 若要讀取 Eagle，先開啟 Eagle，並確認 Eagle local Web API 可用。
- 若要讀取 Local media，先在 `config.json` 設定有效的 roots。
- 若是既有安裝，啟動或升級前先備份 `data/flowinone.sqlite3`。migration `0007`、`0008` 會刪除舊版 workflow/curation/personal-note 資料。

## 2. 第一次啟動

從 repo root 執行：

```bash
conda run -n py3.11 python -m pip install -e '.[dev]'
conda run -n py3.11 flask --app run resources-db-upgrade
conda run -n py3.11 flask --app run flowinone-doctor
conda run --no-capture-output -n py3.11 python run.py
```

開啟 `http://localhost:5894`。首頁會導向「我的收藏」，預設包含全部四個來源。

另開一個 terminal 啟動背景 worker：

```bash
conda run -n py3.11 python -m src.flowinone.workers
```

Web app 沒有 worker 仍可瀏覽，但新匯入 Resource 的 metadata、全文、摘要、bookmark thumbnail 與 UI 排程的 Catalog sync 會停在 pending。系統沒有多使用者認證，不要把服務綁到 LAN 或公開網路。

### Headless 啟動

先在 `config.json` 填好 Local roots，再設定：

```bash
FLOWINONE_HEADLESS=1 conda run -n py3.11 python run.py
```

若未設定 headless 且路徑缺失，程式會在明確建立 web app 時開啟資料夾選擇器；單純 import config、測試或 worker module 不會開 GUI 或寫檔。

## 3. 使用者從哪裡開始

### 3.1 我想順順地瀏覽收藏

使用 `/navigator/`（我的收藏）。

1. 用搜尋列或「主題／常用」進入收藏；類型可在同列選擇。結果標頭顯示目前主題／搜尋與項目數，已選條件可逐一移除。
2. 來源、標籤、最愛、尚未瀏覽與影片長度收在「篩選」面板。搜尋按 Enter／「搜尋」，進階條件按「套用篩選」；結果右上排序選完立即生效，保留已套用條件、清除分頁與舊位置。未送出的搜尋文字會保留，不參與排序查詢。
3. 點卡片開啟收藏預覽；可接著開啟來源或閱讀 Resource 內容。網格與連續模式共用相同結果。
4. 同一項目若有多個 origin，開啟行為仍依選到的來源決定。
5. 結果右上「⋯」可儲存為常用條件、複製條件連結與選擇縮圖比例；`⌘/Ctrl + K` 可開啟常用動作與條件。
6. 「設定與維護」可進入重複圖片檢閱與「來源與同步」。先分析本機圖片，圖片卡「更多」中的「找相似圖片」才有結果。

收藏預覽與已建立 Catalog 索引的項目詳情可點擊標籤、已連結人物及 EAGLE 資料夾，進入同一套 Navigator 查看相關項目。切換入口會清除原本的搜尋與頁內條件，保留來源選擇；進入後可繼續搜尋、篩選、排序與分頁。人物入口沿用既有 `people`／`item_people` 關係，尚不會把人物名稱 tag 自動轉成人物。

入口使用 `target=tag:<id>`、`target=person:<id>`、`target=eagle_folder:<library-signature>:<folder-id>`。EAGLE 資料夾顯示直接歸屬於該 folder 的索引項目；來源樹狀頁仍用於瀏覽子資料夾。來源未包含 EAGLE 時會提示調整來源，不會自行開啟該來源。

### 3.1.1 按 EAGLE 漫畫／圖集探索

更新後先同步一次 EAGLE。直接含圖片的 folder 會成為一筆「漫畫／圖集」item；只有子資料夾、只有影片或空的 folder 不會變成圖集。

在「我的收藏」選「漫畫／圖集」，再搜尋 folder 名稱，例如「夏日森林自然攝影作品」，會顯示整組作品的封面卡片與圖片數。中文完整名稱與片段（例如「森林」）皆可搜尋。單張圖片保留在「圖片」類型；全部類型會同時包含兩者。

點圖集直接進入該 folder 的圖片與影片，清除外層關鍵字與圖集條件，以「頁碼／檔名順序」排列，分頁能看完所有直接歸屬媒體。子 folder 的圖片不會混入。頁面可返回原本搜尋結果。圖集的 tag 表示「至少有一張成員圖片帶此 tag」，不是每張圖片都有。

Folder 改名、移動或增減圖片後再同步，會更新名稱、封面、圖片數與成員 tag。固定身分使用資源庫與 folder ID；不會因改名重建圖集。這些都是 Catalog 投影，不會修改 EAGLE 原始資料，也不需要新增 Collection DB 或執行 schema migration。

### 3.2 我不知道資源在哪個來源

「我的收藏」預設會搜尋 Local、Eagle、Bookmarks 與 Resources。選擇來源後，其他頁面的導覽列搜尋也會沿用該選擇。

Navigator 頁內搜尋會保留目前條件。尚未設定全域來源時，舊 `scope=gallery` 連結仍限制在本機、Eagle 與書籤，可在「篩選 → 來源」按「全選」。舊橫向模式對應網格，知識模式對應連續模式。

### 3.3 我要匯入並擷取網頁內容

使用 `/resources/`：

1. 貼上單一 URL；Chrome 或檔案匯入收在「匯入書籤」。
2. Flowinone 建立 Resource 與 background job。
3. worker 擷取 metadata、thumbnail、文章/PDF/transcript 內容，以及有設定時的 AI summary。
4. Resource 詳情先呈現摘要、全文與相關資源；「資源管理」可編輯標籤、檢查狀態與原始來源。
5. 來源內容更新時在資源管理按「執行擷取」；若新的文字雜湊不同，「內容版本」會保留新快照，可在「查看差異」比較任兩次擷取。
6. 若 job 失敗，修正連線/來源問題後再 retry。

新增頁的「已加入網址的處理工具」顯示待處理數與背景處理狀態，也可選取網址批次擷取。心跳超過 30 秒未更新就視為離線；若 pending 長時間不動，先確認第二個 terminal 的 worker process。

### 3.4 我要看來源原本的階層

由 `/library/` 或導覽列的「設定與維護」進入：

- Eagle：folders、tags、smart folders、stream。
- Chrome：Bookmarks tree。
- Local：folder、grid、slide、item DB。

Navigator 是跨來源的 flat projection；來源頁才保留來源本身的 tree/folder 結構。

## 4. 資料何時會更新

- Local/Eagle/Chrome 的 Catalog 不是即時雙向同步。來源大量變更後，執行 sync。
- 「來源與同步」的自動偵測預設關閉。啟用後會低頻讀取來源簽名、先建立 baseline，並在變更去抖動後排入既有 durable sync job；不會在 Flask request 中掃描來源。Local 變更會先更新 item index。
- Navigator 的「來源與同步」會排入 durable job 並立即回應；工作由獨立 worker 執行，離開頁面不會取消。同一組 active 同步不會重複排程。
- Resource 建立、更新、改 tags 或 enrichment 完成時，現行 service 會自動同步該筆 Catalog projection；只有 projection 漂移、批次修復或來源大量變更時才需要手動 Catalog sync。
- Eagle metadata 請先透過 Eagle UI 或 Eagle local API 修改，不要直接改 `.info/metadata.json`；之後再 sync Flowinone。

一般同步：

```bash
conda run -n py3.11 flask --app run catalog-sync --source all
```

只同步 Eagle：

```bash
conda run -n py3.11 flask --app run catalog-sync --source eagle
```

checkpoint 損壞或需要完整重建 Eagle projection 時才用：

```bash
conda run -n py3.11 flask --app run catalog-sync --source eagle --full-rescan
```

預設的 Eagle sync 是可續傳 incremental batch；full rescan 會較久。

## 5. 維護指令

手動升級 schema；這個 command 會先在 `data/backups/` 建立 timestamped SQLite backup，並驗證來源與備份完整性：

```bash
conda run -n py3.11 flask --app run resources-db-upgrade
```

執行非破壞性環境檢查：

```bash
conda run -n py3.11 flask --app run flowinone-doctor
```

一次處理有限數量的 Resource jobs：

```bash
conda run -n py3.11 flask --app run resources-worker --limit 20
```

重建搜尋與關聯：

```bash
conda run -n py3.11 flask --app run resources-rebuild-fts
conda run -n py3.11 flask --app run catalog-relations-rebuild
conda run -n py3.11 flask --app run catalog-similarity-rebuild
```

Local sidecar 稽核、匯出與匯入：

```bash
conda run -n py3.11 flask --app run sidecars-audit "/absolute/path/to/media"
conda run -n py3.11 flask --app run sidecars-export "/absolute/path/to/media"
conda run -n py3.11 flask --app run sidecars-import "/absolute/path/to/media"
```

Export／import 預設只預覽；確認輸出後加 `--apply` 才會寫入。Import 更新本機索引後，還需同步 Local Catalog。

## 6. 常見問題

| 現象 | 先檢查 | 建議處理 |
| --- | --- | --- |
| Eagle 顯示離線或資料不完整 | Eagle app 與 local API | 開啟 Eagle，再執行 Eagle sync |
| Resource 一直 pending | worker terminal | 啟動 `python -m src.flowinone.workers`，檢查失敗 job |
| Navigator 看不到剛改的來源資料 | Catalog projection 尚未更新 | 執行對應 source sync |
| 找相似顯示尚未分析 | 尚未建立 Local 圖片 artifact | 在來源管理按「分析本機圖片」，或執行 `catalog-similarity-rebuild`；確認 worker 正在運行 |
| Resource 無法比較版本 | 只有一個不同文字快照，或快照檔已缺失 | 重新擷取一次內容有變更的來源；確認 `data/content/` 未被清除 |
| 啟動時出現資料夾選擇器 | `config.json` 缺 Local roots | 補齊設定；自動化環境加 `FLOWINONE_HEADLESS=1` |
| 顯示 database schema not current | 尚未手動 migration | 執行 `resources-db-upgrade`；不要靠 web request 自動升級 |
| 設定或 DB 路徑不明 | runtime 環境差異 | 執行 `flowinone-doctor`；所有內建 DB 均來自絕對 `FLOWINONE_DATA_DIR` |

## 7. 現行邊界

Flowinone 目前提供四來源瀏覽、Resource 擷取與 Catalog 搜尋；舊筆記／專案工作流已移除，也不提供跨裝置同步。`/debug/` 只在 `FLOWINONE_DEV_TOOLS` 啟用時註冊；舊 Gallery Lab 與 `/api/gallery/*` 已移除。
