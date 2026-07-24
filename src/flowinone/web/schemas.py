"""Pydantic contracts for every Flowinone ``/api/*`` JSON surface."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, RootModel, StrictBool, StrictFloat, StrictInt, StrictStr

from .api import ApiInput, ApiOutput, ApiQuery


CatalogSource = Literal["local", "eagle", "bookmarks", "resources"]


class EmptyRequest(ApiInput):
    pass


class ResourceCreateRequest(ApiInput):
    url: StrictStr = Field(min_length=1, max_length=4096)
    title: StrictStr = Field(default="", max_length=500)
    enqueue: StrictBool = True


class ResourcePatchRequest(ApiInput):
    title: StrictStr | None = Field(default=None, min_length=1, max_length=500)
    availability: Literal["unknown", "available", "dead", "blocked", "auth_required"] | None = None


class ResourceTagsRequest(ApiInput):
    tags: list[StrictStr] = Field(max_length=100)


class EnrichmentRequest(ApiInput):
    include_ai: StrictBool = False
    force: StrictBool = False


class ResourceSearchRequest(ApiInput):
    q: StrictStr = Field(default="", max_length=300)
    query: StrictStr = Field(default="", max_length=300)
    source_type: list[StrictStr] | StrictStr = []
    tag: StrictStr = Field(default="", max_length=160)
    domain: StrictStr = Field(default="", max_length=255)
    page: StrictInt = Field(default=1, ge=1)
    per_page: StrictInt = Field(default=30, ge=1, le=100)


class BookmarkThumbnailEnqueueRequest(ApiInput):
    ids: list[StrictStr] = Field(max_length=200)
    force: StrictBool = False
    priority: StrictInt | None = None


class CatalogEventRequest(ApiInput):
    event_type: Literal["open", "view", "favorite", "unfavorite", "hide", "unhide"]
    event_value: StrictFloat | StrictInt | None = None
    session_id: StrictStr | None = Field(default=None, max_length=64)
    metadata: dict[str, Any] | None = None


class CatalogQueryInput(ApiInput):
    q: StrictStr = Field(default="", max_length=300)
    scope: Literal["all", "gallery", "resources"] = "all"
    sources: list[CatalogSource] = list(CatalogSource.__args__)
    type: StrictStr | None = Field(default=None, max_length=40)
    item_type: StrictStr | None = Field(default=None, max_length=40)
    tags: list[StrictStr] = []
    tag_mode: Literal["any", "all"] = "any"
    favorite: StrictBool = False
    unviewed: StrictBool = False
    duration_min: StrictFloat | StrictInt | None = None
    duration_max: StrictFloat | StrictInt | None = None
    added_from: StrictStr | None = Field(default=None, max_length=40)
    added_to: StrictStr | None = Field(default=None, max_length=40)
    sort: StrictStr = Field(default="recently_added", max_length=40)
    seed: StrictInt | None = Field(default=None, ge=0)
    view: StrictStr | None = Field(default=None, max_length=20)
    limit: StrictInt = Field(default=48, ge=1, le=100)
    cursor: StrictStr | None = Field(default=None, max_length=500)


class CatalogSessionRequest(ApiInput):
    query: CatalogQueryInput
    focused_item_id: StrictStr | None = Field(default=None, max_length=64)
    cursor: StrictStr | None = Field(default=None, max_length=500)
    scroll_position: StrictInt = Field(default=0, ge=0)
    status: Literal["active", "closed"] = "active"


class PersonNameRequest(ApiInput):
    display_name: StrictStr = Field(default="", max_length=200)


class PersonLinkRequest(ApiInput):
    confidence: StrictFloat | StrictInt | None = Field(default=None, ge=0, le=1)


class CatalogSyncRequest(ApiInput):
    sources: list[CatalogSource] = Field(default=list(CatalogSource.__args__), min_length=1)


class ChromeImportFormRequest(ApiInput):
    format: Literal["json", "html", "htm"] | None = None


class BookmarkThumbnailStatusQuery(ApiQuery):
    ids: str = Field(default="", max_length=13000)


class EagleStreamQuery(ApiQuery):
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=30, ge=1, le=60)


class ResourceListQuery(ApiQuery):
    q: str = Field(default="", max_length=300)
    query: str = Field(default="", max_length=300)
    source_type: list[str] = []
    tag: str = Field(default="", max_length=160)
    domain: str = Field(default="", max_length=255)
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=30, ge=1, le=100)


class ResourceTagDeleteQuery(ApiQuery):
    source: str = Field(default="user", min_length=1, max_length=40)


class LimitQuery(ApiQuery):
    limit: int = Field(default=8, ge=1, le=100)


class RelatedLimitQuery(ApiQuery):
    limit: int = Field(default=18, ge=1, le=100)


class SessionLimitQuery(ApiQuery):
    limit: int = Field(default=3, ge=1, le=20)


class CatalogListQuery(ApiQuery):
    q: str = Field(default="", max_length=300)
    scope: Literal["all", "gallery", "resources"] = "all"
    source: list[CatalogSource] = []
    type: str = Field(default="", max_length=40)
    tags: list[str] = []
    tag_mode: Literal["any", "all"] = "any"
    favorite: bool = False
    unviewed: bool = False
    duration_min: float | None = None
    duration_max: float | None = None
    added_from: str = Field(default="", max_length=40)
    added_to: str = Field(default="", max_length=40)
    sort: Literal[
        "newest",
        "recently_added",
        "relevance",
        "title",
        "random",
        "most_viewed",
        "recently_viewed",
        "favorites",
    ] = "recently_added"
    seed: int = Field(default=0, ge=0)
    limit: int = Field(default=48, ge=1, le=100)
    cursor: str = Field(default="", max_length=500)


class GalleryListQuery(ApiQuery):
    q: str = Field(default="", max_length=300)
    source: list[Literal["local", "eagle", "bookmarks"]] = []
    type: Literal["image", "video", "bookmark"] | None = None
    tags: list[str] = []
    tag_mode: Literal["any", "all"] = "any"
    favorite: bool = False
    unviewed: bool = False
    duration_min: float | None = None
    duration_max: float | None = None
    added_from: str = Field(default="", max_length=40)
    added_to: str = Field(default="", max_length=40)
    sort: Literal[
        "newest",
        "recently_added",
        "relevance",
        "title",
        "random",
        "most_viewed",
        "recently_viewed",
        "favorites",
        "similar",
    ] = "recently_added"
    seed: int = Field(default=0, ge=0)
    view: Literal["grid", "compact"] = "grid"
    limit: int = Field(default=48, ge=1, le=96)
    cursor: str = Field(default="", max_length=500)


class ResourceOutput(ApiOutput):
    id: str
    title: str
    canonical_url: str
    detail_url: str | None = None
    tag_names: list[str] = []


class ImportSummaryOutput(ApiOutput):
    created: int = 0
    duplicates: int = 0
    failed: int = 0
    jobs_queued: int = 0
    errors: list[dict[str, Any]] = []


class ResourceCreateOutput(ApiOutput):
    resource: ResourceOutput
    import_summary: ImportSummaryOutput = Field(alias="import")


class ResourceListOutput(ApiOutput):
    items: list[ResourceOutput]
    total: int
    page: int | None = None
    per_page: int | None = None


class JobOutput(ApiOutput):
    id: str
    job_type: str
    status: str


class JobsOutput(ApiOutput):
    jobs: list[JobOutput]


class ResourceItemsOutput(ApiOutput):
    items: list[ResourceOutput]


class ThumbnailStatusOutput(ApiOutput):
    id: str
    status: str
    thumbnail_url: str | None = None
    provider: str | None = None
    error: str | None = None


class ThumbnailStatusesOutput(ApiOutput):
    items: list[ThumbnailStatusOutput]


class ThumbnailEnqueueOutput(ThumbnailStatusesOutput):
    queued: list[str]


class EagleStreamItemOutput(ApiOutput):
    id: str
    name: str | None = None
    thumbnail_route: str | None = None
    detail_url: str
    media_type: str | None = None
    ext: str | None = None


class EagleStreamOutput(ApiOutput):
    items: list[EagleStreamItemOutput]
    nextOffset: int


class CatalogItemOutput(ApiOutput):
    id: str
    title: str
    item_type: str
    tags: list[str] = []
    sources: list[str] = []


class CatalogFacetsOutput(ApiOutput):
    sources: dict[str, int]
    types: dict[str, int]
    tags: list[dict[str, Any]]
    sync: dict[str, dict[str, Any]]


class CatalogListOutput(ApiOutput):
    items: list[CatalogItemOutput]
    next_cursor: str | None
    total_estimate: int
    query: dict[str, Any]
    facets: CatalogFacetsOutput


class CatalogItemsOutput(ApiOutput):
    items: list[CatalogItemOutput]


class RelationsRebuildOutput(ApiOutput):
    items: int
    relations: int


class CatalogSessionOutput(ApiOutput):
    id: str
    query: dict[str, Any]
    focused_item_id: str | None = None
    cursor: str | None = None
    scroll_position: int
    status: str


class CatalogSessionsOutput(ApiOutput):
    items: list[CatalogSessionOutput]


class PersonOutput(ApiOutput):
    id: str
    display_name: str | None = None
    status: str


class SyncStateOutput(ApiOutput):
    status: str
    error: str | None = None


class CatalogSyncOutput(RootModel[dict[str, SyncStateOutput]]):
    pass


class CatalogSyncStatusOutput(ApiOutput):
    sources: dict[str, SyncStateOutput]


class GallerySourceOutput(ApiOutput):
    key: str
    name: str


class GalleryItemOutput(ApiOutput):
    id: str
    source: GallerySourceOutput
    media_type: str
    title: str
    thumbnail_url: str
    detail_url: str


class GalleryItemsOutput(ApiOutput):
    items: list[GalleryItemOutput]
    next_cursor: str | None
    total_estimate: int
    query: dict[str, Any]
    facets: dict[str, Any]
    source_errors: dict[str, str]


class GallerySourceStatusOutput(ApiOutput):
    key: str
    name: str
    count: int
    available: bool
    error: str | None = None


class GallerySourcesOutput(ApiOutput):
    items: list[GallerySourceStatusOutput]
