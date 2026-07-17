# External-spec integration decision

This document re-evaluates the five supplied design notes after the renderer-only decision:

- `flowinone_resource_library_plan.md`
- `flowinone_entrance_system_build_dashboard.md`
- `from網頁flowinone_knowledge_os_codex_spec.md`
- `from網頁flowinone_gallery_browsing_spec.md`
- `flowinone_retention_design_patterns.md`

## Kept and active

| Requirement | Result |
| --- | --- |
| Gallery as a dedicated domain | Kept. Gallery is the product home, not an inspiration/task entry point. |
| Local/Eagle/Bookmark flat browsing | Kept. Sources are selectable together; folders are metadata, never cards. |
| Cross-source Catalog and true search | Kept. FTS, facets, tag ANY/ALL, stable random seed, query-bound keyset cursor. |
| Canonical URL with preserved origins | Kept. Bookmark and Resource may merge for search but open through their selected origin. |
| Favorites, views, bounded sessions | Kept. These are browsing signals, not a knowledge workflow. |
| Resource URL import/extraction | Kept. It supplies rendered metadata and text to Resources and Catalog. |
| Sidecar portability | Kept. Local metadata remains portable without leaking absolute paths. |
| Explainable related items | Kept. Initial relations use tags/title/source metadata and return reasons. |

## Deliberately removed or deferred

| Requirement | Decision |
| --- | --- |
| BUILD/THINK/LEARN/SCAN/RECOVER/WRITE, Entries, Projects, Decisions, Outputs | Removed from this repository. |
| Notes, Raw → Wiki, Obsidian export/sync | Removed or out of scope; knowledge capture belongs to another repository. |
| Manual/Smart/Generated Collections | Removed for now. Gallery is already the visual browsing surface and is not relabelled as inspiration. |
| Cross-device sync, OneTab/Keep/Notion/social connectors | Out of scope. |
| CLIP/FAISS mandatory first version | Deferred. Relation/provider interfaces are enough until a benchmark proves a model helps. |
| OCR/person identification | Deferred implementation; only optional artifact/person schema foundations remain. |

## Conflict rules

- Catalog is a query projection, never a replacement for Eagle, filesystem, Chrome, or Resource authority.
- AI must not silently change user tags or source metadata.
- No autoplay, forced next item, or unlimited feed.
- Gallery remains Gallery. It does not create an inspiration collection, note, Entry, or task as a side effect of browsing.
