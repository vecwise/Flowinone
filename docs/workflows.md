# Flowinone workflows

## Gallery browse loop

1. 開啟 `/gallery/`。
2. 在同一 form 勾選本機、Eagle、書籤，可同時選多個。
3. 搜尋、item type、sort、view、seed 與 cursor 全部寫入 URL。
4. Gallery service 查詢 Catalog projection；不輸出 folder node。
5. 點 item 進入既有 image/video viewer 或外部 bookmark。
6. 返回時以前一個 Query URL 與 sessionStorage scroll key 恢復位置。
7. Open/favorite/view 與目前 query/scroll 保存為單人訊號及 Browse Session。
8. 任一來源同步失效時保留上一版 projection，其餘來源繼續工作。

## Catalog sync and discovery

1. `catalog-sync` 個別讀取 Resource、Chrome、Local、Eagle。
2. canonical identity 合併相同 URL，origin 保留來源主權。
3. FTS、tag、facet 與 detail URI 在同一 transaction 更新。
4. `catalog-relations-rebuild` 以 tag/title/source 建立每個 item 最多 12 個可解釋關係。
5. `catalog-collections-generate` 將 relation components 建成 draft Generated Collections；必須人工發布。

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

## Entry transition loop

允許 SCAN→LEARN/BUILD、LEARN→THINK/BUILD/WRITE、THINK→BUILD/WRITE、BUILD→WRITE。轉換建立新 Entry、`entry_links` 與雙向 audit event；來源 Entry 預設維持原狀，只有人工勾選才完成。

## Sidecar round-trip

`sidecars-export` 預設 dry-run，將人工 metadata 寫入目錄 `.flowinone.json`；`sidecars-audit` 驗證 schema/path；`sidecars-import` 以 portable UID／bounded content fingerprint 重新連結移動後檔案。absolute path、cache 與 AI 暫存不寫入 sidecar。

## Gallery → Knowledge 的明確轉換

媒體瀏覽不會自動建立知識。需要知識化時，使用者在 detail page 建立 typed Entry 或 Inspiration Collection；經整理後才建立 Wiki Draft / Output Asset。這是唯一允許的跨 domain 流程。
