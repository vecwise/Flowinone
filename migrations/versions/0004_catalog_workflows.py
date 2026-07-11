"""Add the cross-source catalog, workflow links, discovery, and local AI artifacts.

Revision ID: 0004_catalog_workflows
Revises: 0003_knowledge_os
Create Date: 2026-07-11
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0004_catalog_workflows"
down_revision: Union[str, None] = "0003_knowledge_os"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Collection purpose (kind) remains independent from how membership is produced.
    for statement in (
        "ALTER TABLE collections ADD COLUMN membership_mode VARCHAR(20) NOT NULL DEFAULT 'manual'",
        "ALTER TABLE collections ADD COLUMN lifecycle_status VARCHAR(20) NOT NULL DEFAULT 'active'",
        "ALTER TABLE collections ADD COLUMN query_json TEXT NOT NULL DEFAULT '{}'",
        "ALTER TABLE collections ADD COLUMN generation_json TEXT NOT NULL DEFAULT '{}'",
        "ALTER TABLE collections ADD COLUMN portable_slug VARCHAR(160)",
        "ALTER TABLE collections ADD COLUMN last_refreshed_at VARCHAR(40)",
        "ALTER TABLE collection_items ADD COLUMN catalog_item_id VARCHAR(32)",
        "ALTER TABLE collection_items ADD COLUMN membership_source VARCHAR(20) NOT NULL DEFAULT 'manual'",
        "ALTER TABLE collection_items ADD COLUMN relation_score FLOAT",
        "ALTER TABLE collection_items ADD COLUMN reason_json TEXT NOT NULL DEFAULT '{}'",
    ):
        op.execute(statement)

    for statement in (
        """
        CREATE TABLE catalog_items (
            id VARCHAR(32) NOT NULL PRIMARY KEY,
            identity_key VARCHAR(160) NOT NULL UNIQUE,
            item_type VARCHAR(40) NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            thumbnail_ref TEXT,
            primary_detail_uri TEXT,
            original_url TEXT,
            duration_seconds FLOAT,
            captured_at VARCHAR(40),
            source_updated_at VARCHAR(40),
            indexed_at VARCHAR(40) NOT NULL,
            availability VARCHAR(20) NOT NULL DEFAULT 'available',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            content_fingerprint VARCHAR(128),
            is_deleted INTEGER NOT NULL DEFAULT 0
        )
        """,
        """
        CREATE TABLE catalog_origins (
            id VARCHAR(32) NOT NULL PRIMARY KEY,
            catalog_item_id VARCHAR(32) NOT NULL REFERENCES catalog_items(id) ON DELETE CASCADE,
            source_kind VARCHAR(32) NOT NULL,
            source_key TEXT NOT NULL,
            detail_uri TEXT,
            original_url TEXT,
            source_path TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            last_seen_at VARCHAR(40) NOT NULL,
            stale INTEGER NOT NULL DEFAULT 0,
            UNIQUE(source_kind, source_key)
        )
        """,
        """
        CREATE TABLE catalog_tags (
            id VARCHAR(32) NOT NULL PRIMARY KEY,
            name VARCHAR(160) NOT NULL,
            normalized_name VARCHAR(160) NOT NULL UNIQUE,
            created_at VARCHAR(40) NOT NULL
        )
        """,
        """
        CREATE TABLE catalog_item_tags (
            catalog_item_id VARCHAR(32) NOT NULL REFERENCES catalog_items(id) ON DELETE CASCADE,
            tag_id VARCHAR(32) NOT NULL REFERENCES catalog_tags(id) ON DELETE CASCADE,
            source VARCHAR(24) NOT NULL,
            confidence FLOAT,
            created_at VARCHAR(40) NOT NULL,
            PRIMARY KEY(catalog_item_id, tag_id, source)
        )
        """,
        """
        CREATE TABLE catalog_sync_state (
            source_kind VARCHAR(32) NOT NULL PRIMARY KEY,
            source_signature TEXT,
            status VARCHAR(20) NOT NULL,
            item_count INTEGER NOT NULL DEFAULT 0,
            error_message TEXT,
            synced_at VARCHAR(40) NOT NULL
        )
        """,
        """
        CREATE TABLE entry_links (
            source_entry_id VARCHAR(32) NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
            target_entry_id VARCHAR(32) NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
            relation_type VARCHAR(32) NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at VARCHAR(40) NOT NULL,
            PRIMARY KEY(source_entry_id, target_entry_id, relation_type)
        )
        """,
        """
        CREATE TABLE item_relations (
            source_item_id VARCHAR(32) NOT NULL REFERENCES catalog_items(id) ON DELETE CASCADE,
            target_item_id VARCHAR(32) NOT NULL REFERENCES catalog_items(id) ON DELETE CASCADE,
            relation_type VARCHAR(32) NOT NULL,
            score FLOAT NOT NULL,
            reason_json TEXT NOT NULL DEFAULT '{}',
            algorithm_version VARCHAR(40) NOT NULL,
            computed_at VARCHAR(40) NOT NULL,
            PRIMARY KEY(source_item_id, target_item_id, relation_type)
        )
        """,
        """
        CREATE TABLE collection_relations (
            source_collection_id VARCHAR(32) NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
            target_collection_id VARCHAR(32) NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
            relation_type VARCHAR(32) NOT NULL,
            score FLOAT NOT NULL,
            reason_json TEXT NOT NULL DEFAULT '{}',
            computed_at VARCHAR(40) NOT NULL,
            PRIMARY KEY(source_collection_id, target_collection_id, relation_type)
        )
        """,
        """
        CREATE TABLE item_user_state (
            catalog_item_id VARCHAR(32) NOT NULL PRIMARY KEY REFERENCES catalog_items(id) ON DELETE CASCADE,
            favorite INTEGER NOT NULL DEFAULT 0,
            hidden INTEGER NOT NULL DEFAULT 0,
            open_count INTEGER NOT NULL DEFAULT 0,
            last_viewed_at VARCHAR(40),
            updated_at VARCHAR(40) NOT NULL
        )
        """,
        """
        CREATE TABLE catalog_events (
            id VARCHAR(32) NOT NULL PRIMARY KEY,
            catalog_item_id VARCHAR(32) REFERENCES catalog_items(id) ON DELETE CASCADE,
            event_type VARCHAR(32) NOT NULL,
            event_value FLOAT,
            session_id VARCHAR(32),
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at VARCHAR(40) NOT NULL
        )
        """,
        """
        CREATE TABLE browse_sessions (
            id VARCHAR(32) NOT NULL PRIMARY KEY,
            query_json TEXT NOT NULL,
            query_signature VARCHAR(64) NOT NULL,
            focused_item_id VARCHAR(32) REFERENCES catalog_items(id) ON DELETE SET NULL,
            cursor TEXT,
            scroll_position INTEGER NOT NULL DEFAULT 0,
            status VARCHAR(20) NOT NULL DEFAULT 'active',
            created_at VARCHAR(40) NOT NULL,
            updated_at VARCHAR(40) NOT NULL
        )
        """,
        """
        CREATE TABLE catalog_artifacts (
            id VARCHAR(32) NOT NULL PRIMARY KEY,
            catalog_item_id VARCHAR(32) NOT NULL REFERENCES catalog_items(id) ON DELETE CASCADE,
            artifact_type VARCHAR(32) NOT NULL,
            content_text TEXT,
            content_json TEXT,
            provider VARCHAR(120),
            model VARCHAR(160),
            version VARCHAR(40) NOT NULL,
            input_fingerprint VARCHAR(128),
            is_current INTEGER NOT NULL DEFAULT 1,
            created_at VARCHAR(40) NOT NULL
        )
        """,
        """
        CREATE TABLE people (
            id VARCHAR(32) NOT NULL PRIMARY KEY,
            display_name TEXT,
            status VARCHAR(20) NOT NULL DEFAULT 'anonymous',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at VARCHAR(40) NOT NULL,
            updated_at VARCHAR(40) NOT NULL
        )
        """,
        """
        CREATE TABLE item_people (
            catalog_item_id VARCHAR(32) NOT NULL REFERENCES catalog_items(id) ON DELETE CASCADE,
            person_id VARCHAR(32) NOT NULL REFERENCES people(id) ON DELETE CASCADE,
            confidence FLOAT,
            source VARCHAR(24) NOT NULL,
            created_at VARCHAR(40) NOT NULL,
            PRIMARY KEY(catalog_item_id, person_id, source)
        )
        """,
    ):
        op.execute(statement)

    for statement in (
        "CREATE INDEX idx_catalog_items_type_time ON catalog_items(item_type, captured_at, id)",
        "CREATE INDEX idx_catalog_items_availability ON catalog_items(availability, is_deleted)",
        "CREATE INDEX idx_catalog_origins_item ON catalog_origins(catalog_item_id)",
        "CREATE INDEX idx_catalog_origins_source ON catalog_origins(source_kind, stale)",
        "CREATE INDEX idx_catalog_item_tags_tag ON catalog_item_tags(tag_id, catalog_item_id)",
        "CREATE INDEX idx_entry_links_target ON entry_links(target_entry_id, relation_type)",
        "CREATE INDEX idx_item_relations_target ON item_relations(target_item_id, relation_type, score)",
        "CREATE INDEX idx_collection_relations_target ON collection_relations(target_collection_id, score)",
        "CREATE INDEX idx_collection_items_catalog ON collection_items(catalog_item_id)",
        "CREATE INDEX idx_catalog_events_item_time ON catalog_events(catalog_item_id, created_at)",
        "CREATE INDEX idx_browse_sessions_updated ON browse_sessions(status, updated_at)",
        "CREATE INDEX idx_catalog_artifacts_item_type ON catalog_artifacts(catalog_item_id, artifact_type, is_current)",
        "CREATE INDEX idx_item_people_person ON item_people(person_id, catalog_item_id)",
    ):
        op.execute(statement)

    op.execute(
        """
        CREATE VIRTUAL TABLE catalog_fts USING fts5(
            catalog_item_id UNINDEXED,
            title,
            description,
            tags,
            extracted_text,
            tokenize='unicode61 remove_diacritics 2'
        )
        """
    )
    op.execute(
        """
        INSERT OR IGNORE INTO entry_links(source_entry_id, target_entry_id, relation_type, metadata_json, created_at)
        SELECT source_entry_id, id, 'transition', '{}', created_at
        FROM entries WHERE source_entry_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS catalog_fts")
    for table_name in (
        "item_people",
        "people",
        "catalog_artifacts",
        "browse_sessions",
        "catalog_events",
        "item_user_state",
        "collection_relations",
        "item_relations",
        "entry_links",
        "catalog_sync_state",
        "catalog_item_tags",
        "catalog_tags",
        "catalog_origins",
        "catalog_items",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table_name}")
    # SQLite cannot drop the appended columns without rebuilding the two legacy
    # tables. They are harmless on downgrade and older ORM mappings ignore them.
