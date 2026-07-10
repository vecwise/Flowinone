from src.file_handler.thumbnails import extensions
from src.file_handler.thumbnails.providers import provider_for_url
from src.file_handler.thumbnails.urls import provider_name_for_url


def test_missing_local_extension_keeps_generic_pipeline(monkeypatch, tmp_path):
    monkeypatch.setenv(extensions.LOCAL_PROVIDER_ENV, str(tmp_path / "missing.py"))
    extensions.load_local_provider.cache_clear()
    try:
        assert provider_name_for_url("https://example.com/article") == "metadata"
    finally:
        extensions.load_local_provider.cache_clear()


def test_local_extension_contract(monkeypatch, tmp_path):
    provider_path = tmp_path / "thumbnail_provider.py"
    provider_path.write_text(
        """
from types import SimpleNamespace

def classify_url(url):
    return "custom" if "custom.example" in url else None

def resolve(url, fetch_html):
    if classify_url(url):
        return SimpleNamespace(provider="custom", image_urls=("https://cdn.example.com/cover.jpg",))
    return None
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv(extensions.LOCAL_PROVIDER_ENV, str(provider_path))
    extensions.load_local_provider.cache_clear()
    try:
        url = "https://custom.example/item"
        assert provider_name_for_url(url) == "custom"
        result = provider_for_url(url, lambda _: "")
        assert result.provider == "custom"
        assert result.image_urls == ("https://cdn.example.com/cover.jpg",)
    finally:
        extensions.load_local_provider.cache_clear()
