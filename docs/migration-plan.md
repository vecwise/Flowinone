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

## 下一階段（有實際規模需求時）

### Gallery canonical index

觸發條件：Eagle >500 個 active candidates、跨來源排序明顯延遲、需要 favorite/event/session 或 10k+ continuous paging。

1. 建立獨立 Gallery SQLite migration。
2. 將 Local/Eagle/Bookmark adapter 轉為 ingestion source。
3. canonical item 不含 folder；folder 僅作 tag/source metadata。
4. 加入 FTS、facet、favorite、events、browse sessions。
5. cursor 改為 DB keyset pagination，保留現有 API contract。

### Knowledge hybrid retrieval

觸發條件：FTS/metadata 無法在可接受時間內找到舊 Resource。

1. 先量測 keyword retrieval quality。
2. 新增 provider-neutral embedding interface。
3. 加入 vector score，但保留 project/mode/manual priority 權重。
4. 不導入 Graph DB 或 Agent swarm，直到有不可由 relational links 解決的 use case。

## Migration safety

- 執行：`conda run -n py3.11 flask --app run resources-db-upgrade`。
- `0003_knowledge_os` 會以 Alembic batch recreate 擴充 SQLite Entry check constraint。
- 升級前備份 `data/`；不需移動 Eagle library、Chrome JSON 或 Obsidian vault。
