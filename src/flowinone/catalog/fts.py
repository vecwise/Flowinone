"""Transaction-scoped Catalog FTS updates without repeated UNINDEXED ID scans."""

from sqlalchemy import text


class CatalogSearchIndex:
    """Read FTS IDs once, then address existing documents by SQLite rowid.

    catalog_item_id is UNINDEXED in FTS5. Looking it up for every source item
    scans the entire virtual table and keeps bulk writers locked for minutes.
    An instance belongs to one connection/transaction and must not be reused
    after commit or rollback.
    """

    def __init__(self, connection, item_ids=None):
        self.connection = connection
        self.rows = {}
        selected = set(item_ids) if item_ids is not None else None
        if selected is not None and not selected:
            return
        for row in connection.execute(text(
            "SELECT rowid,catalog_item_id,title,description,tags,extracted_text FROM catalog_fts"
        )).mappings():
            if selected is None or row["catalog_item_id"] in selected:
                self.rows[row["catalog_item_id"]] = dict(row)

    def get(self, item_id):
        return self.rows.get(item_id)

    def replace(self, item_id, *, title, description, tags, extracted_text):
        values = dict(catalog_item_id=item_id, title=title or "", description=description or "",
                      tags=tags or "", extracted_text=extracted_text or "")
        previous = self.get(item_id)
        if previous and all((previous[key] or "") == value for key, value in values.items()):
            return
        if previous:
            values["rowid"] = previous["rowid"]
            self.connection.execute(text(
                "UPDATE catalog_fts SET title=:title,description=:description,tags=:tags,"
                "extracted_text=:extracted_text WHERE rowid=:rowid"
            ), values)
        else:
            result = self.connection.execute(text(
                "INSERT INTO catalog_fts(catalog_item_id,title,description,tags,extracted_text) "
                "VALUES(:catalog_item_id,:title,:description,:tags,:extracted_text)"
            ), values)
            values["rowid"] = result.lastrowid
        self.rows[item_id] = values
