"""Persist named Navigator searches separately from transient browse sessions.

Revision ID: 0011_saved_catalog_searches
Revises: 0010_runtime_state
Create Date: 2026-08-08
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0011_saved_catalog_searches"
down_revision: Union[str, None] = "0010_runtime_state"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE saved_catalog_searches (
            id VARCHAR(32) NOT NULL PRIMARY KEY,
            label VARCHAR(80) NOT NULL,
            query_json TEXT NOT NULL,
            query_signature VARCHAR(64) NOT NULL,
            is_pinned INTEGER NOT NULL DEFAULT 1,
            created_at VARCHAR(40) NOT NULL,
            updated_at VARCHAR(40) NOT NULL,
            last_used_at VARCHAR(40)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX idx_saved_catalog_searches_display
        ON saved_catalog_searches(is_pinned, updated_at DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_saved_catalog_searches_display")
    op.execute("DROP TABLE IF EXISTS saved_catalog_searches")
