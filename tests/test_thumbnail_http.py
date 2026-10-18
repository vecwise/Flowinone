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


@pytest.mark.parametrize('status,accepted',[(203,True),(202,False)])
def test_non_authoritative_success_can_supply_content_but_pending_cannot(monkeypatch,status,accepted):
    monkeypatch.setattr(http,'validate_public_http_url',lambda url:url)
    response=Response(status=status)
    session=type('Session',(),{'get':lambda self,*a,**kw:response,'close':lambda self:None})()
    client=http.SafeHTTPClient()
    if accepted:
        assert client.fetch_bytes('https://example.com/cover',max_bytes=8,accept='image/*',session_factory=lambda:session)[0]==b'cover'
    else:
        with pytest.raises(http.FetchError,match='HTTP 202'):
            client.fetch_bytes('https://example.com/cover',max_bytes=8,accept='image/*',session_factory=lambda:session)
    assert response.closed


def test_large_html_keeps_metadata_prefix_without_reading_body(monkeypatch):
    monkeypatch.setattr(http, "validate_public_http_url", lambda url: url)
    prefix = b'<meta property="og:image" content="/cover.jpg">'
    monkeypatch.setattr(http, "MAX_HTML_BYTES", len(prefix))
    response = Response(headers={"Content-Length": "9000000"})
    def chunks(chunk_size):
        yield prefix + b'padding'
        pytest.fail("The HTML body was read after reaching the byte budget")
    response.iter_content = chunks
    session = type("Session", (), {"get":lambda self,*a,**kw:response,"close":lambda self:None})()
    monkeypatch.setattr(http.requests, "Session", lambda:session)
    assert http.SafeHTTPClient().fetch_html("https://example.com/large") == prefix.decode()
    assert response.closed
