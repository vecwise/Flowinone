import os

from flask import Flask
from routes import register_routes, register_routes_debug
from src.flowinone.resource_library.worker import start_background_resource_worker
from src.file_handler.thumbnails.worker import start_background_worker

app = Flask(__name__)

# 註冊所有路由
register_routes(app)
register_routes_debug(app)

if __name__ == "__main__":
    debug = os.environ.get("FLOWINONE_DEBUG", "1").lower() not in {"0", "false", "no"}
    # Werkzeug's debug parent only watches files. The serving child owns the worker.
    if not debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        start_background_worker()
        start_background_resource_worker()
    app.run(debug=debug, port=5894)
