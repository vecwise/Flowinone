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

### Machine-local thumbnail adapters

The public build includes YouTube plus Open Graph, Twitter Card, JSON-LD, and `image_src` metadata providers. Optional machine-specific rules can live in `.flowinone_local/thumbnail_provider.py`, which is ignored by Git and excluded from the Python package. The adapter may export `classify_url(url)` and `resolve(url, fetch_html)`; set `FLOWINONE_LOCAL_THUMBNAIL_PROVIDER` to load a different local file.

---

## 🧭 Navigation Cheat Sheet

| Menu item | What you get |
|-----------|---------------|
| **Home** | Eagle-powered discovery feed (random inspiration, video highlights, hot tags, similar clusters) |
| **DB Main** | Local/external media folders with Grid / Single / Linear views |
| **Chrome Bookmarks** | Full Chrome bookmark hierarchy |
| **YouTube Bookmarks** | Every YouTube link rendered with thumbnails |
| **EAGLE Folders / Tags / Stream** | Browse Eagle folders, tags, and real-time item streams |

Every browsing page supports:
- Three view toggles (Grid / Single / Linear)
- Alphabetical sorting (A→Z / Z→A)
- Folder drill-down; bookmarks/videos open directly (new tab or built-in player)

---

## 🧩 Architecture at a Glance

- `run.py` — bootstraps the Flask app and registers all routes.
- `routes.py` — routing layer combining Eagle, file-system, and Chrome bookmark endpoints.
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

PRs and issues are very welcome—let’s make Flowinone even better together!

---

## 📄 License

Distributed under the [MIT License](LICENSE).
