import base64
import json
from dataclasses import replace
from pathlib import Path

import fitz
import pytest

from src.flowinone.resource_library.ai import AIAnswer, AIUnavailable, OpenAICompatibleClient
from src.flowinone.resource_library.database import ResourceDatabase
from src.flowinone.resource_library.enrichment import EnrichmentService
from src.flowinone.resource_library.extractors import (
    ExtractedContent,
    GenericWebExtractor,
    GitHubExtractor,
    PdfExtractor,
    YouTubeExtractor,
)
from src.flowinone.resource_library.jobs import JobQueue
from src.flowinone.resource_library.service import ResourceService
from src.flowinone.resource_library.settings import ResourceSettings
from src.flowinone.resource_library.worker import ResourceWorker


class FakeHTTPClient:
    def __init__(self, payload: bytes, content_type: str = "text/html; charset=utf-8"):
        self.payload = payload
        self.content_type = content_type

    def fetch_bytes(self, url, *, max_bytes, accept, timeout=15):
        assert len(self.payload) <= max_bytes
        return self.payload, self.content_type, url


def make_settings(tmp_path: Path) -> ResourceSettings:
    return ResourceSettings(
        data_dir=tmp_path / "data",
        database_path=tmp_path / "resource.db",
        content_dir=tmp_path / "data" / "content",
        export_dir=tmp_path / "data" / "exports",
        obsidian_vault_path=None,
        obsidian_target_folder="Resources/Digested",
        llm_base_url=None,
        llm_api_key=None,
        llm_model=None,
        user_context="test",
        http_timeout_seconds=30,
        max_download_bytes=2_000_000,
    )


def test_generic_extractor_reads_metadata_and_article_text():
    html = b"""
    <html lang="zh-Hant"><head>
      <title>Fallback</title>
      <meta property="og:title" content="Resource Architecture">
      <meta name="description" content="A useful description">
      <meta name="author" content="Alvin">
      <link rel="canonical" href="https://example.com/article/">
      <link rel="icon" href="/favicon.png">
    </head><body><article><h1>Resource Architecture</h1>
      <p>This article explains a durable resource database, full text search,
      background processing, and note promotion with enough detail to extract.</p>
      <p>It keeps external material separate from synthesized personal knowledge.</p>
    </article></body></html>
    """
    extractor = GenericWebExtractor(FakeHTTPClient(html))
    metadata = extractor.fetch_metadata("https://example.com/article?utm_source=test")
    assert metadata.title == "Resource Architecture"
    assert metadata.author == "Alvin"
    assert metadata.canonical_url == "https://example.com/article"
    assert metadata.favicon_url == "https://example.com/favicon.png"

    content = extractor.extract_content("https://example.com/article")
    assert "durable resource database" in content.text
    assert content.content_type == "article_text"
    assert content.raw_bytes == html


def test_github_extractor_uses_repository_metadata_and_readme():
    class GitHubClient:
        def fetch_bytes(self, url, *, max_bytes, accept, timeout=15):
            if url.endswith("/readme"):
                payload = {
                    "content": base64.b64encode(
                        b"# Flowinone\n\nDurable resource workflow and inspiration collections."
                    ).decode("ascii")
                }
            else:
                payload = {
                    "full_name": "alvin/flowinone",
                    "description": "Visual knowledge hub",
                    "html_url": "https://github.com/alvin/flowinone",
                    "created_at": "2026-01-01T00:00:00Z",
                    "language": "Python",
                    "topics": ["knowledge-base"],
                    "owner": {"login": "alvin", "avatar_url": "https://avatars.example/alvin"},
                }
            encoded = json.dumps(payload).encode("utf-8")
            return encoded, "application/json", url

    extractor = GitHubExtractor(GitHubClient())
    metadata = extractor.fetch_metadata("https://github.com/alvin/flowinone")
    assert metadata.title == "alvin/flowinone"
    assert metadata.source_type == "github"
    content = extractor.extract_content("https://github.com/alvin/flowinone")
    assert "Durable resource workflow" in content.text
    assert content.raw_content_type == "text/markdown"


def test_pdf_extractor_reads_text_without_ocr():
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Flowinone PDF knowledge extraction")
    payload = document.tobytes()
    document.close()
    extractor = PdfExtractor(FakeHTTPClient(payload, "application/pdf"))
    content = extractor.extract_content("https://example.com/paper.pdf")
    assert "Flowinone PDF knowledge extraction" in content.text
    assert content.content_type == "pdf_text"


def test_youtube_extractor_keeps_description_and_transcript(monkeypatch):
    info = {
        "title": "Flowinone walkthrough",
        "description": "A visual resource workflow.",
        "webpage_url": "https://www.youtube.com/watch?v=abc12345",
        "channel": "Alvin",
        "upload_date": "20260710",
        "language": "en",
        "thumbnails": [{"url": "https://i.ytimg.com/vi/abc12345/hqdefault.jpg"}],
        "subtitles": {
            "en": [
                {
                    "url": "https://subs.example/video.vtt",
                    "ext": "vtt",
                }
            ]
        },
    }
    monkeypatch.setattr(YouTubeExtractor, "_info", staticmethod(lambda url: info))
    vtt = b"WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nDurable resource flow\n"
    extractor = YouTubeExtractor(FakeHTTPClient(vtt, "text/vtt"))
    metadata = extractor.fetch_metadata("https://youtu.be/abc12345")
    assert metadata.source_platform == "youtube"
    assert metadata.published_at == "2026-07-10"
    content = extractor.extract_content("https://youtu.be/abc12345")
    assert "Durable resource flow" in content.text
    assert content.content_type == "video_transcript"


def test_enrichment_persists_content_and_updates_fts(monkeypatch, tmp_path):
    database = ResourceDatabase(tmp_path / "resource.db")
    settings = make_settings(tmp_path)
    service = ResourceService(database, link_thumbnail_cache=False)
    resource_id = service.create_url(
        "https://example.com/article", title="Before", enqueue=False
    )["resource"]["id"]

    class FakeExtractor:
        def extract_content(self, url):
            return ExtractedContent(
                content_type="article_text",
                text="A distinctive hippocampus knowledge workflow with enough trustworthy text for indexing.",
                markdown="# Distinctive workflow",
                raw_bytes=b"<article>hippocampus knowledge workflow</article>",
                raw_content_type="text/html",
                extractor_name="fixture",
                extractor_version="1",
            )

    monkeypatch.setattr(
        "src.flowinone.resource_library.enrichment.extractor_for",
        lambda *args, **kwargs: FakeExtractor(),
    )
    enrichment = EnrichmentService(database, settings)
    result = enrichment.extract_content(resource_id)
    assert Path(result["text_path"]).is_file()
    detail = service.repository.get(resource_id)
    assert detail["reading_state"] == "unread"
    assert detail["enrichment_status"] == "complete"
    assert service.repository.list(query="hippocampus").total == 1
    monkeypatch.setattr(
        enrichment.ai,
        "ask",
        lambda **kwargs: AIAnswer(
            question=kwargs["question"],
            answer="The extracted text describes a knowledge workflow.",
            provider="fixture",
            model="fixture-model",
        ),
    )
    answer = enrichment.ask_question(resource_id, "What does it describe?")
    assert "knowledge workflow" in answer["answer"]
    detail = service.repository.get(resource_id)
    assert detail["ai_artifacts"][0]["question"] == "What does it describe?"
    database.dispose()


def test_ai_is_optional(tmp_path):
    client = OpenAICompatibleClient(make_settings(tmp_path))
    assert not client.available
    with pytest.raises(AIUnavailable):
        client.enrich(title="A", url="https://example.com", content="x" * 100)


def test_openai_compatible_summary_is_structured(monkeypatch, tmp_path):
    settings = replace(
        make_settings(tmp_path),
        llm_base_url="http://localhost:1234/v1",
        llm_model="local-test-model",
    )

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "summary_one_line": "一句可靠摘要",
                                    "summary_short": "根據抽取內容形成的短摘要。",
                                    "why_this_matters": "可用於 Flowinone。",
                                    "structured": {"main_topic": "knowledge"},
                                    "tags": ["AI", "Knowledge"],
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }

    class AnswerResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "Only the extracted text supports this answer."}}]}

    class Session:
        trust_env = True

        def post(self, *args, **kwargs):
            assert kwargs["json"]["model"] == "local-test-model"
            return Response() if "response_format" in kwargs["json"] else AnswerResponse()

        def close(self):
            return None

    monkeypatch.setattr("src.flowinone.resource_library.ai.requests.Session", Session)
    result = OpenAICompatibleClient(settings).enrich(
        title="Resource",
        url="https://example.com/resource",
        content="trustworthy extracted content " * 10,
    )
    assert result.summary_one_line == "一句可靠摘要"
    assert result.tags == ["AI", "Knowledge"]
    answer = OpenAICompatibleClient(settings).ask(
        title="Resource",
        url="https://example.com/resource",
        content="trustworthy extracted content " * 10,
        question="What is supported?",
    )
    assert answer.answer.startswith("Only the extracted text")


def test_worker_completes_claimed_job_without_network(monkeypatch, tmp_path):
    database = ResourceDatabase(tmp_path / "resource.db")
    service = ResourceService(database, link_thumbnail_cache=False)
    resource_id = service.create_url("https://example.com/worker", enqueue=False)["resource"]["id"]
    queue = JobQueue(database)
    job = queue.queue("fetch_metadata", resource_id=resource_id)
    worker = ResourceWorker(database, max_workers=1)
    monkeypatch.setattr(worker.enrichment, "process_job", lambda claimed: {"ok": True})
    assert worker.run_until_idle(max_jobs=1) == 1
    states = {row["id"]: row["status"] for row in queue.list_for_resource(resource_id)}
    assert states[job["id"]] == "complete"
    database.dispose()
