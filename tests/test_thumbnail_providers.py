from pathlib import Path

from src.file_handler.thumbnails import providers


FIXTURES = Path(__file__).parent / "fixtures"


def test_structured_metadata_order_and_no_arbitrary_image():
    html = (FIXTURES / "metadata.html").read_text(encoding="utf-8")
    urls = providers.extract_structured_image_urls(html, "https://example.com/article")

    assert urls[0] == "https://cdn.example.com/secure-cover.jpg"
    assert "https://example.com/covers/open-graph.jpg" in urls
    assert "https://cdn.example.com/structured-cover.jpg" in urls
    assert "https://ads.example.com/do-not-use.jpg" not in urls


def test_youtube_provider_does_not_fetch_html():
    def fail_fetch(_):
        raise AssertionError("YouTube provider should not request page HTML")

    result = providers.provider_for_url("https://youtube.com/shorts/abcdefghijk", fail_fetch)
    assert result.provider == "youtube"
    assert result.image_urls[0].endswith("/maxresdefault.jpg")
    assert result.image_urls[-1].endswith("/hqdefault.jpg")
