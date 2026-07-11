"""Generalize Entry links to typed, multi-source provenance.

Revision ID: 0006_typed_entry_links
Revises: 0005_catalog_performance
Create Date: 2026-07-11
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0006_typed_entry_links"
down_revision: Union[str, None] = "0005_catalog_performance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE entry_links RENAME TO entry_links_legacy")
    op.execute(
        """
        CREATE TABLE entry_links (
            id VARCHAR(32) NOT NULL PRIMARY KEY,
            entry_id VARCHAR(32) NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
            linked_type VARCHAR(40) NOT NULL,
            linked_id TEXT NOT NULL,
            direction VARCHAR(16) NOT NULL DEFAULT 'source',
            relation_type VARCHAR(32) NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at VARCHAR(40) NOT NULL,
            UNIQUE(entry_id, linked_type, linked_id, direction, relation_type),
            CHECK(direction IN ('source','output'))
        )
        """
    )
    # A transition is represented on the new/target Entry as an incoming
    # typed source. Reverse traversal still exposes it as an outgoing link on
    # the original Entry.
    op.execute(
        """
        INSERT INTO entry_links(
            id,entry_id,linked_type,linked_id,direction,relation_type,metadata_json,created_at
        )
        SELECT lower(hex(randomblob(16))),target_entry_id,'entry',source_entry_id,
               'source',relation_type,metadata_json,created_at
        FROM entry_links_legacy
        """
    )
    op.execute("DROP TABLE entry_links_legacy")
    op.execute("CREATE INDEX idx_entry_links_owner ON entry_links(entry_id,direction,created_at)")
    op.execute("CREATE INDEX idx_entry_links_reverse ON entry_links(linked_type,linked_id,direction)")


def downgrade() -> None:
    op.execute("ALTER TABLE entry_links RENAME TO entry_links_typed")
    op.execute(
        """
        CREATE TABLE entry_links (
            source_entry_id VARCHAR(32) NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
            target_entry_id VARCHAR(32) NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
            relation_type VARCHAR(32) NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at VARCHAR(40) NOT NULL,
            PRIMARY KEY(source_entry_id,target_entry_id,relation_type)
        )
        """
    )
    op.execute(
        """
        INSERT OR IGNORE INTO entry_links(source_entry_id,target_entry_id,relation_type,metadata_json,created_at)
        SELECT linked_id,entry_id,relation_type,metadata_json,created_at
        FROM entry_links_typed
        WHERE linked_type='entry' AND direction='source'
        """
    )
    op.execute("DROP TABLE entry_links_typed")
    op.execute("CREATE INDEX idx_entry_links_target ON entry_links(target_entry_id,relation_type)")
