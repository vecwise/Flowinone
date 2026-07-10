import socket

import pytest

from src.file_handler.thumbnails.urls import (
    UnsafeURLError,
    canonicalize_url,
    extract_youtube_id,
    provider_name_for_url,
    validate_public_http_url,
)


@pytest.mark.parametrize(
    ("url", "video_id"),
    [
        ("https://www.youtube.com/watch?v=abcdefghijk&t=20", "abcdefghijk"),
        ("https://youtube.com/shorts/abcdefghijk?feature=share", "abcdefghijk"),
        ("https://www.youtube.com/live/abcdefghijk", "abcdefghijk"),
        ("https://youtu.be/abcdefghijk?si=tracking", "abcdefghijk"),
    ],
)
def test_extract_youtube_variants(url, video_id):
    assert extract_youtube_id(url) == video_id
    assert canonicalize_url(url) == f"https://www.youtube.com/watch?v={video_id}"


@pytest.mark.parametrize(
    ("url", "provider"),
    [
        ("https://www.youtube.com/watch?v=abcdefghijk", "youtube"),
        ("https://example.com/article", "metadata"),
    ],
)
def test_provider_selection(url, provider):
    assert provider_name_for_url(url) == provider


def _resolver_for(address):
    def _resolve(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]

    return _resolve


def test_ssrf_allows_public_dns_result():
    url = "https://example.com/page"
    assert validate_public_http_url(url, resolver=_resolver_for("93.184.216.34")) == url


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.4", "169.254.1.2", "192.168.1.8"])
def test_ssrf_blocks_non_public_dns_results(address):
    with pytest.raises(UnsafeURLError):
        validate_public_http_url("https://example.test/page", resolver=_resolver_for(address))


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/file", "http://localhost/admin"])
def test_ssrf_blocks_non_http_or_local_urls(url):
    with pytest.raises(UnsafeURLError):
        validate_public_http_url(url)
