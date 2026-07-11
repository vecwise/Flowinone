"""Project-linked retrieval, decisions, and traceable Output Assets."""

from __future__ import annotations

import json
from typing import Any, Iterable

from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from src.flowinone.entry_system.models import ENTRY_MODES, Project
from src.flowinone.resource_library.database import ResourceDatabase
from src.flowinone.resource_library.models import (
    AssetSource,
    Decision,
    OutputAsset,
    Resource,
    ResourceMode,
    ResourceProject,
    new_id,
    utc_now_text,
)
from src.flowinone.resource_library.repository import ResourceNotFound, serialize_resource


ASSET_TYPES = {
    "article",
    "spec",
    "prompt",
    "sop",
    "technical_note",
    "interview_answer",
    "script",
    "readme",
    "adr",
    "code",
    "checklist",
    "agent_skill",
}
ASSET_STATUSES = {"draft", "ready", "exported", "archived"}


class KnowledgeOSError(ValueError):
    """Validation/not-found error for the Knowledge OS application service."""


def _json(value: str | None, fallback: Any) -> Any:
    try:
        parsed = json.loads(value or "")
        return parsed
    except (TypeError, json.JSONDecodeError):
        return fallback


def _asset_dict(asset: OutputAsset) -> dict[str, Any]:
    return {
        "id": asset.id,
        "title": asset.title,
        "asset_type": asset.asset_type,
        "project_id": asset.project_id,
        "status": asset.status,
        "version": asset.version,
        "body": asset.body,
        "export_path": asset.export_path,
        "metadata": _json(asset.metadata_json, {}),
        "created_at": asset.created_at,
        "updated_at": asset.updated_at,
        "sources": [
            {
                "source_type": source.source_type,
                "source_id": source.source_id,
                "citation_label": source.citation_label,
            }
            for source in asset.sources
        ],
    }


class KnowledgeOSService:
    """Keep retrieval context and human-owned outputs separate from raw resources."""

    def __init__(self, database: ResourceDatabase):
        self.database = database

    @staticmethod
    def _project(session, project_id: str | None) -> Project | None:
        if not project_id:
            return None
        project = session.get(Project, project_id)
        if project is None:
            raise KnowledgeOSError("找不到指定 Project")
        return project

    def link_resource(
        self,
        resource_id: str,
        *,
        project_id: str | None = None,
        modes: Iterable[str] = (),
        relevance: float = 1.0,
        source: str = "user",
    ) -> dict[str, Any]:
        normalized_modes = tuple(dict.fromkeys(str(mode).strip().lower() for mode in modes))
        if any(mode not in ENTRY_MODES for mode in normalized_modes):
            raise KnowledgeOSError("包含無效的 Mode")
        if source not in {"user", "ai", "system"}:
            raise KnowledgeOSError("無效的關聯來源")
        relevance = max(0.0, min(float(relevance), 1.0))
        with self.database.session() as session:
            resource = session.get(Resource, resource_id)
            if resource is None:
                raise ResourceNotFound(resource_id)
            self._project(session, project_id)
            if project_id:
                link = session.get(ResourceProject, (resource_id, project_id))
                if link is None:
                    session.add(
                        ResourceProject(
                            resource_id=resource_id,
                            project_id=project_id,
                            relevance=relevance,
                            source=source,
                        )
                    )
                else:
                    link.relevance = relevance
                    link.source = source
            for mode in normalized_modes:
                link = session.get(ResourceMode, (resource_id, mode))
                if link is None:
                    session.add(
                        ResourceMode(
                            resource_id=resource_id,
                            mode=mode,
                            relevance=relevance,
                            source=source,
                        )
                    )
                else:
                    link.relevance = relevance
                    link.source = source
        return self.resource_context(resource_id)

    def resource_context(self, resource_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            resource = session.get(Resource, resource_id)
            if resource is None:
                raise ResourceNotFound(resource_id)
            projects = session.scalars(
                select(ResourceProject).where(ResourceProject.resource_id == resource_id)
            ).all()
            modes = session.scalars(
                select(ResourceMode).where(ResourceMode.resource_id == resource_id)
            ).all()
            return {
                "resource_id": resource_id,
                "projects": [
                    {
                        "project_id": link.project_id,
                        "relevance": link.relevance,
                        "source": link.source,
                    }
                    for link in projects
                ],
                "modes": [
                    {"mode": link.mode, "relevance": link.relevance, "source": link.source}
                    for link in modes
                ],
            }

    def retrieve(
        self,
        *,
        project_id: str | None = None,
        mode: str | None = None,
        query: str = "",
        limit: int = 12,
    ) -> list[dict[str, Any]]:
        if mode and mode not in ENTRY_MODES:
            raise KnowledgeOSError("無效的 retrieval mode")
        limit = max(1, min(int(limit), 100))
        statement = select(Resource).where(Resource.disposition == "active")
        if project_id:
            statement = statement.join(ResourceProject).where(ResourceProject.project_id == project_id)
        if mode:
            statement = statement.join(ResourceMode).where(ResourceMode.mode == mode)
        normalized_query = query.strip()
        if normalized_query:
            pattern = f"%{normalized_query}%"
            statement = statement.where(
                or_(
                    Resource.title.ilike(pattern),
                    Resource.summary_one_line.ilike(pattern),
                    Resource.summary_short.ilike(pattern),
                    Resource.user_note.ilike(pattern),
                )
            )
        statement = statement.distinct().order_by(Resource.priority.desc(), Resource.updated_at.desc()).limit(limit)
        with self.database.session() as session:
            resources = session.scalars(statement).all()
            return [serialize_resource(resource) for resource in resources]

    def create_asset(self, payload: dict[str, Any]) -> dict[str, Any]:
        title = str(payload.get("title") or "").strip()
        asset_type = str(payload.get("asset_type") or "spec").strip().lower()
        status = str(payload.get("status") or "draft").strip().lower()
        if not title:
            raise KnowledgeOSError("Output Asset 需要標題")
        if asset_type not in ASSET_TYPES:
            raise KnowledgeOSError("不支援的 Output Asset 類型")
        if status not in ASSET_STATUSES:
            raise KnowledgeOSError("無效的 Output Asset 狀態")
        raw_sources = payload.get("sources") or []
        if isinstance(raw_sources, str):
            raw_sources = _json(raw_sources, [])
        with self.database.session() as session:
            project_id = str(payload.get("project_id") or "").strip() or None
            self._project(session, project_id)
            asset = OutputAsset(
                id=new_id(),
                title=title,
                asset_type=asset_type,
                project_id=project_id,
                status=status,
                version=str(payload.get("version") or "0.1").strip() or "0.1",
                body=str(payload.get("body") or ""),
                export_path=str(payload.get("export_path") or "").strip() or None,
                metadata_json=json.dumps(payload.get("metadata") or {}, ensure_ascii=False),
            )
            session.add(asset)
            for source in raw_sources:
                if not isinstance(source, dict):
                    continue
                source_type = str(source.get("source_type") or "").strip()
                source_id = str(source.get("source_id") or "").strip()
                if source_type and source_id:
                    asset.sources.append(
                        AssetSource(
                            source_type=source_type,
                            source_id=source_id,
                            citation_label=str(source.get("citation_label") or "").strip() or None,
                        )
                    )
            session.flush()
            session.refresh(asset)
            return _asset_dict(asset)

    def list_assets(
        self, *, project_id: str | None = None, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        statement = select(OutputAsset).options(selectinload(OutputAsset.sources))
        if project_id:
            statement = statement.where(OutputAsset.project_id == project_id)
        if status:
            if status not in ASSET_STATUSES:
                raise KnowledgeOSError("無效的 Output Asset 狀態")
            statement = statement.where(OutputAsset.status == status)
        statement = statement.order_by(OutputAsset.updated_at.desc()).limit(max(1, min(limit, 200)))
        with self.database.session() as session:
            return [_asset_dict(asset) for asset in session.scalars(statement).all()]

    def update_asset(self, asset_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        allowed = {"title", "asset_type", "project_id", "status", "version", "body", "export_path"}
        changes = dict(payload)
        if "asset_type" in changes:
            changes["asset_type"] = str(changes["asset_type"] or "").strip().lower()
        if "status" in changes:
            changes["status"] = str(changes["status"] or "").strip().lower()
        if "title" in changes:
            changes["title"] = str(changes["title"] or "").strip()
            if not changes["title"]:
                raise KnowledgeOSError("Output Asset 需要標題")
        with self.database.session() as session:
            asset = session.scalar(
                select(OutputAsset)
                .options(selectinload(OutputAsset.sources))
                .where(OutputAsset.id == asset_id)
            )
            if asset is None:
                raise KnowledgeOSError("找不到 Output Asset")
            if "asset_type" in changes and changes["asset_type"] not in ASSET_TYPES:
                raise KnowledgeOSError("不支援的 Output Asset 類型")
            if "status" in changes and changes["status"] not in ASSET_STATUSES:
                raise KnowledgeOSError("無效的 Output Asset 狀態")
            if "project_id" in changes:
                self._project(session, str(changes.get("project_id") or "").strip() or None)
            for key in allowed & changes.keys():
                value = changes[key]
                if key == "project_id":
                    value = str(value or "").strip() or None
                setattr(asset, key, value)
            asset.updated_at = utc_now_text()
            session.flush()
            return _asset_dict(asset)

    def create_decision(self, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        question = str(payload.get("question") or "").strip()
        decision_text = str(payload.get("decision") or "").strip()
        if not question or not decision_text:
            raise KnowledgeOSError("Decision 需要問題與人工確認的決定")
        alternatives = payload.get("alternatives") or []
        if isinstance(alternatives, str):
            alternatives = [value.strip() for value in alternatives.splitlines() if value.strip()]
        with self.database.session() as session:
            self._project(session, project_id)
            decision = Decision(
                id=new_id(),
                project_id=project_id,
                question=question,
                decision=decision_text,
                rationale=str(payload.get("rationale") or "").strip() or None,
                alternatives_json=json.dumps(alternatives, ensure_ascii=False),
            )
            session.add(decision)
            session.flush()
            return self._decision_dict(decision)

    @staticmethod
    def _decision_dict(decision: Decision) -> dict[str, Any]:
        return {
            "id": decision.id,
            "project_id": decision.project_id,
            "question": decision.question,
            "decision": decision.decision,
            "rationale": decision.rationale,
            "alternatives": _json(decision.alternatives_json, []),
            "created_at": decision.created_at,
        }

    def list_decisions(self, project_id: str) -> list[dict[str, Any]]:
        with self.database.session() as session:
            rows = session.scalars(
                select(Decision)
                .where(Decision.project_id == project_id)
                .order_by(Decision.created_at.desc())
            ).all()
            return [self._decision_dict(row) for row in rows]


__all__ = [
    "ASSET_STATUSES",
    "ASSET_TYPES",
    "KnowledgeOSError",
    "KnowledgeOSService",
]
