# 共用頁型與路由

Flowinone 使用瀏覽、詳情、管理三種正式頁型，沿用 Flask/Jinja 與既有資料來源。

| 正式頁型 | 入口與內容 | 共用部分 |
| --- | --- | --- |
| `browse.html` | `/navigator/`、`/both/<path>/`、`/folders/`、`/chrome/<path>/`、`/chrome_youtube/`、Eagle Folders / Tags / Smart Folders / Stream 與搜尋結果 | 頁首、卡片、網格／清單切換、空／失敗狀態、分批連結 |
| `detail.html` | `/image/<path>`、`/video/<path>`、Eagle Image / Video、`/resources/<id>/`、`/resources/<id>/versions/` | 頁首、返回條件；內容與版本檢視導覽；完整內容與 Navigator 預覽共用內容結構 |
| `manage.html` | `/library/`、`/resources/`、`/item_db`、`/route-guide/`、`/ui-lab/`、`/catalog/duplicate-review/`、三種本機維護 POST 結果 | 管理頁首與功能導覽；來源同步、匯入、索引、結果及重複群組檢閱；UI 比較 |
| 階段 | commit | 正式頁面數 |
| --- | --- | --- |
| 起點 | `d47b4c3` | 17 |
| 1 | `0b03d82` | 12 |
| 2 | `a122afa` | 8 |
| 3 | `0ed7950` | 4 |
| 4 | `git log --oneline --grep="stage 4 unify navigation"` | 4 |

Navigator 查 Catalog；來源資料夾直接讀來源。Grid／Slide 舊網址轉到共用瀏覽頁，來源離線或缺少資料時顯示狀態與準備入口。

`/route-guide/` 列出頁型、route、模板與現有資料範例；維護 POST 導回原操作頁提交表單。版本差異歸入詳情，重複圖片檢閱歸入管理。
