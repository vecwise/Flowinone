"""Persist review-only canonical choices for Local duplicate groups.

Revision ID: 0012_catalog_duplicate_reviews
Revises: 0011_saved_catalog_searches
Create Date: 2026-08-09
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0012_catalog_duplicate_reviews"
down_revision: Union[str, None] = "0011_saved_catalog_searches"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE catalog_duplicate_reviews (
            content_hash VARCHAR(72) NOT NULL PRIMARY KEY,
            canonical_item_id VARCHAR(32) NOT NULL
                REFERENCES catalog_items(id) ON DELETE CASCADE,
            updated_at VARCHAR(40) NOT NULL
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS catalog_duplicate_reviews")
