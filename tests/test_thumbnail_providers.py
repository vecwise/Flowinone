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


def test_standard_image_objects_and_video_posters_are_content_candidates():
    html = '''
      <meta property="og:image:url" content="/og.jpg">
      <meta itemprop="image" content="/microdata.jpg">
      <script type="application/ld+json">{"@type":"Article","image":{"@type":"ImageObject","url":"/article.jpg"}}</script>
      <script type="application/ld+json">{"image":[{"contentUrl":"/gallery.jpg"}]}</script>
      <video poster="/video.jpg"></video><img src="/advert.jpg">
    '''
    urls = providers.extract_structured_image_urls(html, "https://example.com/page")
    assert urls == tuple("https://example.com/" + name + ".jpg" for name in
                        ("og", "microdata", "article", "gallery", "video"))


def test_author_avatar_cannot_replace_content_cover():
    html = '''<script type="application/ld+json">{"author":{"image":"/avatar.jpg"},"image":"/cover.jpg"}</script>'''
    assert providers.extract_structured_image_urls(html, "https://example.com/page") == ("https://example.com/cover.jpg",)


def test_empty_poster_or_json_image_never_downloads_the_page_as_an_image():
    html = '''<video poster=""></video><video poster="  "></video>
      <script type="application/ld+json">{"image":"","thumbnailUrl":[" "]}</script>'''
    assert providers.extract_structured_image_urls(html, "https://example.com/page") == ()
