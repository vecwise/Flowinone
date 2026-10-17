# 多 UI 比較

從首頁的「UI Lab」進入 `/ui-lab/`。保留目前基準 `baseline`，另有視覺策展 `curator`、閱讀優先 `reader`、素材工作台 `workbench`。四套 UI 仍共用 `browse.html`、`detail.html`、`manage.html` 三個頁型，Lab 本身使用管理模板。

每套提供瀏覽、詳情、管理三個新分頁連結，共 12 個比較入口。同一頁型使用同一個網址條件／真實樣本，僅 `ui` 不同；詳情優先選 Resource，其次是可用媒體，沒有樣本時明示準備入口。也可用任一方案開啟進 Lab 前的頁面。

| UI | 瀏覽 | 詳情 | 管理 |
| --- | --- | --- | --- |
| 目前基準 | 原有網格／閱讀模式 | 原有主內容與接著看 | 原有來源與維護分區 |
| 視覺策展 | 大幅影像、寬鬆網格 | 寬幅內容、相關項目置底橫排 | 大型來源展卡、寬鬆分區 |
| 閱讀優先 | 文字書目卡、舒適字距與行距 | 集中行寬、相關內容置底 | 單欄來源與表單 |
| 素材工作台 | 桌面側欄篩選、密集網格；主題捷徑保留，精簡推薦摘要 | 主內容與資訊並排 | 桌面側邊功能列、緊湊來源狀態 |

## 切換與狀態

頁面上方及收藏詳情預覽內有介面選單。切換會更新根元素的 `data-ui`，不重新載入或重建內容，因此未送出文字、勾選、檔案輸入、展開狀態、預覽與媒體元素仍留在原頁面。不同版面會改變內容位置與頁面高度。

選擇記在 `?ui=...`，保留其他參數（包含重複的來源／標籤、游標、seed、context、view）、錨點及內層 `return_to`。目前 UI 隨一般站內連結、GET／POST 表單及 HTML 轉址繼續；Lab 的固定比較連結不被覆寫。沒有 `ui` 時使用基準，未知值也回到基準。不寫全域 UI 偏好或 cookie，讓分頁可以獨立比較。

`ui` 不屬於 CatalogQuery，不進查詢簽章或 API payload。靜態檔、媒體串流、資源附件、API 與外部連結不附加 UI 參數。跨來源 URL 不能作為 Lab 返回頁。

## 維護位置

- `src/flowinone/web/ui_variants.py`：四套方案登錄、伺服器初始選擇、HTML 連結與轉址、Lab 樣本。
- `static/css/ui_variants.css`：方案 token、三頁型版面與響應式規則；基準沿用既有 CSS。
- `static/js/ui_switcher.js`：即時切換、URL 與表單同步、動態預覽連結。
- `templates/components/_ui_picker.html`：上方工具列與預覽共用的選單。
- `templates/components/_ui_lab.html`：比較卡與 12 個真實入口。

新增正式 HTML route 時，同步補上 `UI_ENDPOINTS`、前端 `isPagePath` 與測試導覽；不要把 API 或檔案路由加入呈現層名單。新增 UI 優先使用 token 與三頁型的布局規則，保留共用元件的 data 屬性與操作契約。
