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

開啟 `http://localhost:5894`。首頁會導向 Navigator 的「素材」scope。

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

### 3.1 我只想瀏覽圖片、影片與書籤

使用 `/navigator/?scope=gallery`（Navigator · 素材）。

1. 勾選 Local、Eagle 或 Bookmarks，可複選。
2. 先用文字、來源、類型與排序搜尋；tag、收藏、尚未瀏覽與影片長度放在「進階篩選」。輸入不會自動送出，按 Enter 或「套用」才執行。
3. 點卡片後，Local/Eagle media 使用內建 viewer；Bookmark 開啟原始網址。
4. 同一項目若有多個 origin，開啟行為仍依選到的來源決定。
5. `⌘/Ctrl + K` 可開啟常用動作與已儲存搜尋；將目前條件儲存後，可快速回到同一個 scope 和篩選組合。
6. 如需清理本機素材，先到「來源管理」按「分析本機圖片」；分析完成後，圖片卡的「找相似」會先列出完整 SHA-256 相同檔案，再列出 dHash 視覺相近項目。

### 3.2 我不知道資源在哪個來源

切到 `/navigator/?scope=all`（Navigator · 全部內容）。它會搜尋 Local、Eagle、Bookmarks 與 Resources，支援來源、類型、標籤、日期、影片長度、收藏與 keyset 分頁。

Navigator 內只保留一個頁內搜尋，它會保留目前的「素材」或「全部內容」scope。Resources 與其他頁面的導覽列搜尋則預設進入「全部內容」。

### 3.3 我要匯入並擷取網頁內容

使用 `/resources/`：

1. 貼上單一 URL、匯入 Chrome bookmarks，或上傳支援的清單。
2. Flowinone 建立 Resource 與 background job。
3. worker 擷取 metadata、thumbnail、文章/PDF/transcript 內容，以及有設定時的 AI summary。
4. 進入 Resource detail 檢查狀態、原始來源、摘要、全文、標籤與相關資源。
5. 來源內容更新時按「重新擷取」；若新的文字雜湊不同，detail 的「內容版本」會保留新快照，可在「查看差異」比較任兩次擷取。
6. 若 job 失敗，修正連線/來源問題後再 retry。

頁首會同時顯示待處理數與 `worker 線上/離線`。心跳超過 30 秒未更新就視為離線；若 pending 長時間不動，先確認第二個 terminal 的 worker process。

### 3.4 我要看來源原本的階層

由 `/library/` 或導覽列的來源選單進入：

- Eagle：folders、tags、smart folders、stream。
- Chrome：Bookmarks tree。
- Local：folder、grid、slide、item DB。

Navigator 是跨來源的 flat projection；來源頁才保留來源本身的 tree/folder 結構。

## 4. 資料何時會更新

- Local/Eagle/Chrome 的 Catalog 不是即時雙向同步。來源大量變更後，執行 sync。
- 「來源管理」的自動偵測預設關閉。啟用後會低頻讀取來源簽名、先建立 baseline，並在變更去抖動後排入既有 durable sync job；不會在 Flask request 中掃描來源。Local 變更會先更新 item index。
- Navigator 的「來源管理」會排入 durable job 並立即回應；工作由獨立 worker 執行，離開頁面不會取消。同一組 active 同步不會重複排程。
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
