"""Persist worker heartbeats for truthful UI health state.

Revision ID: 0010_runtime_state
Revises: 0009_eagle_sync_cursor
Create Date: 2026-07-24
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0010_runtime_state"
down_revision: Union[str, None] = "0009_eagle_sync_cursor"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE runtime_state (
            component TEXT PRIMARY KEY,
            heartbeat_at TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS runtime_state")
