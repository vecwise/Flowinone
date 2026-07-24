import os

from flask import Flask


def create_app(test_config: dict | None = None) -> Flask:
    """Create one configured Flowinone web application.

    Background processing intentionally does not start here. WSGI servers may
    call this factory once per process, so worker ownership belongs to the
    separate ``python -m src.flowinone.workers`` runtime.
    """
    app = Flask(__name__)
    app.config.from_mapping(
        FLOWINONE_DEV_TOOLS=False,
        FLOWINONE_AUTO_MIGRATE=False,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    if test_config is not None:
        app.config.from_mapping(test_config)
    if app.config.get("TESTING") and not (
        test_config and "FLOWINONE_AUTO_MIGRATE" in test_config
    ):
        app.config["FLOWINONE_AUTO_MIGRATE"] = True

    # Delay source imports until explicit application construction. Importing
    # config, a worker, or a test module must never open a GUI or write files.
    import config

    if not app.config.get("TESTING"):
        config.ensure_db_routes(interactive=True)

    from routes import register_routes, register_routes_debug

    register_routes(app)
    if app.config.get("FLOWINONE_DEV_TOOLS"):
        register_routes_debug(app)
    return app


def main() -> None:
    app = create_app()
    debug = os.environ.get("FLOWINONE_DEBUG", "0").lower() not in {
        "0",
        "false",
        "no",
    }
    app.run(host="127.0.0.1", debug=debug, port=5894)


if __name__ == "__main__":
    main()
