# Eagle Web API v2 migration

Flowinone uses Eagle Web API v2 (`/api/v2`) exclusively. Eagle 4.0 Build 21 or
newer is required; comments and Smart Folder APIs require Build 22 or newer.

## What v2 adds

| Area | v1 | v2 |
|---|---|---|
| Search | Basic `item/list` keyword filters | Full-text `item/query` with AND, OR, NOT, exact phrases, and grouping |
| AI | None | Optional semantic text search, Base64 reverse-image search, and similar-item search |
| Items | Separate add-from-URL/path/bookmark endpoints | Unified single/batch `item/add`, field selection, richer filters, custom thumbnails, and item comments |
| Folders | List/create/rename/update | Paginated filtering plus create, update, recolor, reparent, and move to root |
| Smart folders | None | List/create/update/remove, query matching items, and inspect the supported rule schema |
| Tags | No documented management API | Paginated tags, recent/starred tags, rename, and merge |
| Tag groups | None | Full group CRUD and add/remove tag membership |
| Library/app | Basic metadata/history/switch/icon | Same capabilities under consistent v2 routes and response conventions |
| Transport | Local v1 routes | JSend responses, default pagination, optional LAN token authentication, and an API Playground |

Official references: [v2 introduction](https://developer.eagle.cool/web-api),
[v2 API reference](https://developer.eagle.cool/web-api/api/item), and
[legacy v1 reference](https://api.eagle.cool/).

## Flowinone architecture

- `src/eagle_api/client.py` owns HTTP, `/api/v2`, timeouts, LAN token support,
  pagination, and all supported endpoint families.
- `src/eagle_api/__init__.py` is a compatibility facade for the existing
  `EAGLE_*` calls. It translates v2's nested paginated response into the list
  shape currently consumed by Flowinone.
- `src/file_handler/eagle_integration.py` maps Eagle records into Flowinone's
  domain models. The templates and routes do not know Eagle's wire format.

The default server is `http://localhost:41595/api/v2`. For LAN access, set
`EAGLE_API_URL` to the remote v2 base URL and `EAGLE_API_TOKEN` to the token from
Eagle Preferences > Developer. Never commit that token.

## Features exposed in Flowinone

- Global Eagle search now uses full-text v2 query syntax.
- Smart Folders are available as dynamic collections.
- Detail-page recommendations prefer Eagle AI visual similarity when the AI
  Search plugin is ready, then automatically fall back to tags/folders.
- Destructive management methods (remove Smart Folder, merge tags, switch
  library, etc.) are available to Python callers but are not exposed as web UI
  actions.
