"""Create the Resource Library, curation, and Obsidian bridge schema.

Revision ID: 0001_resource_library
Revises: None
Create Date: 2026-07-10
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0001_resource_library"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE_DDL = (
    """
    CREATE TABLE collections (
        id VARCHAR(32) NOT NULL PRIMARY KEY,
        title TEXT NOT NULL,
        description TEXT,
        kind VARCHAR(24) NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        CONSTRAINT ck_collections_kind
            CHECK (kind IN ('inspiration','project','reading_list'))
    )
    """,
    """
    CREATE TABLE draft_notes (
        id VARCHAR(32) NOT NULL PRIMARY KEY,
        title TEXT NOT NULL,
        note_type VARCHAR(24) NOT NULL,
        body TEXT NOT NULL,
        status VARCHAR(20) NOT NULL,
        obsidian_path TEXT,
        export_hash VARCHAR(64),
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        CONSTRAINT ck_draft_notes_type CHECK (note_type IN ('literature','synthesis')),
        CONSTRAINT ck_draft_notes_status CHECK (status IN ('draft','exported'))
    )
    """,
    """
    CREATE TABLE resource_state (
        key VARCHAR(120) NOT NULL PRIMARY KEY,
        value TEXT,
        updated_at VARCHAR(40) NOT NULL
    )
    """,
    """
    CREATE TABLE resources (
        id VARCHAR(32) NOT NULL PRIMARY KEY,
        original_url TEXT NOT NULL,
        canonical_url TEXT NOT NULL,
        url_hash VARCHAR(64) NOT NULL UNIQUE,
        title TEXT,
        description TEXT,
        source_type VARCHAR(32) NOT NULL,
        source_platform VARCHAR(64),
        domain VARCHAR(255),
        author TEXT,
        language VARCHAR(32),
        mime_type VARCHAR(255),
        reading_state VARCHAR(20) NOT NULL,
        disposition VARCHAR(20) NOT NULL,
        availability VARCHAR(20) NOT NULL,
        priority INTEGER NOT NULL,
        saved_reason TEXT,
        published_at VARCHAR(40),
        captured_at VARCHAR(40) NOT NULL,
        last_checked_at VARCHAR(40),
        read_at VARCHAR(40),
        favicon_path TEXT,
        thumbnail_path TEXT,
        screenshot_path TEXT,
        thumbnail_media_id VARCHAR(64),
        summary_one_line TEXT,
        summary_short TEXT,
        summary_structured_json TEXT,
        why_this_matters TEXT,
        user_note TEXT,
        content_hash VARCHAR(64),
        enrichment_status VARCHAR(20) NOT NULL,
        enrichment_error TEXT,
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        CONSTRAINT ck_resources_reading_state
            CHECK (reading_state IN ('inbox','unread','skimmed','reading','digested')),
        CONSTRAINT ck_resources_disposition
            CHECK (disposition IN ('active','archived','rejected')),
        CONSTRAINT ck_resources_availability
            CHECK (availability IN ('unknown','available','dead','blocked','auth_required')),
        CONSTRAINT ck_resources_priority CHECK (priority BETWEEN 0 AND 5)
    )
    """,
    """
    CREATE TABLE tags (
        id VARCHAR(32) NOT NULL PRIMARY KEY,
        name VARCHAR(120) NOT NULL,
        normalized_name VARCHAR(120) NOT NULL UNIQUE,
        category VARCHAR(64),
        created_at VARCHAR(40) NOT NULL
    )
    """,
    """
    CREATE TABLE ai_artifacts (
        id VARCHAR(32) NOT NULL PRIMARY KEY,
        resource_id VARCHAR(32) NOT NULL
            REFERENCES resources(id) ON DELETE CASCADE,
        artifact_type VARCHAR(48) NOT NULL,
        content_text TEXT,
        content_json TEXT,
        provider VARCHAR(120),
        model VARCHAR(160),
        prompt_version VARCHAR(40),
        input_hash VARCHAR(64),
        is_current INTEGER NOT NULL,
        created_at VARCHAR(40) NOT NULL
    )
    """,
    """
    CREATE TABLE collection_items (
        id VARCHAR(32) NOT NULL PRIMARY KEY,
        collection_id VARCHAR(32) NOT NULL
            REFERENCES collections(id) ON DELETE CASCADE,
        source_kind VARCHAR(32) NOT NULL,
        source_id TEXT NOT NULL,
        title_snapshot TEXT,
        url_snapshot TEXT,
        thumbnail_snapshot TEXT,
        annotation TEXT,
        position INTEGER NOT NULL,
        added_at VARCHAR(40) NOT NULL,
        CONSTRAINT uq_collection_item_source
            UNIQUE (collection_id, source_kind, source_id)
    )
    """,
    """
    CREATE TABLE draft_note_sources (
        id VARCHAR(32) NOT NULL PRIMARY KEY,
        draft_note_id VARCHAR(32) NOT NULL
            REFERENCES draft_notes(id) ON DELETE CASCADE,
        source_kind VARCHAR(32) NOT NULL,
        source_id TEXT NOT NULL,
        citation_label TEXT,
        url_snapshot TEXT,
        annotation TEXT,
        position INTEGER NOT NULL,
        added_at VARCHAR(40) NOT NULL,
        CONSTRAINT uq_draft_note_source
            UNIQUE (draft_note_id, source_kind, source_id)
    )
    """,
    """
    CREATE TABLE note_exports (
        id VARCHAR(32) NOT NULL PRIMARY KEY,
        draft_note_id VARCHAR(32) NOT NULL
            REFERENCES draft_notes(id) ON DELETE CASCADE,
        target_path TEXT NOT NULL,
        content_hash VARCHAR(64) NOT NULL,
        status VARCHAR(20) NOT NULL,
        error_message TEXT,
        exported_at VARCHAR(40) NOT NULL
    )
    """,
    """
    CREATE TABLE processing_jobs (
        id VARCHAR(32) NOT NULL PRIMARY KEY,
        job_key VARCHAR(160) NOT NULL UNIQUE,
        resource_id VARCHAR(32) REFERENCES resources(id) ON DELETE CASCADE,
        job_type VARCHAR(48) NOT NULL,
        status VARCHAR(20) NOT NULL,
        priority INTEGER NOT NULL,
        attempts INTEGER NOT NULL,
        max_attempts INTEGER NOT NULL,
        payload_json TEXT,
        input_hash VARCHAR(64),
        run_after VARCHAR(40) NOT NULL,
        lease_owner VARCHAR(120),
        lease_expires_at VARCHAR(40),
        error_message TEXT,
        created_at VARCHAR(40) NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        started_at VARCHAR(40),
        finished_at VARCHAR(40),
        CONSTRAINT ck_processing_jobs_status
            CHECK (status IN ('pending','running','retry','complete','failed','cancelled'))
    )
    """,
    """
    CREATE TABLE resource_contents (
        id VARCHAR(32) NOT NULL PRIMARY KEY,
        resource_id VARCHAR(32) NOT NULL
            REFERENCES resources(id) ON DELETE CASCADE,
        content_type VARCHAR(40) NOT NULL,
        content_path TEXT NOT NULL,
        mime_type VARCHAR(255),
        content_hash VARCHAR(64),
        byte_size INTEGER,
        extractor_name VARCHAR(120),
        extractor_version VARCHAR(40),
        created_at VARCHAR(40) NOT NULL
    )
    """,
    """
    CREATE TABLE resource_note_links (
        resource_id VARCHAR(32) NOT NULL
            REFERENCES resources(id) ON DELETE CASCADE,
        draft_note_id VARCHAR(32) NOT NULL
            REFERENCES draft_notes(id) ON DELETE CASCADE,
        role VARCHAR(32) NOT NULL,
        created_at VARCHAR(40) NOT NULL,
        PRIMARY KEY (resource_id, draft_note_id, role)
    )
    """,
    """
    CREATE TABLE resource_origins (
        id VARCHAR(32) NOT NULL PRIMARY KEY,
        resource_id VARCHAR(32) NOT NULL
            REFERENCES resources(id) ON DELETE CASCADE,
        source VARCHAR(40) NOT NULL,
        source_key TEXT,
        source_title TEXT,
        folder_path TEXT,
        dedupe_key VARCHAR(64) NOT NULL UNIQUE,
        captured_at VARCHAR(40) NOT NULL,
        first_seen_at VARCHAR(40) NOT NULL,
        last_seen_at VARCHAR(40) NOT NULL
    )
    """,
    """
    CREATE TABLE resource_tags (
        resource_id VARCHAR(32) NOT NULL
            REFERENCES resources(id) ON DELETE CASCADE,
        tag_id VARCHAR(32) NOT NULL
            REFERENCES tags(id) ON DELETE CASCADE,
        source VARCHAR(24) NOT NULL,
        confidence FLOAT,
        created_at VARCHAR(40) NOT NULL,
        PRIMARY KEY (resource_id, tag_id, source),
        CONSTRAINT ck_resource_tags_source
            CHECK (source IN ('user','ai','chrome_folder','imported','rule'))
    )
    """,
)


INDEX_DDL = (
    "CREATE INDEX idx_resources_source_type ON resources (source_type)",
    "CREATE INDEX ix_resources_thumbnail_media_id ON resources (thumbnail_media_id)",
    "CREATE INDEX idx_resources_workflow ON resources (disposition, reading_state, priority)",
    "CREATE INDEX idx_resources_domain ON resources (domain)",
    "CREATE INDEX idx_resources_captured_at ON resources (captured_at)",
    "CREATE INDEX idx_ai_artifacts_resource_type ON ai_artifacts (resource_id, artifact_type)",
    "CREATE INDEX idx_collection_items_order ON collection_items (collection_id, position, added_at)",
    "CREATE INDEX idx_draft_note_sources_order ON draft_note_sources (draft_note_id, position)",
    "CREATE INDEX idx_note_exports_note ON note_exports (draft_note_id, exported_at)",
    "CREATE INDEX idx_processing_jobs_ready ON processing_jobs (status, run_after, priority)",
    "CREATE INDEX idx_processing_jobs_resource ON processing_jobs (resource_id)",
    "CREATE INDEX idx_resource_contents_resource_type ON resource_contents (resource_id, content_type)",
    "CREATE INDEX idx_resource_origins_resource ON resource_origins (resource_id)",
    "CREATE INDEX idx_resource_origins_source ON resource_origins (source, folder_path)",
)


def upgrade() -> None:
    for statement in TABLE_DDL:
        op.execute(statement)
    for statement in INDEX_DDL:
        op.execute(statement)
    op.execute(
        """
        CREATE VIRTUAL TABLE resource_fts USING fts5(
            resource_id UNINDEXED,
            title,
            summary_one_line,
            summary_short,
            why_this_matters,
            user_note,
            extracted_text,
            tokenize='unicode61 remove_diacritics 2'
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS resource_fts")
    for table_name in (
        "resource_tags",
        "resource_origins",
        "resource_note_links",
        "resource_contents",
        "processing_jobs",
        "note_exports",
        "draft_note_sources",
        "collection_items",
        "ai_artifacts",
        "tags",
        "resources",
        "resource_state",
        "draft_notes",
        "collections",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table_name}")
