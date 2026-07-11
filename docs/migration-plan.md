# Migration plan

## 已完成

### Phase 0 — Current-state audit

- 盤點 routes、DB、Eagle、Local、Chrome、Resource、Entry 與測試。
- 確認原問題不是資料表完全混用，而是首頁／導覽／folder-first IA 混用。

### Phase 1 — Domain boundary

- 建立 `src/flowinone/gallery/`。
- Gallery 與 Resource/Note schema 分離。
- 根路由維持 BUILD；Gallery 成為一級導航。

### Phase 2 — Flat multi-source Gallery MVP

- Local/Eagle/Bookmarks 同頁 checkbox。
- 搜尋、type、sort、view、seed、signed cursor。
- folder node 永不進 Gallery item response。
- 部分 source failure、empty state、responsive UI、scroll restore。

### Phase 3 — Knowledge OS gap closure

- WRITE mode。
- Resource↔Project/Mode context。
- Project Decision Log。
- Output Asset + typed provenance。
- BUILD project-linked resources / decisions。

## 已完成的下一階段

### Gallery canonical index / P0 Catalog

實際資料量已觸發條件，現已完成 canonical Catalog、origins、FTS、facets、keyset cursor、favorite/events/sessions 與 100k synthetic contract。

既有 `/api/gallery/items` contract 保留；Catalog 也索引 Resource，但 Gallery scope 不接收 Resource workflow state。

### P1/P2 workflow、Collections、recommendation、sidecar

- `entry_links` 與明確 transition API/UI 已完成。
- Manual/Smart/Generated Collections、item/collection relations、draft clustering 已完成。
- sidecar dry-run/audit/import/export 與 portable fingerprint 已完成。
- OCR/person artifact/provider 基礎已完成；模型評估與大量批次處理保留為後續作業。

## 後續依品質量測決定

### Model-assisted hybrid retrieval

觸發條件：FTS/metadata 無法在可接受時間內找到舊 Resource。

1. 先量測 keyword retrieval quality。
2. 在既有 relation/artifact 邊界加入 provider-neutral embedding implementation。
3. 加入 vector score，但保留 project/mode/manual priority 權重。
4. 不導入 Graph DB、Agent swarm、跨裝置同步或外部 connector。

## Migration safety

- 執行：`conda run -n py3.11 flask --app run resources-db-upgrade`。
- `0003_knowledge_os` 擴充 WRITE；`0004` 加 Catalog/workflows；`0005` 加查詢索引；`0006` 將 Entry provenance 升級為 typed multi-source link。
- 升級前備份 `data/`；不需移動 Eagle library、Chrome JSON 或 Obsidian vault。
