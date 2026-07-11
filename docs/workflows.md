# Flowinone workflows

## Gallery browse loop

1. 開啟 `/gallery/`。
2. 在同一 form 勾選本機、Eagle、書籤，可同時選多個。
3. 搜尋、item type、sort、view、seed 與 cursor 全部寫入 URL。
4. Gallery service 從每個來源取得扁平 item；不輸出 folder node。
5. 點 item 進入既有 image/video viewer 或外部 bookmark。
6. 返回時以前一個 Query URL 與 sessionStorage scroll key 恢復位置。
7. 任一來源失效時顯示可恢復訊息，其餘來源繼續工作。

## Resource to action loop

1. URL/Chrome bookmark 建立 Raw Resource。
2. metadata/content extractor 與可選 AI 產生 Brief 欄位。
3. 使用者在 Resource detail 指定 Project、Mode 與 relevance。
4. BUILD dashboard 或 `/api/retrieval` 只提取符合情境的 Resource。
5. 使用者可建立 Literature Note、BUILD/LEARN/WRITE Entry。
6. WRITE 建立 Output Asset，並用 `asset_sources` 保留 Resource/Note/Decision/Entry 來源。

## Decision loop

1. Project detail 記錄 question、decision、rationale、alternatives。
2. BUILD 顯示目前 Project 最近的 Decision。
3. WRITE Output Asset 可以引用 Decision ID。
4. Decision 只透過明確 user form/API 寫入；AI enrichment 不更新它。

## Gallery → Knowledge 的明確轉換

媒體瀏覽不會自動建立知識。需要知識化時，使用者在 detail page 建立 typed Entry 或 Inspiration Collection；經整理後才建立 Wiki Draft / Output Asset。這是唯一允許的跨 domain 流程。
