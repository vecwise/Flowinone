# 共用頁型與路由

Flowinone 使用三個正式頁面模板，沿用 Flask/Jinja 與既有資料來源。

| 頁型 | 入口與內容 | 共用部分 |
| --- | --- | --- |
| `browse.html` | `/navigator/`、本機與 Chrome 資料夾、Eagle Folders／Tags／Smart Folders／Stream | 頁首、卡片、網格／連續模式、空與失敗狀態、分批載入 |
| `detail.html` | 本機／Eagle 圖片影片、`/resources/<id>/`、`/resources/<id>/versions/` | 頁首、返回條件、內容與版本檢視；預覽共用內容片段 |
| `manage.html` | `/library/`、`/resources/`、`/item_db`、`/catalog/duplicate-review/`、`/route-guide/`、`/ui-lab/` | 管理導覽、來源同步、匯入、索引、操作結果與比對工具 |

主導覽為「我的收藏／管理收藏」；外觀、頁面測試與 UI Lab 位於設定選單。Resource 版本差異歸入詳情，重複圖片檢閱歸入管理，保留原有 URL 與返回條件。

Navigator 查 Catalog；來源資料夾直接讀來源。來源離線或缺少資料時，共用頁面顯示狀態與準備入口。Grid／Slide 舊網址轉到共用瀏覽頁；`/navigator/?manage=1` 轉到 `/library/`。

## 路由導覽與維護

`/route-guide/` 按頁型列出 route、模板與可用範例。動態連結使用現有 Resource、本機檔案與 Eagle 索引，不產生虛構 ID；維護 POST 導回原操作頁提交表單。

新增頁面時，維持頁型與元件分工，並同步更新測試導覽及 [UI 路由登錄](ui-lab.md#維護位置)。媒體呈現與返回狀態見 [網格與連續模式](gallery-reading.md)。

自動測試涵蓋頁型歸屬、舊轉址、版本差異、重複群組操作與返回條件。執行方式見 [README 測試](../README.md#測試)。
