"""Checkpointed Eagle projection, isolated from other Catalog sources."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Sequence
from urllib.parse import quote

from sqlalchemy import text

from src.file_handler.eagle_integration import get_eagle_catalog_page, get_eagle_catalog_source
from src.flowinone.resource_library.models import new_id, utc_now_text

from .query import _json

EAGLE_SYNC_CURSOR_VERSION = 1
EAGLE_SYNC_FINGERPRINT_VERSION = 1
EAGLE_SYNC_PAGE_SIZE = 500


class EagleSyncMixin:
    """Eagle-specific operations used by CatalogSyncService."""

    @staticmethod
    def _eagle_source_tokens(source: dict[str, Any]) -> tuple[str, str]:
        identity = str(source.get("identity") or "")
        if not identity:
            raise ValueError("Eagle catalog source identity is missing")
        signature = hashlib.sha256(identity.encode()).hexdigest()
        version = str(source.get("version") or "")
        snapshot = (
            hashlib.sha256(f"{identity}\0{version}".encode()).hexdigest()
            if version
            else ""
        )
        return signature, snapshot

    @staticmethod
    def _eagle_item_fingerprint(row: dict[str, Any]) -> str:
        payload = {
            "id": str(row.get("id") or ""),
            "name": str(row.get("name") or ""),
            "ext": str(row.get("ext") or "").lower(),
            "media_type": str(row.get("media_type") or ""),
            "original_url": str(row.get("original_url") or ""),
            "description": str(row.get("description") or ""),
            "captured_at": str(row.get("captured_at") or ""),
            "modified_at": str(row.get("modified_at") or ""),
            "tags": sorted(str(tag) for tag in row.get("tags") or []),
            "folders": sorted(str(folder) for folder in row.get("folders") or []),
        }
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @classmethod
    def _eagle_page_digest(cls, items: Sequence[dict[str, Any]]) -> str:
        payload = [
            (str(row.get("id") or ""), cls._eagle_item_fingerprint(row))
            for row in items
        ]
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()

    @staticmethod
    def _write_eagle_cursor(
        conn,
        *,
        cursor: dict[str, Any],
        source_signature: str,
        item_count: int,
        synced_at: str,
    ) -> None:
        conn.execute(
            text(
                """
                INSERT INTO catalog_sync_state(
                    source_kind,source_signature,status,item_count,error_message,
                    synced_at,cursor_version,cursor_json
                ) VALUES(
                    'eagle',:signature,'syncing',:count,NULL,:synced_at,
                    :cursor_version,:cursor_json
                )
                ON CONFLICT(source_kind) DO UPDATE SET
                    source_signature=excluded.source_signature,
                    status='syncing',error_message=NULL,synced_at=excluded.synced_at,
                    cursor_version=excluded.cursor_version,cursor_json=excluded.cursor_json
                """
            ),
            {
                "signature": source_signature,
                "count": item_count,
                "synced_at": synced_at,
                "cursor_version": EAGLE_SYNC_CURSOR_VERSION,
                "cursor_json": json.dumps(cursor, sort_keys=True, separators=(",", ":")),
            },
        )

    def _new_eagle_cursor(
        self,
        *,
        mode: str,
        source_snapshot: str,
    ) -> dict[str, Any]:
        return {
            "mode": mode,
            "scan_id": f"{utc_now_text()}:{new_id()}",
            "source_snapshot": source_snapshot,
            "next_offset": 0,
            "total": None,
            "changed": 0,
            "skipped": 0,
            "last_page_offset": None,
            "last_page_digest": None,
        }

    @staticmethod
    def _validated_eagle_cursor(candidate: Any) -> dict[str, Any]:
        if not isinstance(candidate, dict):
            raise ValueError("Eagle sync cursor must be an object")
        if candidate.get("mode") not in {"incremental", "full"}:
            raise ValueError("Eagle sync cursor mode is invalid")
        if not str(candidate.get("scan_id") or ""):
            raise ValueError("Eagle sync cursor scan id is missing")
        next_offset = int(candidate.get("next_offset") or 0)
        changed = int(candidate.get("changed") or 0)
        skipped = int(candidate.get("skipped") or 0)
        if min(next_offset, changed, skipped) < 0:
            raise ValueError("Eagle sync cursor contains a negative counter")
        total_value = candidate.get("total")
        total = None if total_value is None else int(total_value)
        if total is not None and (total < 0 or next_offset > total):
            raise ValueError("Eagle sync cursor total is invalid")
        if next_offset and (
            candidate.get("last_page_offset") is None
            or not str(candidate.get("last_page_digest") or "")
        ):
            raise ValueError("Eagle sync cursor anchor is missing")
        last_page_offset = candidate.get("last_page_offset")
        if last_page_offset is not None and not (
            0 <= int(last_page_offset) < max(1, next_offset)
        ):
            raise ValueError("Eagle sync cursor anchor offset is invalid")
        return {
            **candidate,
            "next_offset": next_offset,
            "total": total,
            "changed": changed,
            "skipped": skipped,
            "last_page_offset": (
                int(last_page_offset) if last_page_offset is not None else None
            ),
        }

    def _sync_eagle(
        self,
        *,
        full_rescan: bool = False,
        attempt: int = 1,
        max_attempts: int = 1,
    ) -> dict[str, Any]:
        """Checkpoint Eagle pages and only project records whose fingerprint changed."""
        source = get_eagle_catalog_source(force=True)
        source_signature, source_snapshot = self._eagle_source_tokens(source)
        with self.database.engine.connect() as conn:
            durable = conn.execute(
                text(
                    "SELECT source_signature,status,item_count,cursor_version,cursor_json "
                    "FROM catalog_sync_state WHERE source_kind='eagle'"
                )
            ).mappings().first()
        previous_count = int(durable["item_count"] or 0) if durable else 0
        cursor = None
        invalid_cursor = False
        if durable and durable["cursor_json"] and not full_rescan:
            try:
                candidate = self._validated_eagle_cursor(
                    json.loads(str(durable["cursor_json"]))
                )
                if (
                    int(durable["cursor_version"] or 0) != EAGLE_SYNC_CURSOR_VERSION
                    or durable["source_signature"] != source_signature
                ):
                    raise ValueError("incompatible Eagle sync cursor")
                cursor = candidate
            except (TypeError, ValueError, json.JSONDecodeError):
                invalid_cursor = True

        if cursor is None:
            can_increment = bool(
                durable
                and durable["status"] == "complete"
                and durable["source_signature"] == source_signature
                and not full_rescan
                and not invalid_cursor
            )
            cursor = self._new_eagle_cursor(
                mode="incremental" if can_increment else "full",
                source_snapshot=source_snapshot,
            )
            with self.database.write_transaction() as conn:
                self._write_eagle_cursor(
                    conn,
                    cursor=cursor,
                    source_signature=source_signature,
                    item_count=previous_count,
                    synced_at=utc_now_text(),
                )

        resumed = int(cursor.get("next_offset") or 0) > 0
        if resumed:
            anchor_offset = cursor.get("last_page_offset")
            anchor_digest = cursor.get("last_page_digest")
            snapshot_changed = bool(
                cursor.get("source_snapshot")
                and source_snapshot
                and cursor["source_snapshot"] != source_snapshot
            )
            anchor_valid = anchor_offset is not None and bool(anchor_digest)
            if anchor_valid and not snapshot_changed:
                anchor = get_eagle_catalog_page(
                    offset=int(anchor_offset), limit=EAGLE_SYNC_PAGE_SIZE
                )
                anchor_valid = (
                    int(anchor.total) == int(cursor.get("total") or 0)
                    and self._eagle_page_digest(anchor.items) == anchor_digest
                )
            if snapshot_changed or not anchor_valid:
                cursor = self._new_eagle_cursor(
                    mode=str(cursor.get("mode") or "full"),
                    source_snapshot=source_snapshot,
                )
                resumed = False
                with self.database.write_transaction() as conn:
                    self._write_eagle_cursor(
                        conn,
                        cursor=cursor,
                        source_signature=source_signature,
                        item_count=previous_count,
                        synced_at=utc_now_text(),
                    )

        self._publish_status(
            "eagle",
            "syncing",
            attempt=attempt,
            max_attempts=max_attempts,
            processed=int(cursor.get("next_offset") or 0),
            total=cursor.get("total"),
            changed=int(cursor.get("changed") or 0),
            skipped=int(cursor.get("skipped") or 0),
            resumed=resumed,
            full_rescan=cursor.get("mode") == "full",
            error=None,
        )

        while cursor.get("total") is None or int(cursor["next_offset"]) < int(cursor["total"]):
            offset = int(cursor.get("next_offset") or 0)
            page = get_eagle_catalog_page(offset=offset, limit=EAGLE_SYNC_PAGE_SIZE)
            if int(page.offset) != offset:
                raise RuntimeError(
                    f"Eagle returned offset {page.offset} while {offset} was requested"
                )
            if cursor.get("total") is None:
                cursor["total"] = int(page.total)
            elif int(page.total) != int(cursor["total"]):
                raise RuntimeError(
                    "Eagle library changed during catalog sync; the next run will restart safely"
                )
            if not page.items and offset < int(cursor["total"]):
                raise RuntimeError("Eagle returned an empty page before the catalog scan completed")

            item_ids = [str(row.get("id") or "") for row in page.items if row.get("id")]
            stored: dict[str, dict[str, Any]] = {}
            with self.database.write_transaction() as conn:
                if item_ids:
                    placeholders = ",".join(f":item_{index}" for index in range(len(item_ids)))
                    stored = {
                        str(row["source_key"]): dict(row)
                        for row in conn.execute(
                            text(
                                f"SELECT source_key,catalog_item_id,metadata_json FROM catalog_origins "
                                f"WHERE source_kind='eagle' AND source_key IN ({placeholders})"
                            ),
                            {f"item_{index}": item_id for index, item_id in enumerate(item_ids)},
                        ).mappings()
                    }

                unchanged = []
                for row in page.items:
                    item_id = str(row.get("id") or "")
                    item_type = str(row.get("media_type") or "")
                    if not item_id or item_type not in {"image", "video"}:
                        continue
                    fingerprint = self._eagle_item_fingerprint(row)
                    existing = stored.get(item_id)
                    existing_metadata = _json(existing["metadata_json"], {}) if existing else {}
                    is_unchanged = bool(
                        cursor.get("mode") != "full"
                        and existing
                        and existing_metadata.get("source_fingerprint") == fingerprint
                        and existing_metadata.get("fingerprint_version")
                        == EAGLE_SYNC_FINGERPRINT_VERSION
                    )
                    if is_unchanged:
                        unchanged.append(
                            {
                                "source_key": item_id,
                                "item_id": existing["catalog_item_id"],
                                "seen": cursor["scan_id"],
                            }
                        )
                        cursor["skipped"] = int(cursor.get("skipped") or 0) + 1
                        continue

                    if existing:
                        conn.execute(
                            text(
                                "DELETE FROM catalog_item_tags "
                                "WHERE catalog_item_id=:item AND source IN ('eagle','source')"
                            ),
                            {"item": existing["catalog_item_id"]},
                        )
                    detail = f"/EAGLE_{item_type}/{quote(item_id)}/"
                    metadata = {
                        "ext": row.get("ext"),
                        "folders": row.get("folders") or [],
                        "source_fingerprint": fingerprint,
                        "fingerprint_version": EAGLE_SYNC_FINGERPRINT_VERSION,
                        "modified_at": row.get("modified_at") or None,
                    }
                    self._upsert(
                        conn,
                        identity_key=f"eagle:{item_id}",
                        source_kind="eagle",
                        source_key=item_id,
                        item_type=item_type,
                        title=str(row.get("name") or "Untitled"),
                        description=str(row.get("description") or ""),
                        thumbnail_ref=str(row.get("thumbnail_route") or ""),
                        detail_uri=detail,
                        original_url=str(row.get("original_url") or ""),
                        tags=[(str(tag), "eagle") for tag in row.get("tags") or []],
                        captured_at=str(row.get("captured_at") or ""),
                        source_updated_at=str(row.get("modified_at") or ""),
                        metadata=metadata,
                        content_fingerprint=fingerprint,
                        prefer=True,
                        seen_at=str(cursor["scan_id"]),
                    )
                    cursor["changed"] = int(cursor.get("changed") or 0) + 1

                if unchanged:
                    conn.execute(
                        text(
                            "UPDATE catalog_origins SET last_seen_at=:seen,stale=0 "
                            "WHERE source_kind='eagle' AND source_key=:source_key"
                        ),
                        unchanged,
                    )
                    conn.execute(
                        text(
                            "UPDATE catalog_items SET is_deleted=0,availability='available' "
                            "WHERE id=:item_id"
                        ),
                        unchanged,
                    )

                next_offset = offset + len(page.items)
                if next_offset <= offset and next_offset < int(cursor["total"]):
                    raise RuntimeError("Eagle catalog pagination did not advance")
                cursor.update(
                    {
                        "next_offset": next_offset,
                        "last_page_offset": offset,
                        "last_page_digest": self._eagle_page_digest(page.items),
                    }
                )
                batch_time = utc_now_text()
                self._write_eagle_cursor(
                    conn,
                    cursor=cursor,
                    source_signature=source_signature,
                    item_count=previous_count,
                    synced_at=batch_time,
                )

            self._publish_status(
                "eagle",
                "syncing",
                attempt=attempt,
                max_attempts=max_attempts,
                processed=min(int(cursor["next_offset"]), int(cursor["total"])),
                total=int(cursor["total"]),
                changed=int(cursor["changed"]),
                skipped=int(cursor["skipped"]),
                resumed=resumed,
                full_rescan=cursor.get("mode") == "full",
                error=None,
                synced_at=batch_time,
            )

        final_source = get_eagle_catalog_source(force=True)
        final_signature, final_snapshot = self._eagle_source_tokens(final_source)
        if final_signature != source_signature or (
            source_snapshot and final_snapshot and final_snapshot != source_snapshot
        ):
            raise RuntimeError(
                "Eagle library changed during catalog sync; the next run will restart safely"
            )

        completed_at = utc_now_text()
        with self.database.write_transaction() as conn:
            seen_count = int(
                conn.execute(
                    text(
                        "SELECT COUNT(*) FROM catalog_origins "
                        "WHERE source_kind='eagle' AND last_seen_at=:scan_id"
                    ),
                    {"scan_id": cursor["scan_id"]},
                ).scalar()
                or 0
            )
            if seen_count != int(cursor.get("total") or 0):
                raise RuntimeError(
                    "Eagle returned an unstable item order; the next run will restart safely"
                )
            deleted = conn.execute(
                text(
                    "UPDATE catalog_origins SET stale=1 "
                    "WHERE source_kind='eagle' AND stale=0 AND last_seen_at<>:scan_id"
                ),
                {"scan_id": cursor["scan_id"]},
            ).rowcount
            conn.execute(
                text(
                    """
                    UPDATE catalog_items SET is_deleted=1,availability='missing'
                    WHERE id IN (
                        SELECT catalog_item_id FROM catalog_origins
                        WHERE source_kind='eagle' AND stale=1
                    )
                    AND NOT EXISTS (
                        SELECT 1 FROM catalog_origins live
                        WHERE live.catalog_item_id=catalog_items.id AND live.stale=0
                    )
                    """
                )
            )
            count = int(
                conn.execute(
                    text(
                        "SELECT COUNT(*) FROM catalog_origins "
                        "WHERE source_kind='eagle' AND stale=0"
                    )
                ).scalar()
                or 0
            )
            conn.execute(
                text(
                    """
                    INSERT INTO catalog_sync_state(
                        source_kind,source_signature,status,item_count,error_message,
                        synced_at,cursor_version,cursor_json
                    ) VALUES('eagle',:signature,'complete',:count,NULL,:synced_at,:version,NULL)
                    ON CONFLICT(source_kind) DO UPDATE SET
                        source_signature=excluded.source_signature,status='complete',
                        item_count=excluded.item_count,error_message=NULL,
                        synced_at=excluded.synced_at,cursor_version=excluded.cursor_version,
                        cursor_json=NULL
                    """
                ),
                {
                    "signature": source_signature,
                    "count": count,
                    "synced_at": completed_at,
                    "version": EAGLE_SYNC_CURSOR_VERSION,
                },
            )
        return {
            "count": count,
            "processed": int(cursor.get("total") or 0),
            "total": int(cursor.get("total") or 0),
            "changed": int(cursor.get("changed") or 0),
            "skipped": int(cursor.get("skipped") or 0),
            "deleted": max(0, int(deleted or 0)),
            "resumed": resumed,
            "full_rescan": cursor.get("mode") == "full",
            "synced_at": completed_at,
        }
