"""Remove the knowledge workflow and curation subsystems.

Revision ID: 0007_renderer_only_cleanup
Revises: 0006_typed_entry_links
Create Date: 2026-07-18
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0007_renderer_only_cleanup"
down_revision: Union[str, None] = "0006_typed_entry_links"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Delete workflow-owned tables while preserving renderer projections."""
    for table_name in (
        "collection_relations",
        "collection_items",
        "collections",
        "note_exports",
        "resource_note_links",
        "draft_note_sources",
        "draft_notes",
        "entry_links",
        "entry_events",
        "resource_modes",
        "resource_projects",
        "asset_sources",
        "decisions",
        "output_assets",
        "entries",
        "projects",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table_name}")

    op.execute("DELETE FROM processing_jobs WHERE job_type='export_obsidian'")
    op.execute("DELETE FROM catalog_events WHERE event_type='add_to_collection'")
    # The FTS schema is retained for compatibility with existing Resource DBs,
    # but renderer search no longer indexes personal-note text.
    op.execute("UPDATE resource_fts SET user_note=''")


def downgrade() -> None:
    raise RuntimeError(
        "0007 is intentionally irreversible because it deletes workflow-owned data"
    )
