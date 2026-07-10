"""Optional OpenAI-compatible summarization without coupling core workflows to AI."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

import requests

from .settings import ResourceSettings, get_resource_settings


PROMPT_VERSION = "resource-summary-v1"


class AIUnavailable(RuntimeError):
    """Raised when no explicit local/external AI provider is configured."""


@dataclass(frozen=True)
class AIEnrichment:
    summary_one_line: str
    summary_short: str
    why_this_matters: str
    structured: dict
    tags: list[str]
    provider: str
    model: str
    prompt_version: str = PROMPT_VERSION


@dataclass(frozen=True)
class AIAnswer:
    question: str
    answer: str
    provider: str
    model: str
    prompt_version: str = "resource-qa-v1"


def _extract_json(raw: str) -> dict:
    text = (raw or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("AI 回傳內容不是 JSON object")
    return payload


class OpenAICompatibleClient:
    """Small client for LM Studio and explicitly configured OpenAI-compatible APIs."""

    def __init__(self, settings: Optional[ResourceSettings] = None):
        self.settings = settings or get_resource_settings()

    @property
    def available(self) -> bool:
        return bool(self.settings.llm_base_url and self.settings.llm_model)

    def enrich(self, *, title: str, url: str, content: str) -> AIEnrichment:
        if not self.available:
            raise AIUnavailable("尚未設定 LLM_BASE_URL 與 LLM_MODEL")
        if len(content.strip()) < 80:
            raise ValueError("抽取內容太短，無法可靠摘要")

        endpoint = self.settings.llm_base_url.rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.settings.llm_api_key:
            headers["Authorization"] = f"Bearer {self.settings.llm_api_key}"
        system_prompt = (
            "你是個人知識庫的資源分析器。只能根據提供的文字回答，不得假裝讀過未提供內容。"
            "回傳單一 JSON object，不要 Markdown。"
        )
        user_prompt = f"""
請分析以下外部資源。

標題：{title}
URL：{url}
使用者脈絡：{self.settings.user_context}

內容：
{content[:60000]}

請回傳：
{{
  "summary_one_line": "30～80 個中文字",
  "summary_short": "150～400 個中文字，說明主題、論點、方法、適用情境與限制",
  "why_this_matters": "具體說明和使用者脈絡的關聯；若無明確關聯就直接說明",
  "structured": {{
    "main_topic": "",
    "main_claims": [],
    "key_concepts": [],
    "methods": [],
    "tools_mentioned": [],
    "use_cases": [],
    "limitations": [],
    "questions_to_verify": [],
    "related_projects": [],
    "recommended_action": "read|skim|archive|reject"
  }},
  "tags": ["3 到 8 個簡短主題標籤"]
}}
""".strip()
        session = requests.Session()
        session.trust_env = False
        try:
            request_payload = {
                "model": self.settings.llm_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
            }
            response = session.post(
                endpoint,
                headers=headers,
                json=request_payload,
                timeout=(5, max(30, self.settings.http_timeout_seconds * 2)),
            )
            if getattr(response, "status_code", 200) in {400, 404, 422}:
                # Some local OpenAI-compatible servers do not implement
                # response_format even though they otherwise support chat completions.
                request_payload.pop("response_format", None)
                response = session.post(
                    endpoint,
                    headers=headers,
                    json=request_payload,
                    timeout=(5, max(30, self.settings.http_timeout_seconds * 2)),
                )
            response.raise_for_status()
            body = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise RuntimeError(f"AI provider 呼叫失敗: {exc}") from exc
        finally:
            session.close()
        try:
            raw_content = body["choices"][0]["message"]["content"]
            payload = _extract_json(str(raw_content))
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("AI provider 回傳格式無法解析") from exc

        structured = payload.get("structured")
        tags = payload.get("tags")
        return AIEnrichment(
            summary_one_line=str(payload.get("summary_one_line") or "").strip(),
            summary_short=str(payload.get("summary_short") or "").strip(),
            why_this_matters=str(payload.get("why_this_matters") or "").strip(),
            structured=structured if isinstance(structured, dict) else {},
            tags=[str(tag).strip() for tag in tags if str(tag).strip()][:8]
            if isinstance(tags, list)
            else [],
            provider=self.settings.llm_base_url or "",
            model=self.settings.llm_model or "",
        )

    def ask(self, *, title: str, url: str, content: str, question: str) -> AIAnswer:
        """Answer one user question using only the locally extracted resource text."""
        if not self.available:
            raise AIUnavailable("尚未設定 LLM_BASE_URL 與 LLM_MODEL")
        question = question.strip()
        if not question:
            raise ValueError("問題不可空白")
        if len(content.strip()) < 40:
            raise ValueError("目前沒有足夠的抽取內容可供 Ask AI 使用")
        endpoint = self.settings.llm_base_url.rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.settings.llm_api_key:
            headers["Authorization"] = f"Bearer {self.settings.llm_api_key}"
        prompt = f"""標題：{title}
URL：{url}

抽取內容：
{content[:60000]}

問題：{question}

只根據抽取內容回答。若內容不足，清楚說明不知道或需要查看原文；不要補造資訊。"""
        session = requests.Session()
        session.trust_env = False
        try:
            response = session.post(
                endpoint,
                headers=headers,
                json={
                    "model": self.settings.llm_model,
                    "messages": [
                        {
                            "role": "system",
                            "content": "你是資源閱讀助理，只能依據使用者提供的抽取文字回答。",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.1,
                },
                timeout=(5, max(30, self.settings.http_timeout_seconds * 2)),
            )
            response.raise_for_status()
            body = response.json()
            answer = str(body["choices"][0]["message"]["content"]).strip()
        except (requests.RequestException, KeyError, IndexError, TypeError, ValueError) as exc:
            raise RuntimeError(f"Ask AI 呼叫失敗: {exc}") from exc
        finally:
            session.close()
        if not answer:
            raise RuntimeError("Ask AI 沒有回傳內容")
        return AIAnswer(
            question=question,
            answer=answer,
            provider=self.settings.llm_base_url or "",
            model=self.settings.llm_model or "",
        )


__all__ = [
    "AIAnswer",
    "AIEnrichment",
    "AIUnavailable",
    "OpenAICompatibleClient",
    "PROMPT_VERSION",
]
