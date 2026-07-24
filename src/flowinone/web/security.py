"""Local-app request boundary and CSRF protection."""

from __future__ import annotations

import hmac
import os
import secrets
from urllib.parse import urlsplit

from flask import Flask, abort, request, session


UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _configured_hosts(app: Flask) -> set[str]:
    configured = app.config.get("FLOWINONE_ALLOWED_HOSTS") or os.environ.get(
        "FLOWINONE_ALLOWED_HOSTS", ""
    )
    extra = {
        value.strip().lower()
        for value in str(configured).split(",")
        if value.strip()
    }
    return set(LOOPBACK_HOSTS) | extra


def _csrf_token() -> str:
    token = session.get("_flowinone_csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_flowinone_csrf"] = token
    return str(token)


def _same_origin() -> bool:
    origin = request.headers.get("Origin", "").strip()
    if not origin:
        return request.headers.get("Sec-Fetch-Site", "").lower() != "cross-site"
    parsed = urlsplit(origin)
    return parsed.scheme in {"http", "https"} and parsed.netloc.lower() == request.host.lower()


def register_request_security(app: Flask) -> None:
    """Enforce loopback host, same-origin writes, and form CSRF tokens."""

    app.secret_key = app.config.get("SECRET_KEY") or os.environ.get(
        "FLOWINONE_SECRET_KEY"
    ) or secrets.token_hex(32)

    @app.before_request
    def protect_local_boundary():
        hostname = (urlsplit(f"//{request.host}").hostname or "").lower()
        if hostname not in _configured_hosts(app):
            abort(403, description="Host is not allowed for this local application.")
        if request.method not in UNSAFE_METHODS:
            return None
        if not _same_origin():
            abort(403, description="Cross-site write request rejected.")
        if request.path.startswith("/api/"):
            return None
        supplied = request.form.get("csrf_token", "")
        if not supplied or not hmac.compare_digest(supplied, _csrf_token()):
            abort(403, description="Invalid or missing CSRF token.")
        return None

    @app.context_processor
    def inject_csrf_token():
        return {"csrf_token": _csrf_token}


__all__ = ["register_request_security"]
