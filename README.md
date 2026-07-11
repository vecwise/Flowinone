# Flowinone — Unified Visual Knowledge Hub

Flowinone brings every visual collection you care about into a single, elegant interface:

- **Local media libraries** (NAS, external drives, internal assets)
- **Eagle App** folders, tags, and items via its API
- **Chrome bookmarks**, including auto-generated thumbnail walls for YouTube links

Add an AI-style discovery homepage, three browsing modes (grid / single / vertical strip), a built-in video player, and one-click Finder/Explorer shortcuts, and you get far more than bookmark management—it’s a full visual knowledge hub.

---

## 🔥 Highlights

- **Eagle Explorer**: Real-time access to Eagle folders, tags, and items. The home page curates newly-added and trending collections automatically.
- **Multi-source browsing**: Local disks, Eagle, Chrome bookmarks, and YouTube videos all surface in the same UI with quick view switching (Grid / Single / Linear).
- **Chrome + YouTube integration**:
  - Navigate the entire bookmark tree.
  - A dedicated YouTube page gathers every saved video and displays rich thumbnails.
- **Smart discovery**: The landing page offers random inspiration, video highlights, folder spotlights, popular tags, and AI-style similar clusters.
- **Thoughtful interactions**:
  - Bookmarks and YouTube links open in new tabs.
  - Local and Eagle videos play in the built-in viewer.
  - Finder/Explorer shortcuts jump straight to the real file system path.

### Resource Flow + Obsidian

Flowinone now also contains a local-first Resource Library instead of treating every
bookmark as a finished note:

```text
Chrome / URL
    → Resource Inbox
    → metadata + local thumbnail + extracted article/PDF/transcript
    → FTS5 search + reading workflow + optional AI summary
    → mixed-source Inspiration Collection
    → literature or synthesis Draft
    → Obsidian Markdown
```

- `/resources/` — searchable resource cards, reading states, tags, priority, archive/reject.
- `/inspiration/` — collections that may mix Resource, Eagle, and filesystem references.
- `/notes/` — multi-source Markdown drafts and conflict-safe Obsidian export.
- Resource metadata/workflow lives in `data/flowinone.sqlite3`.
- Large/raw content stays under `data/content/`; thumbnails remain filesystem cache.
- Eagle and local files remain authoritative in Eagle/filesystem. Collections store typed references and display snapshots only.
- AI is optional. Import, browsing, FTS, workflow, collections, and Obsidian export work without it.

### Entry System + BUILD Dashboard

The default `/` route now opens `/build/`: a deliberate resume surface rather than a
content feed. It uses Entry + Project records in the same local SQLite database:

```text
BUILD / THINK / LEARN / SCAN / RECOVER
    → Entry (target + context + state snapshot + next action)
    → Project context
    → Resource / Eagle / local-media / Collection / Draft target
```

- `/build/` — Continue, New, Think → Build, Current Project, recent work, blocked work.
- `/entries/` and `/projects/` — manage reusable context and stateful entry points.
- `/library/` — preserves the former content-discovery workbench as a secondary surface.
- Resource filters can be saved as entries; Resource, Collection, Draft, Eagle image, and
  local video pages can create LEARN or BUILD entries directly.
- Targets are validated local paths or allowed URI schemes; Flowinone never executes an
  arbitrary shell command from Entry metadata.

Read the combined product specification in [docs/flowinone_knowledge_flow_system.md](docs/flowinone_knowledge_flow_system.md).

---

## ⚙️ Getting Started

### 1. Clone the repo
```bash
git clone https://github.com/your-name/flowinone.git
cd flowinone
```

### 2. Use the project Conda environment
```bash
conda activate py3.11
python --version
python -m pip install -r requirements.txt
```

Flowinone uses the existing `py3.11` environment. Do not create a project `venv` or install dependencies with the system Python.

### 3. Configure paths
- `config.json` 內的 `DB_route_external` / `DB_route_internal` 為空時，啟動會跳出圖形化視窗讓你選資料夾，選完自動寫回 `config.json`。
- 若要手動設定或在 headless 環境執行：
  - 直接編輯 `config.json` 裡的 `DB_route_external` / `DB_route_internal`
  - 或預先設定 `FLOWINONE_HEADLESS=1` 並提供有效路徑，避免啟動時顯示 GUI。
- `CHROME_BOOKMARK_PATH`: Chrome bookmark JSON (預設為 macOS；Windows/Linux 請自行修改)

### 4. Launch the app
```bash
conda activate py3.11
python run.py
```
Visit `http://localhost:5894`.

### Verification and thumbnail sync

Use `conda run` for commands that must not depend on shell activation:

```bash
conda run -n py3.11 python -m compileall routes.py src
conda run -n py3.11 python -m pytest
conda run -n py3.11 flask --app run thumbnails-sync --missing
```

The thumbnail sync command also accepts `--force`, `--domain`, and `--limit`.

### Resource Library setup

Copy the relevant values from `.env.example` into your shell environment. At minimum,
the defaults work locally. To promote notes, set an existing vault explicitly:

```bash
export OBSIDIAN_VAULT_PATH="$HOME/Documents/My Vault"
export OBSIDIAN_TARGET_FOLDER="Resources/Digested"
```

Flowinone will create the target subfolder but will not create a vault, leave the vault,
or overwrite a different existing note without an explicit overwrite request.

Apply migrations and import the current Chrome profile:

```bash
conda run -n py3.11 flask --app run resources-db-upgrade
conda run -n py3.11 flask --app run entries-db-upgrade
conda run -n py3.11 flask --app run resources-sync --no-enqueue
```

`--no-enqueue` is recommended for the first large backfill. New resources can be enriched
from their detail page, or imported with jobs enabled. Process ready jobs manually with:

```bash
conda run -n py3.11 flask --app run resources-worker --limit 20
```

Maintenance commands:

```bash
conda run -n py3.11 flask --app run resources-rebuild-fts
conda run -n py3.11 flask --app run resources-retry-failed
conda run -n py3.11 flask --app run resources-export-mirrors
```

Equivalent standalone scripts are under `scripts/` for bookmark import, FTS rebuilding,
and failed-job retry.

If `LLM_BASE_URL` and `LLM_MODEL` are configured, content extraction automatically queues
AI summaries and AI tags. The prompt input is limited to extracted content and the provider
is shown in stored AI artifact provenance. `user_note` and user tags are never overwritten.

### Machine-local thumbnail adapters

The public build includes YouTube plus Open Graph, Twitter Card, JSON-LD, and `image_src` metadata providers. Optional machine-specific rules can live in `.flowinone_local/thumbnail_provider.py`, which is ignored by Git and excluded from the Python package. The adapter may export `classify_url(url)` and `resolve(url, fetch_html)`; set `FLOWINONE_LOCAL_THUMBNAIL_PROVIDER` to load a different local file.

---

## 🧭 Navigation Cheat Sheet

| Menu item | What you get |
|-----------|---------------|
| **BUILD (Home)** | Resume a current project or the next meaningful action without a content feed |
| **THINK / LEARN / SCAN / RECOVER** | Enter an intentional mode with its own lightweight Entry list |
| **Entries / Projects** | State-based action starts and their durable work contexts |
| **Explore Library** | The former Eagle-powered discovery workbench, now secondary to BUILD |
| **DB Main** | Local/external media folders with Grid / Single / Linear views |
| **Chrome Bookmarks** | Full Chrome bookmark hierarchy |
| **YouTube Bookmarks** | Every YouTube link rendered with thumbnails |
| **EAGLE Folders / Tags / Smart Folders / Stream** | Browse Eagle folders, dynamic smart folders, tags, and real-time item streams |
| **Resource Flow** | Process unread web resources, search extracted content, and promote notes |
| **Inspiration** | Curate mixed Eagle/local/resource references into collections |
| **Synthesis Notes** | Merge multiple sources and export Markdown to Obsidian |

Every browsing page supports:
- Three view toggles (Grid / Single / Linear)
- Alphabetical sorting (A→Z / Z→A)
- Folder drill-down; bookmarks/videos open directly (new tab or built-in player)

---

## 🧩 Architecture at a Glance

- `run.py` — bootstraps the Flask app and registers all routes.
- `routes.py` — routing layer combining Eagle, file-system, and Chrome bookmark endpoints.
- `src/eagle_api/` — isolated Eagle Web API v2 client and compatibility facade. Local Eagle
  uses `http://localhost:41595/api/v2` by default; set `EAGLE_API_URL` and
  `EAGLE_API_TOKEN` when connecting to Eagle over LAN.
- `src/flowinone/resource_library/` — Resource domain, repository/services, extractors,
  leased jobs, curation, Obsidian bridge, Flask blueprint, and worker.
- `src/flowinone/entry_system/` — Entry, Project, resume policy, safe target execution,
  Flask UI/API, state snapshots, and Think → Build flow.
- `migrations/` — Alembic schema history for the durable Resource DB, FTS projection,
  and Entry system.
- `file_handler.py` — core logic:
  - Normalizes all media metadata (local + Eagle).
  - Parses Chrome bookmarks, detects YouTube URLs, and builds recommendations.
  - Powers the AI-style homepage feed.
- `templates/` — Jinja2 templates, with `view_both.html` providing the three-view UI.
- `書籤瀏覽器_youtube專用/` — original YouTube bookmark scripts kept for reference (now integrated).

---

## 🛣️ Roadmap & Ideas

- [ ] Publish `requirements.txt` and a Dockerfile for painless deployment.
- [ ] Feed face-recognition results into Eagle metadata (auto “main character” tags).
- [ ] Deeper AI clustering (color palettes, subjects, layouts).
- [ ] Optional auth / remote access (currently optimized for LAN usage).

### Backup and restore

Stop Flowinone or make a consistent SQLite backup, then copy the whole `data/` directory.
That captures the Resource DB, extracted content, resource mirrors, and preview cache. The
Obsidian vault is intentionally outside Flowinone ownership and must be backed up separately.
After restore, run `resources-db-upgrade`; derived FTS rows can be repaired with
`resources-rebuild-fts` and thumbnail caches can be regenerated.

PRs and issues are very welcome—let’s make Flowinone even better together!

---

## 📄 License

Distributed under the [MIT License](LICENSE).
