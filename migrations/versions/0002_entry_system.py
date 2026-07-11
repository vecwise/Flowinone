"""Add the local-first Entry system, Projects, and Entry event log.

Revision ID: 0002_entry_system
Revises: 0001_resource_library
Create Date: 2026-07-11
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0002_entry_system"
down_revision: Union[str, None] = "0001_resource_library"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE projects (
            id VARCHAR(32) NOT NULL PRIMARY KEY,
            name TEXT NOT NULL,
            status VARCHAR(20) NOT NULL,
            goal TEXT,
            next_action TEXT,
            current_entry_id VARCHAR(32),
            repositories_json TEXT NOT NULL DEFAULT '[]',
            notes_json TEXT NOT NULL DEFAULT '[]',
            views_json TEXT NOT NULL DEFAULT '[]',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at VARCHAR(40) NOT NULL,
            updated_at VARCHAR(40) NOT NULL,
            CONSTRAINT ck_projects_status
                CHECK (status IN ('active','paused','completed','archived'))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE entries (
            id VARCHAR(32) NOT NULL PRIMARY KEY,
            name TEXT NOT NULL,
            mode VARCHAR(20) NOT NULL,
            kind VARCHAR(48) NOT NULL,
            status VARCHAR(20) NOT NULL,
            intent TEXT,
            target_type VARCHAR(48),
            target_uri TEXT,
            context_json TEXT NOT NULL DEFAULT '{}',
            state_json TEXT NOT NULL DEFAULT '{}',
            on_enter_json TEXT NOT NULL DEFAULT '{}',
            next_action TEXT,
            last_action TEXT,
            project_id VARCHAR(32) REFERENCES projects(id) ON DELETE SET NULL,
            source_entry_id VARCHAR(32) REFERENCES entries(id) ON DELETE SET NULL,
            created_at VARCHAR(40) NOT NULL,
            updated_at VARCHAR(40) NOT NULL,
            last_opened_at VARCHAR(40),
            completed_at VARCHAR(40),
            CONSTRAINT ck_entries_mode
                CHECK (mode IN ('build','think','learn','scan','recover')),
            CONSTRAINT ck_entries_status
                CHECK (status IN ('active','blocked','completed','archived'))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE entry_events (
            id VARCHAR(32) NOT NULL PRIMARY KEY,
            entry_id VARCHAR(32) NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
            event_type VARCHAR(48) NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at VARCHAR(40) NOT NULL
        )
        """
    )
    for statement in (
        "CREATE INDEX idx_projects_status_updated ON projects (status, updated_at)",
        "CREATE INDEX ix_projects_current_entry_id ON projects (current_entry_id)",
        "CREATE INDEX idx_entries_dashboard ON entries (mode, status, last_opened_at, updated_at)",
        "CREATE INDEX idx_entries_project ON entries (project_id, status, updated_at)",
        "CREATE INDEX idx_entries_source ON entries (source_entry_id)",
        "CREATE INDEX idx_entry_events_entry_created ON entry_events (entry_id, created_at)",
    ):
        op.execute(statement)


def downgrade() -> None:
    for table_name in ("entry_events", "entries", "projects"):
        op.execute(f"DROP TABLE IF EXISTS {table_name}")
