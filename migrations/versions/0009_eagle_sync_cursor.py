"""Persist resumable, versioned Eagle Catalog sync cursors.

Revision ID: 0009_eagle_sync_cursor
Revises: 0008_renderer_resource_schema
Create Date: 2026-07-24
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0009_eagle_sync_cursor"
down_revision: Union[str, None] = "0008_renderer_resource_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE catalog_sync_state ADD COLUMN cursor_version INTEGER")
    op.execute("ALTER TABLE catalog_sync_state ADD COLUMN cursor_json TEXT")


def downgrade() -> None:
    with op.batch_alter_table("catalog_sync_state", recreate="always") as batch:
        batch.drop_column("cursor_json")
        batch.drop_column("cursor_version")
