# 外部規格整合與完成度裁決

本文件重新對照五份外部設計稿與目前程式：

- `flowinone_resource_library_plan.md`
- `flowinone_entrance_system_build_dashboard.md`
- `from網頁flowinone_knowledge_os_codex_spec.md`
- `from網頁flowinone_gallery_browsing_spec.md`
- `flowinone_retention_design_patterns.md`

## 完成度矩陣

| 規格主題 | 狀態 | 現行實作／裁決 |
| --- | --- | --- |
| Gallery 與 Knowledge OS 分離 | 已完成 | `/gallery/` 是扁平瀏覽 domain；不帶 Resource workflow state，也不會因 canonical merge 自動跳入 Resource |
| Local／Eagle／Bookmark 同頁瀏覽 | 已完成 | 來源可複選；folder 只作 metadata，不是 Gallery item |
| Gallery Query State | 已完成 | q、source、type、tags ANY/ALL、日期、時長、favorite、unviewed、sort、seed、cursor、view |
| 跨來源搜尋與 facets | 已完成 | Catalog projection + FTS5；`/search/` 與 `/api/catalog/*` |
| SQLite keyset cursor | 已完成 | cursor 綁定 query signature；固定 random seed 可重現 |
| Browse Session／Continue Browsing | 已完成 | 保存 query、seed/cursor、focused item、scroll 與最後活動時間 |
| Favorite／view events | 已完成 | `item_user_state` 與 `catalog_events` 支援 favorite、hide、open/view、collection action |
| Resource Library | 已完成基礎 | 匯入、去重、extractor、縮圖、FTS、選配 AI、狀態、Ask AI、Obsidian export |
| BUILD／THINK／LEARN／SCAN／RECOVER／WRITE | 已完成 | 六種模式、Project、Decision、Output Asset 均使用 shared SQLite |
| 正式 Entry 轉換 | 已完成 | transition matrix、typed multi-source provenance link/event、選配完成來源；AI 不自動改狀態 |
| Manual／Smart／Generated Collection | 已完成基礎 | 沿用既有 Collection；Smart 保存 query，Generated 是需確認的固定 draft |
| Similar Item／Collection | 已完成基礎 | explainable relation graph；先用 tag、title、source、collection、Eagle metadata |
| Sidecar portable metadata | 已完成基礎 | `.flowinone.json` manifest、原子輸出、dry-run/diff、UID/fingerprint 搬移重連 |
| OCR artifact | 基礎架構完成 | versioned artifact 與 provider interface；選配 Tesseract，未強制安裝 runtime |
| 匿名 Person Cluster | 基礎架構完成 | person、item relation、人工命名 API；face provider/embedding benchmark 尚待後續 |
| 自動 Raw → Wiki | 排除 | 屬於另一個 repo；Flowinone 只保留明確人工 promote/export |
| 跨裝置同步 | 排除 | local-first、單人資料模型 |
| OneTab／Keep／Notion／社群 connector | 排除 | 暫不加入來源或雙向同步 |
| Obsidian Vault 全文索引／雙向同步 | 排除 | 只做 conflict-safe export，Vault 不進 Catalog |

Gallery 的 canonical item 只用於搜尋與去重；開啟時必須依使用者目前選取的 origin：Bookmark 開原始 URL、Local／Eagle 開來源 viewer、Resource 才開 Resource detail。Gallery 瀏覽、相似項目與 favorite 不會自動建立 Inspiration／Collection。

## 衝突裁決

| 衝突 | 決策 |
| --- | --- |
| FastAPI + React 新堆疊 vs 現有 Flask + Jinja | 不全面重寫；以 package、service、API contract 分離 domain |
| 建立新的 Gallery 主資料表 vs Local/Eagle/Chrome/Resource authoritative source | Catalog 是可重建 projection；來源仍是 authority，失敗時保留上一版並標 stale |
| Gallery description/tag 等於知識筆記 | 禁止；Gallery metadata 不冒充 user note，轉成 Entry/Resource 必須明確操作 |
| Resource 與 Chrome 同 URL 是兩個 item | Catalog 合併 canonical URL，但保留兩個 origin 與來源 provenance |
| 建立第三套 collection schema | 禁止；擴充既有 `collections`／`collection_items` |
| AI 自動改人工狀態或覆蓋 tag | 禁止；AI artifact、source tag、user tag 分開保存 |
| 自動推薦 feed／autoplay | 不採用；使用可解釋 rails、Load More 與有邊界 Browse Session |
| 第一版強制 CLIP／FAISS | 不採用；relation/provider-neutral interface 先行，再以 benchmark 決定模型 |

## 合法的 domain 交會

```text
Local / Eagle / Bookmark / Resource authority
                   ↓ rebuildable sync
              Catalog projection
          ↙          ↓             ↘
      Gallery      Search       Collections
          \          |             /
           explicit Entry transition
                      ↓
 BUILD / THINK / LEARN / SCAN / RECOVER / WRITE
                      ↓ explicit export
                   Obsidian
```

Gallery item 不會自動成為 Resource 或 Note。合法交會是 typed reference、加入 Collection、建立 Entry、建立有 provenance 的 Output Asset，或由使用者明確匯出 Draft Note。

## 下一步仍值得做

1. 在實際媒體樣本上 benchmark OCR 與 face provider，選定 runtime 後才啟用批次 job。
2. 補人物 cluster 的 merge/split UI；目前已有匿名 cluster、命名與 item link 基礎。
3. 以真實使用事件調整推薦權重，但維持每筆 reason 與 hidden/favorite 控制。
4. Eagle 超過目前 adapter 批次上限時做完整增量同步與同步進度 UI。

## 明確不做

- 跨裝置同步、自動 Raw → Wiki、Notion/Keep/OneTab/社群 connectors。
- Obsidian 全文索引或雙向同步。
- Graph DB、Agent swarm、多人帳號、社交功能。
- autoplay、強制下一個、無限制 infinite scroll。
- AI 靜默覆寫 user tags、Decision、Collection 或 sidecar。
