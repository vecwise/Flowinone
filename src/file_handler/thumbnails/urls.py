"""URL normalization and network-boundary checks for thumbnails."""

from __future__ import annotations

import ipaddress
import socket
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "igshid",
    "si",
}


class UnsafeURLError(ValueError):
    """Raised when a URL is not safe for a server-side request."""


def canonicalize_url(url: str) -> str:
    """Return a stable bookmark URL while preserving content-defining queries."""
    from .extensions import canonicalize_local_url

    url = canonicalize_local_url(url) or url

    try:
        parsed = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return url.strip()

    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower().rstrip(".")
    port = parsed.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"
    else:
        netloc = host

    clean_query = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        key_lower = key.lower()
        if key_lower.startswith("utm_") or key_lower in TRACKING_QUERY_KEYS:
            continue
        clean_query.append((key, value))

    path = parsed.path or "/"
    return urlunsplit((scheme, netloc, path, urlencode(clean_query, doseq=True), ""))


def provider_name_for_url(url: str) -> str:
    from .extensions import classify_local_url

    local_provider = classify_local_url(url)
    if local_provider:
        return local_provider
    return "metadata"


def _is_public_ip(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    return ip.is_global


def validate_public_http_url(
    url: str,
    resolver: Callable[..., list] = socket.getaddrinfo,
) -> str:
    """Reject non-web URLs and hosts resolving to local or reserved networks."""
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise UnsafeURLError("invalid URL") from exc

    if parsed.scheme.lower() not in {"http", "https"}:
        raise UnsafeURLError("only HTTP(S) URLs are allowed")
    if not parsed.hostname:
        raise UnsafeURLError("URL has no hostname")
    if parsed.username or parsed.password:
        raise UnsafeURLError("credentialed URLs are not allowed")

    host = parsed.hostname.lower().rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        raise UnsafeURLError("localhost is blocked")

    try:
        direct_ip = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        direct_ip = None
    if direct_ip is not None:
        if not direct_ip.is_global:
            raise UnsafeURLError("private or reserved IP is blocked")
        return url

    try:
        addresses = resolver(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except OSError as exc:
        raise UnsafeURLError(f"hostname resolution failed: {host}") from exc

    resolved = {entry[4][0] for entry in addresses if entry and len(entry) >= 5 and entry[4]}
    if not resolved or any(not _is_public_ip(address) for address in resolved):
        raise UnsafeURLError("hostname resolves to a private or reserved address")
    return url


__all__ = [
    "UnsafeURLError",
    "canonicalize_url",
    "provider_name_for_url",
    "validate_public_http_url",
]
