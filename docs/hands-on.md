# Flowinone 動手接手：操作 → 結果 → 原因 → 程式碼

目標是讓你能預測程式行為，並驗證自己的預測。每次只做一個練習；先猜結果，再執行，最後只讀列出的函式。

建議先讀 [README 的四來源摘要](../README.md) 與 [現行架構的三張圖](architecture.md)，再選下面的練習。

所有 CLI 都在 repo 根目錄執行，Python 一律使用 `py3.11`。`rg -n` 是搜尋程式碼並顯示行號；也可用編輯器的全專案搜尋。文件中的行號只供目前版本的 debugger 練習，更新後以函式名稱重新定位。

## 先選一條路

| 目前狀態 | 從哪裡開始 |
| --- | --- |
| 還沒設定媒體路徑，也沒啟動 app | 先做第 5、6、7 題的 pytest，使用臨時 DB／替代來源，不用開 Web 或 Eagle |
| 已照 [README 啟動流程](../README.md#啟動) 開起 Web | 做第 1～4 題；只需要 Web，已有資料才能做搜尋與收藏 |
| 想弄懂資料怎麼更新 | 做第 6 題，再做第 8 題 |

每題完成後寫一句：**「我改了 ____，看到 ____，因為 ____ 函式做了 ____。」** 能說出這句，就比只看到測試通過多掌握了一層。

## 1. 網址到底交給誰處理？

**操作 A：列出 route。** 先完成 README 的 root 設定，避免 Flask 建立 app 時跳出資料夾選擇視窗。

```bash
conda run -n py3.11 flask --app run routes
```

**預期：** 表格包含以下 endpoint 與 rule；輸出順序、HEAD／OPTIONS 顯示可能不同。

| Endpoint | Rule | 意義 |
| --- | --- | --- |
| `local.index` | `/` | 首頁入口 |
| `catalog.navigator_page` | `/navigator/` | Navigator HTML |
| `catalog.api_items` | `/api/catalog/items` | Catalog JSON |
| `resource_library.api_resources_create` | `/api/resources` | 新增 Resource 的 POST route |

**操作 B：Web 啟動後，查看首頁 response，不跟隨轉址。**

```bash
curl -sS -D - -o /dev/null http://127.0.0.1:5894/
```

**預期：** status 是 `302`，header 有 `Location: /navigator/?scope=gallery`。`curl` 沒加 `-L`，所以你看見的是轉址本身；一般瀏覽器會自動繼續載入 Navigator。

**為什麼／哪段程式：** [run.py](../run.py) 的 `create_app()` 呼叫 [routes.py](../routes.py) 的 `register_routes()`，註冊各 Blueprint。[web/local.py](../src/flowinone/web/local.py) 的 `index()` 回傳：

```python
return redirect(url_for("catalog.navigator_page", scope="gallery"))
```

搜尋位置：

```bash
rg -n 'def create_app|def register_routes|def index|def navigator_page|def api_items' run.py routes.py src/flowinone
```

**換一個條件：** 開 `http://127.0.0.1:5894/navigator/?scope=all&q=hello`。預期直接載入跨來源搜尋頁，搜尋字保留為 `hello`。

**若不同：** connection refused 先看 Web 是否啟動；不要從 Catalog SQL 開始找。

## 2. 搜尋字如何一路變成卡片？

**前提：** Navigator 已有本機素材；若沒有，先做第 8 題，或用第 7 題的測試觀察搜尋。

**操作：**

1. 開 `http://127.0.0.1:5894/navigator/?scope=gallery&source=local`。
2. 記下一張卡片的名稱；在搜尋框輸入它的一個可辨識單字，送出搜尋。
3. 查看網址列的 `q`、`source=local`、`scope=gallery`。
4. 開瀏覽器開發工具 → Network，重新整理，點選 `/navigator/` 的 document request，看 Query String Parameters 與 Response。
5. 清空搜尋再送出，比較卡片。

**預期：** `q` 隨輸入改變；response 是包含卡片的 HTML。清空 `q` 後解除文字條件，但本機來源限制仍存在。這裡送出表單會導覽到新的 URL。

**為什麼／哪段程式：**

```text
templates/navigator.html：<form method="get">，input name="q"
  → catalog/blueprint.py：_navigator_query() 讀 request.args
  → CatalogQuery.create() 整理條件
  → navigator_page() 呼叫 CatalogService.list(effective_query)
  → catalog/browse.py：list() 查 Catalog／全文索引
  → render_template("navigator.html", payload=..., query=...)
  → template 的 for item in payload['items'] 畫卡片
```

```bash
rg -n 'name="q"|for item in|render_template|def _navigator_query|def navigator_page|def list\(' templates/navigator.html src/flowinone/catalog/blueprint.py src/flowinone/catalog/browse.py
```

**換一個條件：** 在搜尋 URL 把 `q` 改成 `flowinonezznomatch987654`。若資料中沒有這個詞，結果應為零；頁面仍正常顯示，零筆不是 server error。

**若不同：** 先清除 tag、favorite、type 等額外條件。搜尋使用全文索引，不保證任意片段都能命中；換卡片中的完整單字驗證。

## 3. 收藏是改了 HTML，還是寫入資料？

**操作：**

1. 在 Navigator 選一張原本未收藏的卡片，先記下原始狀態。
2. 開 Network，清空列表，按卡片「收藏」。
3. 找到 `POST /api/catalog/items/<item_id>/events`，看 Payload 與 Response。
4. 重新整理頁面；再打開進階篩選的「只看最愛」，送出查詢。
5. 清除收藏篩選，找到同張卡片，再取消收藏，還原原始狀態。

**預期：** request body 有 `{"event_type":"favorite"}`，成功 response 為 `200` 且項目 `favorite` 為 `true`。按鈕變成「已收藏」，重新整理後仍保留；取消時送出 `unfavorite`。卡片是否立即從目前列表消失，與下一次查詢是否排除它，是兩件事。

**為什麼／哪段程式：**

- [navigator.html](../templates/navigator.html) 的 `.navigator-favorite` click handler 先 `fetch()`，成功後改按鈕文字。
- [catalog/api.py](../src/flowinone/catalog/api.py) 的 `api_event()` 把事件交給 `CatalogService.record_event()`。
- [catalog/browse.py](../src/flowinone/catalog/browse.py) 的 `record_event()` 新增 `catalog_events`，並更新 `item_user_state.favorite`；所以刷新後能再讀回。

```bash
rg -n 'navigator-favorite|def api_event|def record_event|UPDATE item_user_state SET favorite' templates/navigator.html src/flowinone/catalog
```

**若不同：** 沒 request 先查 Console／click handler；非 200 看 response；已 200 但重整不保留，再追寫入與下次查詢。取消收藏會還原收藏狀態，但事件紀錄仍保留。

## 4. 親手造成一次 400，知道錯誤在哪一層

**操作：** Web 開著時，在終端機貼上：

```bash
curl -sS -i http://127.0.0.1:5894/api/resources \
  -H 'Content-Type: application/json' \
  -H 'Origin: http://127.0.0.1:5894' \
  --data '{"url":"https://example.com/learning","enqueue":"false"}'
```

**預期：** `400`，JSON 的 `error` 是 `invalid_request`，`details` 指向 `enqueue`。不會建立 Resource，也不會擷取 URL，因為在呼叫 service 前就拒絕了輸入。

**為什麼／哪段程式：** [web/schemas.py](../src/flowinone/web/schemas.py) 的 `ResourceCreateRequest.enqueue` 要求 `StrictBool`。JSON 的 `"false"` 是字串，`false` 才是布林值。[web/api.py](../src/flowinone/web/api.py) 的 `parse_json()`／`parse_payload()` 驗證失敗，error handler 回 400；[Resource API](../src/flowinone/resource_library/api.py) 的 `api_resources_create()` 因此還沒走到 `_service().create_url()`。

```bash
rg -n 'class ResourceCreateRequest|enqueue:|def parse_json|def parse_payload|def handle_request_validation|def api_resources_create' src/flowinone
```

**不開 Web 的替代操作：**

```bash
conda run -n py3.11 pytest -vv tests/test_app_architecture.py::test_api_contract_rejects_coercion_unknown_fields_and_bad_query
```

**預期：** `1 passed`；打開測試看 `assert coerced.status_code == 400`，它就是剛才 curl 的可重複版本。這題故意驗證錯誤輸入；改成合法布林值的 live POST 會真的建立 Resource。

## 5. 兩個 URL 為什麼只剩一筆 Resource？

**操作：** 不必啟動 Web／Worker，執行現成案例：

```bash
conda run -n py3.11 pytest -vv tests/test_resource_library_core.py::test_duplicate_urls_merge_but_origins_and_fts_remain
```

**預期：** `1 passed`。打開 [test_resource_library_core.py](../tests/test_resource_library_core.py) 的這個函式，對照它的實際輸入與斷言：

| 輸入／操作 | 預期資料 |
| --- | --- |
| `https://example.com/a?utm_source=one`，放在資料夾 A | 第一筆 Resource |
| `https://example.com/a`，放在資料夾 B | 判斷為重複，不新增第二筆 Resource |
| 查 import summary | `created == 1`、`duplicates == 1` |
| 查 Resource 的 origins | 同時保留 A、B |
| 搜尋第一筆的 `Knowledge` | 一筆結果 |

**為什麼／哪段程式：** [ResourceService.import_records()](../src/flowinone/resource_library/service.py) 逐筆交給 [ResourceRepository.upsert_bookmark()](../src/flowinone/resource_library/repository.py)。[canonical.py](../src/flowinone/resource_library/canonical.py) 與共用 URL 正規化邏輯去除追蹤參數；Resource 身分相同，來源位置仍分別保存。

```bash
rg -n 'def import_records|def upsert_bookmark|def normalize_resource_url|def canonicalize_url|utm_' src/flowinone/resource_library src/file_handler/thumbnails/urls.py
```

**你應該能回答：**「一個 Resource」不等於「只出現在一個書籤資料夾」。此案例設定 `enqueue=False`、`link_thumbnails=False`，驗證的是資料規則，不會去抓 example.com。

## 6. 暫停程式，親眼看 job 從 pending 變 complete

先跑一次正常測試：

```bash
conda run -n py3.11 pytest -vv tests/test_security_and_runtime.py::test_catalog_sync_can_run_as_a_durable_worker_job
```

**預期：** `1 passed`。這題用臨時資料庫，並以假同步結果取代實際 Chrome 掃描，驗證的是「排程 → 領取 → 執行 → 完成」流程。

接著進入 Python debugger。`--trace` 是在測試開始時暫停；`-s` 與 `--no-capture-output` 讓你可以在終端機互動：

```bash
conda run --no-capture-output -n py3.11 python -m pytest -q -s --trace tests/test_security_and_runtime.py::test_catalog_sync_can_run_as_a_durable_worker_job
```

出現 `(Pdb)` 後，依序輸入以下 debugger 指令（不要把 `(Pdb)` 本身貼進去）：

```text
b tests/test_security_and_runtime.py:132
b tests/test_security_and_runtime.py:135
c
p queued.status_code
p client.get(f"/api/catalog/sync/jobs/{job_id}").get_json()["job"]["status"]
c
p completed.status_code
p completed.get_json()["job"]["status"]
c
```

**預期：**

1. 第一個 `c` 停在呼叫 `run_until_idle()` 之前；兩次 `p` 分別看到 `202`、`'pending'`。
2. 第二個 `c` 跑過 worker，再停在最後的 assert；兩次 `p` 看到 `200`、`'complete'`。
3. 最後 `c` 讓測試跑完，顯示 `1 passed`。`b` 是設中斷點、`c` 是繼續、`p` 是印出運算結果；想提前離開用 `q`，測試會被中止。

行號若已變動，先找「跑 worker 的 assert」與「檢查完成的 assert」，把兩個 `b` 的數字換掉：

```bash
rg -n 'assert ResourceWorker\(database|assert completed.get_json' tests/test_security_and_runtime.py
```

**為什麼／哪段程式：**

```text
測試 client.post("/api/catalog/sync")
  → catalog/api.py：api_sync()
  → resource_library/jobs.py：JobQueue.queue()，寫 pending，HTTP 回 202
  → 測試明確呼叫 ResourceWorker.run_until_idle(max_jobs=1)
  → worker.py：_run_loop() 透過 queue.claim() 領取工作
  → _process() 分派 catalog_sync，呼叫 CatalogSyncService.sync()
  → queue.complete() 更新 job
  → 測試重新 GET 同一個 job，HTTP 回 200，job status 為 complete
```

測試特別設 `FLOWINONE_CATALOG_SYNC_INLINE=False`，才能驗證與正常 Web 排隊模式相同的路徑。`202` 是 HTTP 的「接受工作」；`complete` 是工作狀態，兩者不能混用。

## 7. 改一個 filter，確認它如何改變查詢

**操作：**

```bash
conda run -n py3.11 pytest -vv tests/test_catalog_next_phase.py::test_catalog_merges_origins_filters_fts_cursor_events_and_sessions
```

**預期：** `1 passed`。打開 [測試函式與上方的 _catalog()](../tests/test_catalog_next_phase.py)，只看前半部搜尋：

1. `_catalog()` 建立測試項目，其中一筆 Resource 的擷取文字是 `catalog search projection`。
2. `CatalogQuery.create(q="projection", sources=["resources"], sort="relevance")` 搜到這筆。
3. 斷言 `match_reason.field == "content"`，表示命中來源是擷取內容。
4. 改搜 Eagle 的 `visual` 時，測試預期命中標題；搜 `taxonomy` 時預期命中 tag。

**為什麼／哪段程式：** [catalog/query.py](../src/flowinone/catalog/query.py) 的 `CatalogQuery.create()` 保存條件，[catalog/browse.py](../src/flowinone/catalog/browse.py) 的 `CatalogService.list()` 依來源與文字查詢，並組出 `match_reason`，交給 Navigator 顯示命中理由。

**再操作一次：**

```bash
rg -n 'match_reason|match_field|catalog_fts|def list\(' src/flowinone/catalog/browse.py templates/navigator.html
```

**預期：** 同時找到查詢／結果欄位的 Python 與顯示它的 template。你要能指出：改命中規則要看 Python，改「命中」提示的呈現要看 HTML。

## 8. 新圖在磁碟上，為什麼 Navigator 還沒有？

**前提：** Web 已啟動，root 指向你的測試素材資料夾；先在 Navigator →「來源管理」暫停自動偵測，記下原設定。此題會更新本機索引與 Catalog。

**操作與觀察：**

| 步驟 | action | 預期結果 |
| --- | --- | --- |
| A | 用檔案管理器複製一張小圖片到 `DB_route_external` 下，取一個不重複的名稱，例如 `flowinonelearning987.jpg`（副檔名與原檔一致） | 磁碟有檔案；此時 Navigator 不一定知道，單純重新整理不會掃描磁碟 |
| B | 開 `/item_db` →「同步索引」，完成後回本機索引頁找該檔案 | `item_db.db` 已有該圖；還未代表 Catalog 更新 |
| C | 執行下方 CLI，只同步 Local | 輸出 `local` 狀態為 `complete`；直接執行，這一步不需要 worker |
| D | 開 `/navigator/?scope=gallery&source=local&q=flowinonelearning987` | 能找到該圖；點開會進 local viewer |

```bash
conda run -n py3.11 flask --app run catalog-sync --source local
```

**為什麼／哪段程式：**

```text
磁碟新增圖片
  → /item_db 的「同步索引」form POST /update_db
  → web/local.py：update_item_db_route()
  → file_handler/item_db.py：update_item_database() 掃描 root，更新 item_db.db
  → catalog-sync CLI → CatalogSyncService.sync(("local",))
  → catalog/sync.py：_sync_local() 讀 fetch_items()，寫 Catalog
  → CatalogService.list() 查到 → navigator.html 顯示
```

```bash
rg -n 'def update_item_db_route|def update_item_database|def _sync_local|def catalog_sync' src
```

**若不同：** B 找不到，先查 root、圖片類型與掃描回傳的 errors；B 有但 D 無，查 C 的 status、名稱與 filter。不要先重抓全部 Eagle／Chrome。

**收尾：** 用檔案管理器只把剛才的測試副本移到垃圾桶，再重做 B、C，確認 Navigator 不再顯示；依原設定恢復自動偵測。

## 最後做一次自己的修改

1. 在 [navigator.html](../templates/navigator.html) 找卡片附近的「命中：」文字，記下原文，改成「符合搜尋的原因：」。
2. 重新啟動 Web，回到第 7 題對應的真實搜尋場景，找有命中片段的結果。
3. 預期顯示文字改變，搜尋結果不因這次純文案修改而改變；原因是查詢在 `CatalogService.list()`，這次改的是 template。
4. 只把剛才那一處文字改回，重新啟動。不要用整檔 Git 還原，以免覆蓋其他未提交修改。

如果真實資料沒有可顯示的命中片段，可以改 Navigator 的固定標題，確認後還原。這一步的驗收是：**你能先指出哪個檔案會影響哪個畫面，再親手證明。**
