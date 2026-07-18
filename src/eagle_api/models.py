"""Stable value objects used by the Eagle Web API v2 client."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Generic, Mapping, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class EaglePage(Generic[T]):
    """One v2 list result, retaining pagination metadata."""

    items: list[T]
    total: int
    offset: int
    limit: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.items) < self.total

    @classmethod
    def from_response(cls, response: Mapping[str, Any]) -> "EaglePage[dict[str, Any]]":
        envelope = response.get("data")
        if not isinstance(envelope, Mapping) or not isinstance(envelope.get("data"), list):
            raise ValueError("Eagle response does not contain a paginated data list")
        items = list(envelope["data"])
        offset = int(envelope.get("offset") or 0)
        limit = int(envelope.get("limit") or len(items) or 1)
        total = int(envelope.get("total") or len(items))
        return cls(items=items, total=total, offset=offset, limit=limit)

    def to_dict(self) -> dict[str, Any]:
        return {
            "data": self.items,
            "total": self.total,
            "offset": self.offset,
            "limit": self.limit,
        }


@dataclass(frozen=True)
class EagleAIResult:
    """A semantic/visual match returned by the optional AI Search plugin."""

    item: dict[str, Any]
    score: float

    @classmethod
    def from_response_data(cls, value: Mapping[str, Any]) -> "EagleAIResult":
        item = value.get("item")
        if not isinstance(item, Mapping):
            raise ValueError("AI Search result is missing its item")
        return cls(item=dict(item), score=float(value.get("score") or 0.0))


@dataclass(frozen=True)
class EagleCapabilities:
    """Runtime feature state for the currently running Eagle application."""

    available: bool
    version: str | None = None
    build_version: str | None = None
    supports_smart_folders: bool = False
    supports_comments: bool | None = None
    ai_installed: bool = False
    ai_ready: bool = False
    ai_starting: bool = False
    ai_syncing: bool = False
    ai_sync_progress: float | None = None
    errors: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
