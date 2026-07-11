"""Domain policy for Flowinone's local-first state-based entry system."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.flowinone.resource_library.database import ResourceDatabase, get_resource_database

from .models import ENTRY_MODES, ENTRY_STATUSES, PROJECT_STATUSES
from .repository import EntryNotFound, EntryRepository, ProjectNotFound


class EntrySystemError(ValueError):
    """Raised when an entry-system request is invalid or unsafe."""


ENTRY_ALLOWED_SCHEMES = {"http", "https", "file", "obsidian", "vscode", "eagle"}
ENTRY_TRANSITIONS = {
    "scan": ("learn", "build"),
    "learn": ("think", "build", "write"),
    "think": ("build", "write"),
    "build": ("write",),
    "recover": (),
    "write": (),
}
_SAFE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
_MAX_JSON_CHARS = 32_000


def _clean_text(value: Any, field: str, *, required: bool = False, limit: int = 12_000) -> Optional[str]:
    if value is None:
        if required:
            raise EntrySystemError(f"{field} 為必填")
        return None
    result = str(value).strip()
    if not result:
        if required:
            raise EntrySystemError(f"{field} 為必填")
        return None
    if len(result) > limit:
        raise EntrySystemError(f"{field} 過長")
    return result


def _object(value: Any, field: str) -> dict[str, Any]:
    if value in (None, ""):
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise EntrySystemError(f"{field} 必須是 JSON object") from exc
    if not isinstance(value, Mapping):
        raise EntrySystemError(f"{field} 必須是 JSON object")
    result = dict(value)
    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    if len(encoded) > _MAX_JSON_CHARS:
        raise EntrySystemError(f"{field} 過大")
    return result


def _list(value: Any, field: str) -> list[Any]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise EntrySystemError(f"{field} 必須是 JSON array") from exc
    if not isinstance(value, list):
        raise EntrySystemError(f"{field} 必須是 JSON array")
    if len(value) > 100:
        raise EntrySystemError(f"{field} 最多可儲存 100 個項目")
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    if len(encoded) > _MAX_JSON_CHARS:
        raise EntrySystemError(f"{field} 過大")
    return value


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _json_list(value: list[Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _safe_target_uri(value: Any) -> Optional[str]:
    uri = _clean_text(value, "Target URI", limit=4_096)
    if not uri:
        return None
    if uri.startswith("/"):
        if uri.startswith("//") or "\\" in uri:
            raise EntrySystemError("Flowinone 內部 Target 必須是安全的絕對路徑")
        return uri
    parsed = urlsplit(uri)
    scheme = parsed.scheme.lower()
    if scheme not in ENTRY_ALLOWED_SCHEMES:
        raise EntrySystemError("Target 僅支援 Flowinone 路徑、HTTP(S)、file、Obsidian、VS Code 或 Eagle URI")
    if scheme in {"http", "https"} and not parsed.netloc:
        raise EntrySystemError("HTTP(S) Target 缺少主機名稱")
    if scheme == "file" and not parsed.path:
        raise EntrySystemError("file Target 缺少路徑")
    return uri


def _target_type(value: Any, target_uri: Optional[str]) -> Optional[str]:
    if not target_uri:
        return None
    raw = _clean_text(value, "Target type", limit=48)
    if raw:
        candidate = raw.lower().replace("-", "_")
        if not _SAFE_NAME.match(candidate):
            raise EntrySystemError("Target type 格式不正確")
        return candidate
    return "flowinone_view" if target_uri.startswith("/") else "external"


def _kind(value: Any) -> str:
    candidate = (_clean_text(value, "Entry kind", limit=48) or "task_entry").lower().replace("-", "_")
    aliases = {
        "project": "project_entry",
        "task": "task_entry",
        "note": "note_entry",
        "item": "item_entry",
        "view": "view_entry",
        "flow": "flow_entry",
        "external": "external_entry",
        "coding": "coding_task",
    }
    candidate = aliases.get(candidate, candidate)
    if not _SAFE_NAME.match(candidate):
        raise EntrySystemError("Entry kind 格式不正確")
    return candidate


def _state_query_parameters(state: Mapping[str, Any]) -> dict[str, str]:
    """Convert a portable state snapshot into supported Resource query parameters."""
    params: dict[str, str] = {}
    query = state.get("query")
    if isinstance(query, Mapping) and str(query.get("text") or "").strip():
        params["q"] = str(query["text"]).strip()

    filters = state.get("filters")
    if isinstance(filters, Mapping):
        filters = [
            {"field": field, "value": value}
            for field, value in filters.items()
        ]
    allowed = {"reading_state", "disposition", "source_type", "tag", "domain", "min_priority"}
    if isinstance(filters, list):
        for item in filters:
            if not isinstance(item, Mapping):
                continue
            field = str(item.get("field") or "").strip()
            value = str(item.get("value") or "").strip()
            if field in allowed and value:
                params[field] = value
    return params


class EntryService:
    """High-level Entry, Project, resume, snapshot, and Think → Build operations."""

    def __init__(self, database: Optional[ResourceDatabase] = None):
        self.database = database or get_resource_database()
        self.repository = EntryRepository(self.database)

    def _normalized_entry(
        self,
        data: Mapping[str, Any],
        *,
        existing: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        def supplied(name: str, fallback: Any = None) -> Any:
            return data[name] if name in data else fallback

        current = existing or {}
        name = _clean_text(supplied("name", current.get("name")), "名稱", required=True, limit=500)
        mode = str(supplied("mode", current.get("mode") or "build") or "").strip().lower()
        status = str(supplied("status", current.get("status") or "active") or "").strip().lower()
        if mode not in ENTRY_MODES:
            raise EntrySystemError("無效的 Entry mode")
        if status not in ENTRY_STATUSES:
            raise EntrySystemError("無效的 Entry status")

        raw_target = supplied("target", None)
        if raw_target is not None:
            target = _object(raw_target, "target")
            raw_uri = target.get("uri")
            raw_type = target.get("type")
        elif "target_uri" in data or "target_type" in data:
            raw_uri = supplied("target_uri")
            raw_type = supplied("target_type")
        else:
            target = current.get("target") or {}
            raw_uri = target.get("uri")
            raw_type = target.get("type")
        target_uri = _safe_target_uri(raw_uri)

        context = _object(supplied("context", supplied("context_json", current.get("context", {}))), "context")
        state = _object(supplied("state", supplied("state_json", current.get("state", {}))), "state")
        on_enter = _object(supplied("on_enter", supplied("on_enter_json", current.get("on_enter", {}))), "on_enter")
        if mode == "scan" and status == "active":
            scan = context.get("scan") if isinstance(context.get("scan"), Mapping) else context
            minutes = scan.get("time_budget_minutes")
            item_limit = scan.get("item_limit")
            exit_condition = _clean_text(scan.get("exit_condition"), "SCAN 退出條件", limit=1_000)
            try:
                minutes_value = int(minutes) if minutes not in (None, "") else None
                item_limit_value = int(item_limit) if item_limit not in (None, "") else None
            except (TypeError, ValueError) as exc:
                raise EntrySystemError("SCAN 的時間與項目上限必須是整數") from exc
            if minutes_value is not None and not 1 <= minutes_value <= 240:
                raise EntrySystemError("SCAN 時間上限需在 1–240 分鐘")
            if item_limit_value is not None and not 1 <= item_limit_value <= 100:
                raise EntrySystemError("SCAN 項目上限需在 1–100 個")
            if not any((minutes_value, item_limit_value, exit_condition)):
                raise EntrySystemError("Active SCAN Entry 必須設定時間、項目或退出條件")
            context["scan"] = {
                "time_budget_minutes": minutes_value,
                "item_limit": item_limit_value,
                "exit_condition": exit_condition,
            }
        project_id = _clean_text(supplied("project_id", current.get("project_id")), "Project", limit=32)
        source_entry_id = _clean_text(
            supplied("source_entry_id", current.get("source_entry_id")), "來源 Entry", limit=32
        )
        next_action = _clean_text(
            supplied("next_action", current.get("next_action")), "下一步", limit=4_000
        )
        if mode == "build" and status == "active" and not next_action:
            raise EntrySystemError("Active BUILD Entry 必須有下一步行動")

        values = {
            "name": name,
            "mode": mode,
            "kind": _kind(supplied("kind", current.get("kind") or "task_entry")),
            "status": status,
            "intent": _clean_text(supplied("intent", current.get("intent")), "意圖", limit=8_000),
            "target_type": _target_type(raw_type, target_uri),
            "target_uri": target_uri,
            "context_json": _json(context),
            "state_json": _json(state),
            "on_enter_json": _json(on_enter),
            "next_action": next_action,
            "last_action": _clean_text(
                supplied("last_action", current.get("last_action")), "上一個行動", limit=4_000
            ),
            "project_id": project_id,
            "source_entry_id": source_entry_id,
        }
        if project_id:
            self.repository.get_project(project_id, include_entries=False)
        if source_entry_id:
            self.repository.get_entry(source_entry_id)
        return values

    def create_entry(self, data: Mapping[str, Any]) -> dict[str, Any]:
        entry = self.repository.create_entry(self._normalized_entry(data))
        if entry["project_id"] and entry["mode"] == "build" and entry["status"] == "active":
            project = self.repository.get_project(entry["project_id"], include_entries=False)
            if not project.get("current_entry_id"):
                self.repository.set_current_entry(project["id"], entry["id"])
                entry = self.repository.get_entry(entry["id"])
        return entry

    def update_entry(self, entry_id: str, data: Mapping[str, Any]) -> dict[str, Any]:
        existing = self.repository.get_entry(entry_id)
        values = self._normalized_entry(data, existing=existing)
        entry = self.repository.update_entry(entry_id, values)
        previous_project_id = existing.get("project_id")
        if previous_project_id:
            previous_project = self.repository.get_project(
                previous_project_id, include_entries=False
            )
            if previous_project.get("current_entry_id") == entry_id and (
                previous_project_id != entry.get("project_id")
                or entry.get("mode") != "build"
                or entry.get("status") != "active"
            ):
                self.repository.set_current_entry(previous_project_id, None)
        if entry["project_id"] and entry["mode"] == "build" and entry["status"] == "active":
            project = self.repository.get_project(entry["project_id"], include_entries=False)
            if not project.get("current_entry_id"):
                self.repository.set_current_entry(project["id"], entry["id"])
                entry = self.repository.get_entry(entry["id"])
        return entry

    def _normalized_project(
        self,
        data: Mapping[str, Any],
        *,
        existing: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        current = existing or {}

        def supplied(name: str, fallback: Any = None) -> Any:
            return data[name] if name in data else fallback

        status = str(supplied("status", current.get("status") or "active") or "").strip().lower()
        if status not in PROJECT_STATUSES:
            raise EntrySystemError("無效的 Project status")
        current_entry_id = _clean_text(
            supplied("current_entry_id", current.get("current_entry_id")), "目前 Entry", limit=32
        )
        if current_entry_id:
            entry = self.repository.get_entry(current_entry_id)
            if existing and entry.get("project_id") != existing.get("id"):
                raise EntrySystemError("目前 Entry 必須屬於這個 Project")
        return {
            "name": _clean_text(supplied("name", current.get("name")), "Project 名稱", required=True, limit=500),
            "status": status,
            "goal": _clean_text(supplied("goal", current.get("goal")), "Project goal", limit=8_000),
            "next_action": _clean_text(
                supplied("next_action", current.get("next_action")), "Project 下一步", limit=4_000
            ),
            "current_entry_id": current_entry_id,
            "repositories_json": _json_list(
                _list(supplied("repositories", supplied("repositories_json", current.get("repositories", []))), "repositories")
            ),
            "notes_json": _json_list(
                _list(supplied("notes", supplied("notes_json", current.get("notes", []))), "notes")
            ),
            "views_json": _json_list(
                _list(supplied("views", supplied("views_json", current.get("views", []))), "views")
            ),
            "metadata_json": _json(
                _object(supplied("metadata", supplied("metadata_json", current.get("metadata", {}))), "metadata")
            ),
        }

    def create_project(self, data: Mapping[str, Any]) -> dict[str, Any]:
        return self.repository.create_project(self._normalized_project(data))

    def update_project(self, project_id: str, data: Mapping[str, Any]) -> dict[str, Any]:
        existing = self.repository.get_project(project_id, include_entries=False)
        return self.repository.update_project(project_id, self._normalized_project(data, existing=existing))

    def create_build_from_template(self, data: Mapping[str, Any]) -> dict[str, Any]:
        template = str(data.get("template") or "blank").strip().lower()
        kind_by_template = {
            "note": "note_entry",
            "draft": "note_entry",
            "experiment": "flow_entry",
            "coding_task": "coding_task",
            "blank": "task_entry",
            "project": "project_entry",
        }
        if template not in kind_by_template:
            raise EntrySystemError("不支援的 BUILD 模板")
        name = _clean_text(data.get("name"), "名稱", required=True, limit=500)
        next_action = _clean_text(data.get("next_action"), "下一步", required=True, limit=4_000)
        if template == "project":
            project = self.create_project(
                {
                    "name": name,
                    "goal": data.get("intent") or data.get("goal"),
                    "next_action": next_action,
                }
            )
            entry = self.create_entry(
                {
                    "name": name,
                    "mode": "build",
                    "kind": "project_entry",
                    "status": "active",
                    "intent": data.get("intent") or data.get("goal"),
                    "next_action": next_action,
                    "project_id": project["id"],
                    "target": {"type": "flowinone_view", "uri": f"/projects/{project['id']}/"},
                    "context": {"template": "project", "project_id": project["id"]},
                }
            )
            self.repository.set_current_entry(project["id"], entry["id"])
            return self.repository.get_entry(entry["id"])
        return self.create_entry(
            {
                "name": name,
                "mode": "build",
                "kind": kind_by_template[template],
                "status": "active",
                "intent": data.get("intent"),
                "next_action": next_action,
                "project_id": data.get("project_id"),
                "target_type": data.get("target_type"),
                "target_uri": data.get("target_uri"),
                "context": {"template": template},
            }
        )

    def think_to_build(self, data: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        problem = _clean_text(data.get("problem"), "問題", required=True, limit=8_000)
        hypothesis = _clean_text(data.get("hypothesis"), "假設", limit=8_000)
        next_action = _clean_text(data.get("next_action"), "下一步", required=True, limit=4_000)
        target = data.get("target")
        if target is None:
            target = {"type": data.get("target_type"), "uri": data.get("target_uri")}
        thought = self.create_entry(
            {
                "name": f"Think: {problem[:96]}",
                "mode": "think",
                "kind": "note_entry",
                "status": "completed",
                "intent": problem,
                "next_action": next_action,
                "target": target,
                "context": {"problem": problem, "hypothesis": hypothesis or ""},
                "last_action": "已轉換為 BUILD Entry",
            }
        )
        build = self.create_entry(
            {
                "name": _clean_text(data.get("name"), "BUILD 名稱", limit=500) or next_action,
                "mode": "build",
                "kind": _kind(data.get("kind") or "task_entry"),
                "status": "active",
                "intent": problem,
                "next_action": next_action,
                "project_id": data.get("project_id"),
                "source_entry_id": thought["id"],
                "target": target,
                "context": {
                    "source_think_entry_id": thought["id"],
                    "problem": problem,
                    "hypothesis": hypothesis or "",
                },
            }
        )
        return {"think_entry": thought, "build_entry": build}

    def transition_entry(self, entry_id: str, data: Mapping[str, Any]) -> dict[str, Any]:
        """Create an explicitly requested next-mode Entry with provenance."""
        source = self.repository.get_entry(entry_id)
        target_mode = str(data.get("target_mode") or data.get("mode") or "").strip().lower()
        if target_mode not in ENTRY_TRANSITIONS.get(source["mode"], ()):
            raise EntrySystemError(f"不支援 {source['mode'].upper()} → {target_mode.upper() or '?'} 的轉換")
        next_action = _clean_text(data.get("next_action"), "下一步", limit=4_000)
        if target_mode == "build" and not next_action:
            raise EntrySystemError("轉成 BUILD 時必須提供下一步")
        context = dict(source.get("context") or {})
        context["transition"] = {
            "source_entry_id": source["id"],
            "source_mode": source["mode"],
            "target_mode": target_mode,
        }
        if target_mode == "learn":
            context["learn"] = {
                "question": _clean_text(data.get("learning_question"), "學習問題", limit=4_000),
                "stop_condition": _clean_text(data.get("stop_condition"), "停止條件", limit=2_000),
                "expected_output": _clean_text(data.get("expected_output"), "預期輸出", limit=2_000),
            }
        if data.get("target"):
            target = data["target"]
        elif "target_uri" in data or "target_type" in data:
            target = {"uri": data.get("target_uri"), "type": data.get("target_type")}
        else:
            target = source.get("target") or {}
        created = self.create_entry(
            {
                "name": _clean_text(data.get("name"), "名稱", limit=500) or f"{target_mode.upper()}: {source['name']}",
                "mode": target_mode,
                "kind": data.get("kind") or source.get("kind") or "task_entry",
                "status": "active",
                "intent": data.get("intent") or source.get("intent"),
                "next_action": next_action,
                "project_id": data.get("project_id") or source.get("project_id"),
                "source_entry_id": source["id"],
                "target": target,
                "context": context,
                "state": source.get("state") or {},
            }
        )
        self.repository.add_link(created["id"], "entry", source["id"], "transition", {"from_mode": source["mode"], "to_mode": target_mode})
        if data.get("complete_source") in (True, 1, "1", "true", "yes", "on"):
            self.update_entry(source["id"], {"status": "completed", "last_action": f"已轉換為 {target_mode.upper()} Entry"})
        return {"source_entry": self.repository.get_entry(source["id"]), "target_entry": self.repository.get_entry(created["id"])}

    def create_source_entry(self, data: Mapping[str, Any]) -> dict[str, Any]:
        source_kind = _clean_text(data.get("source_kind"), "來源類型", required=True, limit=48)
        source_id = _clean_text(data.get("source_id"), "來源 ID", required=True, limit=1_000)
        mode = str(data.get("mode") or "learn").strip().lower()
        if mode not in ENTRY_MODES:
            raise EntrySystemError("無效的 mode")
        title = _clean_text(data.get("title"), "來源標題", required=True, limit=500)
        next_action = _clean_text(data.get("next_action"), "下一步", limit=4_000)
        if not next_action and mode == "build":
            next_action = f"開始處理「{title}」"
        if not next_action and mode == "learn":
            next_action = f"帶著問題閱讀「{title}」"
        context: dict[str, Any] = {"source": {"kind": source_kind, "id": source_id}}
        if mode == "scan":
            context["scan"] = {
                "time_budget_minutes": 20,
                "item_limit": 12,
                "exit_condition": "完成項目上限或找到一個值得深入的來源",
            }
        entry = self.create_entry(
            {
                "name": f"{mode.upper()}: {title}",
                "mode": mode,
                "kind": "item_entry",
                "status": "active",
                "intent": _clean_text(data.get("intent"), "意圖", limit=8_000) or f"從 {source_kind} 進入",
                "next_action": next_action,
                "project_id": data.get("project_id"),
                "target_type": data.get("target_type") or "flowinone_view",
                "target_uri": data.get("target_uri"),
                "context": context,
            }
        )
        linked_type = {
            "resource": "resource", "collection": "collection", "draft_note": "note",
            "note": "note", "output_asset": "output_asset",
        }.get(source_kind, "gallery_item")
        self.repository.add_link(
            entry["id"], linked_type, source_id, "source",
            {"source_kind": source_kind, "target_uri": data.get("target_uri")},
        )
        return self.repository.get_entry(entry["id"])

    def create_snapshot(self, data: Mapping[str, Any]) -> dict[str, Any]:
        target_uri = _safe_target_uri(data.get("target_uri"))
        if not target_uri or not target_uri.startswith("/"):
            raise EntrySystemError("View snapshot 必須指向 Flowinone 內部頁面")
        mode = str(data.get("mode") or "learn").strip().lower()
        if mode not in ENTRY_MODES:
            raise EntrySystemError("無效的 mode")
        name = _clean_text(data.get("name"), "Snapshot 名稱", required=True, limit=500)
        next_action = _clean_text(data.get("next_action"), "下一步", limit=4_000)
        if mode == "build" and not next_action:
            next_action = f"從儲存視圖繼續：{name}"
        context = _object(data.get("context") or {}, "context")
        if mode == "scan":
            context["scan"] = {
                "time_budget_minutes": 20,
                "item_limit": 12,
                "exit_condition": "完成項目上限或標記一個下一步",
            }
        return self.create_entry(
            {
                "name": name,
                "mode": mode,
                "kind": "view_entry",
                "status": "active",
                "intent": _clean_text(data.get("intent"), "意圖", limit=8_000),
                "next_action": next_action,
                "target": {"type": "flowinone_view", "uri": target_uri},
                "context": context,
                "state": _object(data.get("state") or {}, "state"),
            }
        )

    def build_dashboard(self) -> dict[str, Any]:
        entries = self.repository.list_entries(mode="build", limit=150)
        active = [entry for entry in entries if entry["status"] == "active"]

        def score(entry: dict[str, Any]) -> tuple[int, str, str]:
            project = entry.get("project") or {}
            value = 100
            if entry.get("next_action"):
                value += 35
            if project.get("status") == "active":
                value += 45
            if project.get("current_entry_id") == entry.get("id"):
                value += 80
            if entry.get("last_opened_at"):
                value += 20
            return (value, entry.get("last_opened_at") or "", entry.get("updated_at") or "")

        continue_entry = max(active, key=score) if active else None
        blocked_entries = [entry for entry in entries if entry["status"] == "blocked"][:8]
        recent_entries = [entry for entry in entries if entry["status"] != "archived"][:8]

        project_id = (continue_entry or {}).get("project_id")
        if project_id:
            current_project = self.repository.get_project(project_id)
        else:
            projects = self.repository.list_projects(statuses=("active",), limit=20)
            prioritized = sorted(
                projects,
                key=lambda project: (
                    bool(project.get("current_entry_id")),
                    project.get("updated_at") or "",
                ),
                reverse=True,
            )
            current_project = self.repository.get_project(prioritized[0]["id"]) if prioritized else None

        return {
            "continue_entry": continue_entry,
            "current_project": current_project,
            "recent_entries": recent_entries,
            "blocked_entries": blocked_entries,
            "active_count": len(active),
            "project_count": len(self.repository.list_projects(statuses=("active",), limit=250)),
        }

    def resolve_destination(self, entry: Mapping[str, Any]) -> str:
        target = entry.get("target") or {}
        uri = _safe_target_uri(target.get("uri"))
        if not uri:
            return f"/entries/{entry['id']}/"
        if not uri.startswith("/"):
            return uri
        state_params = _state_query_parameters(entry.get("state") or {})
        if not state_params:
            return uri
        parsed = urlsplit(uri)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query.update(state_params)
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))

    def enter(self, entry_id: str) -> dict[str, Any]:
        entry = self.repository.mark_entered(entry_id)
        return {
            "entry": entry,
            "destination": self.resolve_destination(entry),
            # on_enter is retained for portable metadata. The local web app never
            # executes arbitrary commands; an explicit user click only redirects to
            # the validated target URI above.
            "declared_actions": (entry.get("on_enter") or {}).get("run_actions", []),
        }


__all__ = [
    "ENTRY_ALLOWED_SCHEMES",
    "ENTRY_TRANSITIONS",
    "EntryNotFound",
    "EntryService",
    "EntrySystemError",
    "ProjectNotFound",
]
