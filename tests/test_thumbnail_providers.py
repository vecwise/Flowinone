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


def test_without_local_rules_only_explicit_metadata_is_used():
    html = '<img src="/advert.jpg"><meta property="og:image" content="/cover.jpg">'
    result = providers.provider_for_url("https://example.com/article", lambda _: html)
    assert result.provider == "metadata"
    assert result.image_urls == ("https://example.com/cover.jpg",)
