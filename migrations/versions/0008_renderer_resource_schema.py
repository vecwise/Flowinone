"""Remove inactive Resource workflow and personal-note storage.

Revision ID: 0008_renderer_resource_schema
Revises: 0007_renderer_only_cleanup
Create Date: 2026-07-18
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0008_renderer_resource_schema"
down_revision: Union[str, None] = "0007_renderer_only_cleanup"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Keep Resource data for rendering while deleting retired workflow fields."""
    op.execute("DROP INDEX IF EXISTS idx_resources_workflow")
    with op.batch_alter_table("resources", recreate="always") as batch:
        batch.drop_constraint("ck_resources_reading_state", type_="check")
        batch.drop_constraint("ck_resources_disposition", type_="check")
        batch.drop_constraint("ck_resources_priority", type_="check")
        batch.drop_column("reading_state")
        batch.drop_column("disposition")
        batch.drop_column("priority")
        batch.drop_column("saved_reason")
        batch.drop_column("read_at")
        batch.drop_column("user_note")

    op.execute(
        """
        CREATE TABLE resource_fts_backup AS
        SELECT resource_id, title, summary_one_line, summary_short,
               why_this_matters, extracted_text
        FROM resource_fts
        """
    )
    op.execute("DROP TABLE resource_fts")
    op.execute(
        """
        CREATE VIRTUAL TABLE resource_fts USING fts5(
            resource_id UNINDEXED,
            title,
            summary_one_line,
            summary_short,
            why_this_matters,
            extracted_text,
            tokenize='unicode61 remove_diacritics 2'
        )
        """
    )
    op.execute(
        """
        INSERT INTO resource_fts (
            resource_id, title, summary_one_line, summary_short,
            why_this_matters, extracted_text
        )
        SELECT resource_id, title, summary_one_line, summary_short,
               why_this_matters, extracted_text
        FROM resource_fts_backup
        """
    )
    op.execute("DROP TABLE resource_fts_backup")


def downgrade() -> None:
    raise RuntimeError(
        "0008 is intentionally irreversible because it deletes inactive workflow fields"
    )
