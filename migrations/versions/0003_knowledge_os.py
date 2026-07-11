"""Add Knowledge OS contexts, decisions, Output Assets, and WRITE mode.

Revision ID: 0003_knowledge_os
Revises: 0002_entry_system
Create Date: 2026-07-11
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0003_knowledge_os"
down_revision: Union[str, None] = "0002_entry_system"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("entries", recreate="always") as batch_op:
        batch_op.drop_constraint("ck_entries_mode", type_="check")
        batch_op.create_check_constraint(
            "ck_entries_mode",
            "mode IN ('build','think','learn','scan','recover','write')",
        )

    op.execute(
        """
        CREATE TABLE resource_projects (
            resource_id VARCHAR(32) NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
            project_id VARCHAR(32) NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            relevance FLOAT NOT NULL DEFAULT 1.0,
            source VARCHAR(20) NOT NULL DEFAULT 'user',
            created_at VARCHAR(40) NOT NULL,
            PRIMARY KEY (resource_id, project_id),
            CONSTRAINT ck_resource_projects_source CHECK (source IN ('user','ai','system'))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE resource_modes (
            resource_id VARCHAR(32) NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
            mode VARCHAR(20) NOT NULL,
            relevance FLOAT NOT NULL DEFAULT 1.0,
            source VARCHAR(20) NOT NULL DEFAULT 'user',
            created_at VARCHAR(40) NOT NULL,
            PRIMARY KEY (resource_id, mode),
            CONSTRAINT ck_resource_modes_mode
                CHECK (mode IN ('build','think','learn','scan','recover','write')),
            CONSTRAINT ck_resource_modes_source CHECK (source IN ('user','ai','system'))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE output_assets (
            id VARCHAR(32) NOT NULL PRIMARY KEY,
            title TEXT NOT NULL,
            asset_type VARCHAR(48) NOT NULL,
            project_id VARCHAR(32) REFERENCES projects(id) ON DELETE SET NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'draft',
            version VARCHAR(40) NOT NULL DEFAULT '0.1',
            body TEXT NOT NULL DEFAULT '',
            export_path TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at VARCHAR(40) NOT NULL,
            updated_at VARCHAR(40) NOT NULL,
            CONSTRAINT ck_output_assets_status
                CHECK (status IN ('draft','ready','exported','archived'))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE asset_sources (
            asset_id VARCHAR(32) NOT NULL REFERENCES output_assets(id) ON DELETE CASCADE,
            source_type VARCHAR(32) NOT NULL,
            source_id TEXT NOT NULL,
            citation_label TEXT,
            created_at VARCHAR(40) NOT NULL,
            PRIMARY KEY (asset_id, source_type, source_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE decisions (
            id VARCHAR(32) NOT NULL PRIMARY KEY,
            project_id VARCHAR(32) REFERENCES projects(id) ON DELETE CASCADE,
            question TEXT NOT NULL,
            decision TEXT NOT NULL,
            rationale TEXT,
            alternatives_json TEXT NOT NULL DEFAULT '[]',
            created_at VARCHAR(40) NOT NULL
        )
        """
    )
    for statement in (
        "CREATE INDEX idx_resource_projects_lookup ON resource_projects (project_id, relevance)",
        "CREATE INDEX idx_resource_modes_lookup ON resource_modes (mode, relevance)",
        "CREATE INDEX idx_output_assets_project_status ON output_assets (project_id, status, updated_at)",
        "CREATE INDEX idx_asset_sources_reverse ON asset_sources (source_type, source_id)",
        "CREATE INDEX idx_decisions_project_created ON decisions (project_id, created_at)",
    ):
        op.execute(statement)


def downgrade() -> None:
    for table_name in (
        "decisions",
        "asset_sources",
        "output_assets",
        "resource_modes",
        "resource_projects",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table_name}")
    op.execute("UPDATE entries SET mode='think' WHERE mode='write'")
    with op.batch_alter_table("entries", recreate="always") as batch_op:
        batch_op.drop_constraint("ck_entries_mode", type_="check")
        batch_op.create_check_constraint(
            "ck_entries_mode",
            "mode IN ('build','think','learn','scan','recover')",
        )
