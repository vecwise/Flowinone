"""Add composite indexes used by Catalog source and keyset queries.

Revision ID: 0005_catalog_performance
Revises: 0004_catalog_workflows
Create Date: 2026-07-11
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0005_catalog_performance"
down_revision: Union[str, None] = "0004_catalog_workflows"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for statement in (
        "CREATE INDEX idx_catalog_origins_source_item ON catalog_origins(source_kind,stale,catalog_item_id)",
        "CREATE INDEX idx_catalog_origins_item_source_stale ON catalog_origins(catalog_item_id,source_kind,stale)",
        "CREATE INDEX idx_catalog_items_title ON catalog_items(title COLLATE NOCASE,id)",
        "CREATE INDEX idx_catalog_items_captured ON catalog_items(captured_at,id)",
        "CREATE INDEX idx_item_user_state_views ON item_user_state(open_count,last_viewed_at,catalog_item_id)",
    ):
        op.execute(statement)


def downgrade() -> None:
    for name in (
        "idx_item_user_state_views",
        "idx_catalog_items_captured",
        "idx_catalog_items_title",
        "idx_catalog_origins_item_source_stale",
        "idx_catalog_origins_source_item",
    ):
        op.execute(f"DROP INDEX IF EXISTS {name}")
