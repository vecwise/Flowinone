import os
import platform
import random
import subprocess
from functools import wraps
from urllib.parse import unquote
from flask import Flask, render_template, abort, send_from_directory, request, redirect, url_for, jsonify, g, send_file, current_app
from src.file_handler import (
    AccessDenied,
    BookmarkNotFound,
    BookmarkError,
    ExternalServiceError,
    FolderNotFound,
    MediaNotFound,
    get_all_folders_info,
    get_folder_images,
    get_image_details,
    get_video_details,
    get_eagle_folders,
    get_eagle_images_by_folderid,
    get_eagle_images_by_tag,
    get_eagle_tags,
    search_eagle_items,
    get_eagle_stream_items,
    get_chrome_bookmarks,
    get_chrome_youtube_bookmarks,
    get_eagle_image_details,
    get_eagle_video_details,
    get_subfolders_info,
    is_eagle_available,
    update_item_database,
    has_chrome_bookmarks,
    has_db_main,
)
from config import DB_route_internal, DB_route_external

SYSTEM_NAME = platform.system()
IS_MACOS = SYSTEM_NAME == "Darwin"
IS_WINDOWS = SYSTEM_NAME == "Windows"


def _path_is_within_roots(target_path, roots):
    """Ensure the requested path stays inside one of the configured roots."""
    try:
        normalized_target = os.path.abspath(target_path)
        for root in roots:
            if not root:
                continue
            normalized_root = os.path.abspath(root)
            if os.path.commonpath([normalized_target, normalized_root]) == normalized_root:
                return True
    except ValueError:
        return False
    return False


def _open_in_file_manager(target_path):
    """Open a folder in the host file manager."""
    if IS_MACOS:
        subprocess.Popen(["open", target_path])
    elif IS_WINDOWS:
        os.startfile(target_path)  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", target_path])


def _to_dict(obj):
    """Convert dataclasses or plain objects to dict for templates."""
    return obj.to_dict() if hasattr(obj, "to_dict") else obj


def _serialize_payload(metadata, data):
    meta_dict = _to_dict(metadata)
    items = [_to_dict(item) for item in data]
    return meta_dict, items


def _serialize_detail(detail):
    return _to_dict(detail)


def _compute_feature_flags():
    eagle_available = is_eagle_available()
    chrome_available = has_chrome_bookmarks()
    db_available = has_db_main()
    return {
        "eagle": eagle_available,
        "chrome": chrome_available,
        "youtube": chrome_available,
        "db": db_available
    }


def _get_feature_flags():
    if not hasattr(g, "feature_flags"):
        g.feature_flags = _compute_feature_flags()
    return g.feature_flags


def require_feature(flag_name):
    """Decorator to enforce feature availability on a route."""
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            flags = _get_feature_flags()
            if not flags.get(flag_name):
                abort(404)
            return view_func(*args, **kwargs)
        return wrapped
    return decorator


def _normalize_current_url():
    """Strip the trailing ? from request.full_path to keep return_to clean."""
    current_url = request.full_path
    if current_url and current_url.endswith('?'):
        return current_url[:-1]
    return current_url


def _attach_detail_urls(items, current_url):
    """Attach detail URLs (with return_to) to media items in-place."""
    for item in items:
        item_id = item.get("id")
        if not item_id:
            continue
        if item.get("media_type") == "video":
            item["url"] = url_for("view_eagle_video", item_id=item_id, return_to=current_url)
        elif item.get("media_type") == "image":
            item["url"] = url_for("view_eagle_image", item_id=item_id, return_to=current_url)
    return items


def _render_media_view(template_name, folder_path, source=None):
    """Shared renderer for folder-based media views."""
    try:
        if source is None:
            metadata, data = get_folder_images(folder_path)
        else:
            metadata, data = get_folder_images(folder_path, source)
    except AccessDenied:
        abort(403)
    except (FolderNotFound, MediaNotFound, BookmarkNotFound):
        abort(404)
    except ExternalServiceError as exc:
        abort(500, description=str(exc))
    metadata_dict, data_list = _serialize_payload(metadata, data)
    return render_template(template_name, metadata=metadata_dict, data=data_list)


def _build_index_context(eagle_enabled):
    """Prepare data for the landing page, with graceful fallback on errors."""
    context = {
        "hero_item": None,
        "featured_media": [],
        "random_images": [],
        "random_videos": [],
        "random_folders": [],
        "eagle_tags": [],
        "curated_clusters": [],
        "fallback_heading": "Welcome to Flowinone",
        "fallback_message": "Connect Eagle, a media library, or Chrome bookmarks to start exploring."
    }
    if not eagle_enabled:
        return context

    try:
        hero_payload = [_to_dict(item) for item in get_eagle_stream_items(offset=0, limit=10)]
        if hero_payload:
            context["hero_item"] = hero_payload[0]
            context["featured_media"] = hero_payload[1:5]

        image_payload = [_to_dict(item) for item in get_eagle_stream_items(offset=20, limit=40)]
        image_only = [item for item in image_payload if item.get("media_type") == "image"]
        video_only = [item for item in image_payload if item.get("media_type") == "video"]

        if image_only:
            context["random_images"] = random.sample(image_only, min(8, len(image_only)))
        if video_only:
            context["random_videos"] = random.sample(video_only, min(4, len(video_only)))

        _, folder_data_raw = get_eagle_folders()
        folder_data = [_to_dict(item) for item in folder_data_raw]
        if folder_data:
            context["random_folders"] = random.sample(folder_data, min(6, len(folder_data)))

        _, tag_data = get_eagle_tags()
        context["eagle_tags"] = random.sample(tag_data, min(20, len(tag_data))) if tag_data else []

        if folder_data:
            clusters_map = {}
            for folder in folder_data:
                prefix = folder.get("name", "").split()[0]
                if not prefix:
                    continue
                clusters_map.setdefault(prefix, []).append(folder)

            curated_clusters = []
            for key, items in clusters_map.items():
                if len(items) < 2:
                    continue
                curated_clusters.append({
                    "title": f"{key} 精選合集",
                    "items": items[:5]
                })

            random.shuffle(curated_clusters)
            context["curated_clusters"] = curated_clusters[:3]

    except Exception:
        current_app.logger.exception("Failed to build index context")

    return context


def register_routes_debug(app):
    @app.route('/debug/')
    def debug_print():
        # df_folders_info = EG.EAGLE_get_folders_df()
        # print(df_folders_info.shape)
        # print(df_folders_info.columns)
        df_to_post = [
            {'author': 'Lynn','title': 'Blog Post 1','content': 'First post content','date_posted': 'September 3, 2018'},
            {'author': 'Lydia','title': 'Blog Post 2','content': 'Second post content','date_posted': 'September 6, 2018'}
            ]
        return render_template('test_arg.html', title='All in One', df_to_post=df_to_post)

def register_routes(app):
    """
    註冊 Flask 路由
    """

    _register_context_processors(app)
    _register_index_routes(app)
    _register_filesystem_routes(app)
    _register_folder_routes(app)
    _register_item_db_routes(app)
    _register_chrome_routes(app)
    _register_eagle_routes(app)
    _register_media_routes(app)


def _register_context_processors(app):
    @app.context_processor
    def inject_feature_flags():
        return {"feature_flags": _get_feature_flags()}


def _register_index_routes(app):
    @app.route('/')
    def index():
        """探索 Eagle 資料庫的首頁推薦"""
        flags = _get_feature_flags()
        if not flags["eagle"]:
            if flags["db"]:
                return redirect(url_for('view_collections'))
            if flags["chrome"]:
                return redirect(url_for('view_chrome_root'))
            context = _build_index_context(False)
            return render_template('index.html', **context)

        context = _build_index_context(True)
        return render_template('index.html', **context)


def _register_filesystem_routes(app):
    @app.route('/open_path/')
    def open_filesystem_path():
        """Open the requested path in the local file manager."""
        raw_path = request.args.get('path')
        if not raw_path:
            abort(400)

        decoded_path = os.path.abspath(unquote(raw_path))
        if not os.path.exists(decoded_path):
            abort(404)

        allowed_roots = [DB_route_external, DB_route_internal]
        if not _path_is_within_roots(decoded_path, allowed_roots):
            abort(403)

        target_directory = decoded_path if os.path.isdir(decoded_path) else os.path.dirname(decoded_path)
        if not target_directory:
            abort(404)

        try:
            _open_in_file_manager(target_directory)
        except Exception as exc:
            abort(500, description=f"Failed to open path: {exc}")

        return redirect(request.referrer or url_for('index'))


def _register_folder_routes(app):
    @app.route('/both/<path:folder_path>/')
    def view_both(folder_path):
        """
        取得指定資料夾內的所有圖片
        src: internal or external
        """
        source = request.args.get('src', 'external')
        return _render_media_view('view_both.html', folder_path, source)

    @app.route('/grid/<path:folder_path>/')
    def view_grid(folder_path):
        """取得指定資料夾內的所有圖片（Grid 模式）"""
        return _render_media_view('view_grid.html', folder_path)

    @app.route('/slide/<path:folder_path>/')
    def view_slide(folder_path):
        """取得指定資料夾內的所有圖片（Slide 模式）"""
        return _render_media_view('view_slide.html', folder_path)

    @app.route('/collections/')
    @require_feature("db")
    def view_collections():
        """顯示 DB main 目錄，使用 view_both 版型"""
        source = request.args.get('src', 'external')
        try:
            metadata, data = get_all_folders_info(source)
        except AccessDenied:
            abort(403)
        except (FolderNotFound, MediaNotFound, BookmarkNotFound):
            abort(404)
        except ExternalServiceError as exc:
            abort(500, description=str(exc))
        metadata_dict, data_list = _serialize_payload(metadata, data)
        return render_template('view_both.html', metadata=metadata_dict, data=data_list)


def _register_item_db_routes(app):
    @app.route('/update_db')
    @require_feature("db")
    def update_item_db_route():
        """Crawl tagged folders and persist items into the central DB."""
        base_path = request.args.get("base") or DB_route_external
        try:
            result = update_item_database(base_path)
        except FileNotFoundError:
            abort(404, description="指定的資料夾不存在，請確認 DB_route_external。")
        except Exception as exc:
            abort(500, description=f"更新 item DB 失敗: {exc}")

        wants_json = request.args.get("format") == "json" or request.accept_mimetypes.best == "application/json"
        if wants_json:
            return jsonify(result)

        return render_template(
            "update_db_result.html",
            title="Update Item DB",
            result=result,
        )


def _register_chrome_routes(app):
    @app.route('/chrome/')
    @require_feature("chrome")
    def view_chrome_root():
        """預設顯示書籤列 (bookmark_bar)。"""
        focus_mode = request.args.get('mode')
        if focus_mode:
            return redirect(url_for('view_chrome_folder', folder_path='bookmark_bar', mode=focus_mode))
        return redirect(url_for('view_chrome_folder', folder_path='bookmark_bar'))

    @app.route('/chrome/<path:folder_path>/')
    @require_feature("chrome")
    def view_chrome_folder(folder_path):
        """瀏覽 Chrome 書籤資料夾。"""
        focus_mode = request.args.get('mode')
        try:
            metadata, data = get_chrome_bookmarks(folder_path, focus_mode)
        except BookmarkNotFound:
            abort(404)
        except BookmarkError as exc:
            abort(500, description=str(exc))
        except ExternalServiceError as exc:
            abort(500, description=str(exc))
        metadata_dict, data_list = _serialize_payload(metadata, data)
        return render_template('view_both.html', metadata=metadata_dict, data=data_list)

    @app.route('/chrome_youtube/')
    @require_feature("youtube")
    def view_chrome_youtube():
        """專門顯示 YouTube 書籤"""
        try:
            metadata, data = get_chrome_youtube_bookmarks()
        except BookmarkNotFound:
            abort(404)
        except BookmarkError as exc:
            abort(500, description=str(exc))
        except ExternalServiceError as exc:
            abort(500, description=str(exc))
        metadata_dict, data_list = _serialize_payload(metadata, data)
        return render_template('view_both.html', metadata=metadata_dict, data=data_list)


def _register_eagle_routes(app):
    @app.route('/EAGLE_folders/')
    @require_feature("eagle")
    def list_all_eagle_folder():
        """列出所有 Eagle 資料夾，並符合 EAGLE API 樣式"""
        try:
            metadata, data = get_eagle_folders()
        except ExternalServiceError as exc:
            abort(500, description=str(exc))
        metadata_dict, data_list = _serialize_payload(metadata, data)
        return render_template('view_both.html', metadata=metadata_dict, data=data_list)

    @app.route('/EAGLE_tags/')
    @require_feature("eagle")
    def list_eagle_tags():
        """列出 Eagle 中的所有標籤並提供連結"""
        try:
            metadata, tags = get_eagle_tags()
        except ExternalServiceError as exc:
            abort(500, description=str(exc))
        metadata_dict = _to_dict(metadata)
        return render_template("eagle_tags.html", metadata=metadata_dict, tags=tags)

    @app.route('/EAGLE_folder/<eagle_folder_id>/')
    @require_feature("eagle")
    def view_eagle_folder(eagle_folder_id):
        """顯示指定 Eagle 資料夾 ID 下的所有圖片"""
        try:
            metadata, data = get_eagle_images_by_folderid(eagle_folder_id)
            subfolders = get_subfolders_info(eagle_folder_id)
            data = subfolders + data
        except MediaNotFound:
            abort(404)
        except ExternalServiceError as exc:
            abort(500, description=str(exc))

        metadata_dict, data_list = _serialize_payload(metadata, data)
        current_url = _normalize_current_url()
        _attach_detail_urls(data_list, current_url)

        return render_template('view_both.html', metadata=metadata_dict, data=data_list)

    @app.route('/EAGLE_tag/<target_tag>/')
    @require_feature("eagle")
    def view_images_by_tag(target_tag):
        """
        顯示所有帶有指定標籤的圖片，並符合 EAGLE API 格式。

        Args:
            target_tag (str): 要查詢的標籤。

        Returns:
            渲染的 HTML 頁面，顯示所有具有該標籤的圖片。
        """
        try:
            metadata, data = get_eagle_images_by_tag(target_tag)
        except ExternalServiceError as exc:
            abort(500, description=str(exc))
        metadata_dict, data_list = _serialize_payload(metadata, data)

        current_url = _normalize_current_url()
        _attach_detail_urls(data_list, current_url)

        return render_template('view_both.html', metadata=metadata_dict, data=data_list)

    @app.route('/search')
    @require_feature("eagle")
    def search_eagle():
        """使用 Eagle API 搜尋並顯示結果。"""
        keyword = request.args.get('query', '').strip()
        if not keyword:
            return redirect(request.referrer or url_for('index'))

        try:
            metadata, data = search_eagle_items(keyword)
        except ExternalServiceError as exc:
            abort(500, description=str(exc))
        metadata_dict, data_list = _serialize_payload(metadata, data)

        current_url = _normalize_current_url()
        _attach_detail_urls(data_list, current_url)

        return render_template('view_both.html', metadata=metadata_dict, data=data_list)

    @app.route('/EAGLE_stream/')
    @require_feature("eagle")
    def eagle_stream():
        """顯示無限滾動串流頁面"""
        return render_template('eagle_stream.html')

    @app.route('/api/EAGLE_stream/')
    @require_feature("eagle")
    def eagle_stream_data():
        """提供 Eagle 串流頁面使用的資料"""
        try:
            offset = int(request.args.get('offset', 0))
            limit = int(request.args.get('limit', 30))
        except ValueError:
            abort(400, description="Invalid offset or limit")

        limit = max(1, min(limit, 60))
        offset = max(0, offset)

        try:
            data = get_eagle_stream_items(offset=offset, limit=limit)
        except ExternalServiceError as exc:
            abort(500, description=str(exc))
        data = [_to_dict(item) for item in data]
        items = []
        for item in data:
            item_id = item.get("id")
            if not item_id:
                continue
            if item.get("media_type") == "video":
                detail_url = url_for("view_eagle_video", item_id=item_id)
            else:
                detail_url = url_for("view_eagle_image", item_id=item_id)

            items.append({
                "id": item_id,
                "name": item.get("name"),
                "thumbnail_route": item.get("thumbnail_route"),
                "detail_url": detail_url,
                "media_type": item.get("media_type"),
                "ext": item.get("ext")
            })

        return jsonify({
            "items": items,
            "nextOffset": offset + len(items)
        })

    @app.route('/EAGLE_video/<item_id>/')
    @require_feature("eagle")
    def view_eagle_video(item_id):
        """顯示 Eagle 影片的詳細資訊與播放器頁面"""
        try:
            metadata, video = get_eagle_video_details(item_id)
        except MediaNotFound:
            abort(404)
        except ExternalServiceError as exc:
            abort(500, description=str(exc))
        metadata_dict = _to_dict(metadata)
        video_dict = _serialize_detail(video)
        return_to = request.args.get("return_to")
        if return_to:
            video_dict["parent_url"] = return_to
        else:
            video_dict["parent_url"] = request.referrer or url_for("index")
        return render_template('video_player.html', metadata=metadata_dict, video=video_dict)

    @app.route('/EAGLE_image/<item_id>/')
    @require_feature("eagle")
    def view_eagle_image(item_id):
        """顯示 Eagle 圖片的詳細資訊與展示頁面"""
        try:
            metadata, image = get_eagle_image_details(item_id)
        except MediaNotFound:
            abort(404)
        except ExternalServiceError as exc:
            abort(500, description=str(exc))
        metadata_dict = _to_dict(metadata)
        image_dict = _serialize_detail(image)
        return_to = request.args.get("return_to")
        if return_to:
            image_dict["parent_url"] = return_to
        else:
            image_dict["parent_url"] = request.referrer or url_for("index")
        return render_template('image_viewer.html', metadata=metadata_dict, image=image_dict)


def _register_media_routes(app):
    @app.route('/serve_image/<path:image_path>')
    def serve_image_by_full_path(image_path):
        """提供靜態圖片服務"""
        # Flask <path> 會吞掉開頭的「/」，補回來避免相對路徑被解讀成專案內路徑。
        decoded = unquote(image_path)
        if not decoded.startswith("/"):
            decoded = "/" + decoded
        decoded_path = os.path.abspath(decoded)

        if not os.path.isfile(decoded_path):
            abort(404)

        if IS_MACOS:
            directory, filename = os.path.split(decoded_path)
            return send_from_directory(directory, filename)

        return send_file(decoded_path)

    @app.route('/video/<path:video_path>')
    def view_video(video_path):
        """顯示影片播放頁面"""
        source = request.args.get('src', 'external')
        try:
            metadata, video = get_video_details(video_path, source)
        except AccessDenied:
            abort(403)
        except (FolderNotFound, MediaNotFound):
            abort(404)
        metadata_dict = _to_dict(metadata)
        video_dict = _serialize_detail(video)
        return render_template('video_player.html', metadata=metadata_dict, video=video_dict)

    @app.route('/image/<path:image_path>')
    def view_image(image_path):
        """顯示圖片展示頁面"""
        source = request.args.get('src', 'external')
        try:
            metadata, image = get_image_details(image_path, source)
        except AccessDenied:
            abort(403)
        except (FolderNotFound, MediaNotFound):
            abort(404)
        metadata_dict = _to_dict(metadata)
        image_dict = _serialize_detail(image)
        return render_template('image_viewer.html', metadata=metadata_dict, image=image_dict)
