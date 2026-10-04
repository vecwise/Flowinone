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

1. 先選主題，再用搜尋、類型與排序縮小結果。
2. 來源、標籤、最愛、尚未瀏覽與影片長度放在「更多篩選」。輸入不會自動送出，按 Enter 或「套用」才執行。
3. 點卡片開啟收藏預覽；可接著開啟來源或閱讀 Resource 內容。網格與列表共用相同結果。
4. 同一項目若有多個 origin，開啟行為仍依選到的來源決定。
5. 「更多篩選」可儲存為常用條件；`⌘/Ctrl + K` 可開啟常用動作與條件。
6. 「設定與維護」可進入重複圖片檢閱與「來源與同步」。先分析本機圖片，圖片卡「更多」中的「找相似圖片」才有結果。

### 3.2 我不知道資源在哪個來源

「我的收藏」預設會搜尋 Local、Eagle、Bookmarks 與 Resources。其他頁面的導覽列搜尋也會進全部收藏。

Navigator 頁內搜尋會保留目前條件。舊 `scope=gallery` 連結仍限制在本機、Eagle 與書籤，可在更多篩選按「包含所有收藏」。舊橫向模式對應網格，知識模式對應列表。

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
