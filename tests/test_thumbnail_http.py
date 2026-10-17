import pytest

from src.file_handler.thumbnails import http
from src.file_handler.thumbnails.urls import UnsafeURLError


class Response:
    def __init__(self, status=200, payload=b"cover", headers=None):
        self.status_code = status
        self.payload = payload
        self.headers = headers or {}
        self.closed = False

    def iter_content(self, chunk_size):
        yield self.payload

    def close(self):
        self.closed = True


def test_custom_transport_uses_shared_limits_and_headers(monkeypatch):
    response = Response()
    calls = []
    session = type("Session", (), {"get":lambda self,url,**kw:(calls.append((url,kw)) or response),
                                    "close":lambda self:None})()
    monkeypatch.setattr(http, "validate_public_http_url", lambda url:url)
    client = http.SafeHTTPClient(http.DomainRateLimiter(0))
    assert client.fetch_bytes("https://cdn.example/cover", max_bytes=8, accept="image/*",
                              headers={"User-Agent":None,"Referer":"https://example.com/page"},
                              session_factory=lambda:session)[0] == b"cover"
    assert "User-Agent" not in calls[0][1]["headers"]
    assert calls[0][1]["allow_redirects"] is False
    assert calls[0][1]["stream"] is True
    assert response.closed


def test_custom_transport_cannot_redirect_to_private_network(monkeypatch):
    calls = []
    response = Response(302, headers={"Location":"http://127.0.0.1/private"})
    def public(url):
        if "127.0.0.1" in url:
            raise UnsafeURLError("private target")
    monkeypatch.setattr(http, "validate_public_http_url", public)
    session = type("Session", (), {"get":lambda self,url,**kw:(calls.append(url) or response),
                                    "close":lambda self:None})()
    with pytest.raises(UnsafeURLError):
        http.SafeHTTPClient(http.DomainRateLimiter(0)).fetch_bytes(
            "https://example.com/cover", max_bytes=8, accept="image/*", session_factory=lambda:session)
    assert calls == ["https://example.com/cover"]
    assert response.closed


def test_custom_transport_still_enforces_byte_budget(monkeypatch):
    monkeypatch.setattr(http, "validate_public_http_url", lambda url:url)
    response = Response(payload=b"too large")
    session = type("Session", (), {"get":lambda self,*a,**kw:response,"close":lambda self:None})()
    with pytest.raises(http.FetchError, match="exceeds"):
        http.SafeHTTPClient().fetch_bytes("https://example.com/cover", max_bytes=3, accept="image/*", session_factory=lambda:session)
    assert response.closed
