import os

from flask import Flask

from routes import register_routes, register_routes_debug


def create_app(test_config: dict | None = None) -> Flask:
    """Create one configured Flowinone web application.

    Background processing intentionally does not start here. WSGI servers may
    call this factory once per process, so worker ownership belongs to the
    separate ``python -m src.flowinone.workers`` runtime.
    """
    app = Flask(__name__)
    if test_config is not None:
        app.config.from_mapping(test_config)
    register_routes(app)
    register_routes_debug(app)
    return app


def main() -> None:
    app = create_app()
    debug = os.environ.get("FLOWINONE_DEBUG", "1").lower() not in {
        "0",
        "false",
        "no",
    }
    app.run(debug=debug, port=5894)


if __name__ == "__main__":
    main()
