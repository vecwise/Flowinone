"""Opt-in authoritative Chrome snapshots, including unsupported URL nodes."""

from alembic import op

revision = "0014_chrome_mirror"
down_revision = "0013_bookmark_bindings"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE bookmark_sources ADD COLUMN sync_mode TEXT NOT NULL DEFAULT 'reviewed' CHECK(sync_mode IN ('reviewed','mirror'))")
    op.execute("ALTER TABLE bookmark_sources ADD COLUMN last_snapshot_sha256 TEXT")
    op.execute("ALTER TABLE bookmark_sources ADD COLUMN last_synced_at TEXT")
    op.execute("""CREATE TABLE chrome_bookmark_nodes (
        source_id TEXT NOT NULL REFERENCES bookmark_sources(id) ON DELETE CASCADE,
        guid TEXT NOT NULL,
        chrome_id TEXT NOT NULL,
        date_added TEXT NOT NULL,
        url TEXT NOT NULL,
        title TEXT NOT NULL,
        folder_path TEXT NOT NULL,
        folder_guids_json TEXT NOT NULL,
        catalog_item_id TEXT REFERENCES catalog_items(id) ON DELETE SET NULL,
        resource_id TEXT REFERENCES resources(id) ON DELETE SET NULL,
        unsupported_reason TEXT,
        observed_at TEXT NOT NULL,
        PRIMARY KEY(source_id,guid),
        UNIQUE(source_id,chrome_id)
    )""")
    op.execute("CREATE INDEX idx_chrome_node_catalog ON chrome_bookmark_nodes(catalog_item_id)")
    op.execute("CREATE INDEX idx_chrome_node_resource ON chrome_bookmark_nodes(resource_id)")


def downgrade():
    if op.get_bind().exec_driver_sql("SELECT 1 FROM bookmark_sources WHERE sync_mode='mirror' LIMIT 1").scalar():
        raise RuntimeError("Disable Chrome mirror mode before downgrading")
    op.execute("DROP TABLE chrome_bookmark_nodes")
    with op.batch_alter_table("bookmark_sources") as batch:
        for name in ("last_synced_at", "last_snapshot_sha256", "sync_mode"):
            batch.drop_column(name)
