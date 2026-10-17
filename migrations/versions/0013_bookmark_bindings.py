"""Add opt-in Chrome identities without replacing content or personal state.

Revision ID: 0013_bookmark_bindings
Revises: 0012_catalog_duplicate_reviews
"""

from alembic import op

revision = "0013_bookmark_bindings"
down_revision = "0012_catalog_duplicate_reviews"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in (
        """CREATE TABLE bookmark_sources (
            id TEXT PRIMARY KEY NOT NULL,
            profile_path TEXT NOT NULL,
            storage_scope TEXT NOT NULL,
            bookmark_path TEXT NOT NULL UNIQUE,
            scope_folder_guid TEXT NOT NULL,
            scope_folder_path TEXT NOT NULL,
            baseline_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(profile_path, storage_scope)
        )""",
        """CREATE TABLE bookmark_bindings (
            id TEXT PRIMARY KEY NOT NULL,
            source_id TEXT NOT NULL REFERENCES bookmark_sources(id),
            guid TEXT,
            chrome_id TEXT,
            date_added TEXT NOT NULL,
            catalog_item_id TEXT NOT NULL REFERENCES catalog_items(id),
            resource_id TEXT NOT NULL REFERENCES resources(id),
            applied_url TEXT NOT NULL,
            canonical_url TEXT NOT NULL,
            observed_url TEXT NOT NULL,
            folder_path TEXT NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('matched','conflict','missing')),
            conflict_reason TEXT,
            url_revision INTEGER NOT NULL DEFAULT 0,
            last_seen_at TEXT NOT NULL,
            CHECK(guid IS NOT NULL OR chrome_id IS NOT NULL)
        )""",
        "CREATE UNIQUE INDEX uq_bookmark_guid ON bookmark_bindings(source_id,guid) WHERE guid IS NOT NULL",
        "CREATE UNIQUE INDEX uq_bookmark_chrome_id ON bookmark_bindings(source_id,chrome_id) WHERE chrome_id IS NOT NULL",
        "CREATE INDEX idx_bookmark_binding_catalog ON bookmark_bindings(catalog_item_id)",
        "CREATE INDEX idx_bookmark_binding_resource ON bookmark_bindings(resource_id)",
        """CREATE TABLE bookmark_url_history (
            id TEXT PRIMARY KEY NOT NULL,
            binding_id TEXT NOT NULL REFERENCES bookmark_bindings(id),
            old_url TEXT NOT NULL,
            new_url TEXT NOT NULL,
            revision INTEGER NOT NULL,
            changed_at TEXT NOT NULL,
            UNIQUE(binding_id,revision)
        )""",
    ):
        op.execute(statement)


def downgrade() -> None:
    # Source origins refer to these IDs as strings, so refuse to discard an
    # active binding registry through a routine schema downgrade.
    if op.get_bind().exec_driver_sql("SELECT COUNT(*) FROM bookmark_bindings").scalar():
        raise RuntimeError("Bookmark bindings exist; restore the pre-backfill backup instead")
    for table in ("bookmark_url_history", "bookmark_bindings", "bookmark_sources"):
        op.execute(f"DROP TABLE {table}")
