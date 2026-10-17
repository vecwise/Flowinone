"""Read a consistent SQLite + WAL image without opening source SQLite files.

Only file reads touch the source. SQLite operates on a deserialized :memory:
image, so preview cannot create SHM files, checkpoint WAL, or run migrations.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
import sqlite3
import struct
from pathlib import Path


def _signature(path: Path):
    try:
        stat = path.stat()
        return stat.st_ino, stat.st_size, stat.st_mtime_ns
    except FileNotFoundError:
        return None


def _checksum(data: bytes, endian: str, seed=(0, 0)):
    first, second = seed
    for x, y in struct.iter_unpack(endian + "II", data):
        first = (first + x + second) & 0xFFFFFFFF
        second = (second + y + first) & 0xFFFFFFFF
    return first, second


def read_snapshot(database: Path) -> tuple[sqlite3.Connection, dict]:
    database = database.expanduser().resolve(strict=True)
    wal = Path(str(database) + "-wal")
    journal = Path(str(database) + "-journal")
    for _ in range(8):
        if journal.exists() and journal.stat().st_size:
            raise ValueError("Rollback journal present; retry after the writer finishes")
        before = [_signature(path) for path in (database, wal)]
        raw = database.read_bytes()
        log = wal.read_bytes() if before[1] else b""
        if before == [_signature(path) for path in (database, wal)]:
            break
    else:
        raise ValueError("Database changed during every snapshot attempt")
    if raw[:16] != b"SQLite format 3\0":
        raise ValueError("Invalid SQLite file")
    page_size = int.from_bytes(raw[16:18], "big")
    page_size = 65536 if page_size == 1 else page_size
    image = bytearray(raw)
    frames: list[tuple[int, bytes]] = []
    committed = size = 0
    if log:
        if len(log) < 32:
            raise ValueError("Incomplete WAL header; retry preview")
        magic, version, wal_page_size = struct.unpack(">III", log[:12])
        if magic not in (0x377F0682, 0x377F0683) or version != 3007000 or wal_page_size != page_size:
            raise ValueError("Unsupported WAL header")
        endian = "<" if magic == 0x377F0682 else ">"
        seed = _checksum(log[:24], endian)
        if seed != struct.unpack(">II", log[24:32]):
            raise ValueError("Invalid WAL header checksum")
        for offset in range(32, len(log) - 24 - page_size + 1, 24 + page_size):
            header = log[offset:offset + 24]
            page = log[offset + 24:offset + 24 + page_size]
            number, db_size = struct.unpack(">II", header[:8])
            if not number or header[8:16] != log[16:24]:
                break  # A WAL can retain frames from an earlier generation.
            checksum = _checksum(header[:8] + page, endian, seed)
            if checksum != struct.unpack(">II", header[16:24]):
                break
            seed = checksum
            frames.append((number, page))
            if db_size:
                committed, size = len(frames), db_size
        if committed:
            required = size * page_size
            if len(image) < required:
                image.extend(b"\0" * (required - len(image)))
            for number, page in frames[:committed]:
                if number <= size:
                    image[(number - 1) * page_size:number * page_size] = page
            del image[required:]
    # SQLite cannot deserialize a WAL-mode database. Only the memory copy's
    # journal format bytes change; the file images above remain untouched.
    image[18:20] = b"\1\1"
    connection = sqlite3.connect(":memory:")
    connection.deserialize(bytes(image))
    connection.execute("PRAGMA query_only=ON")
    if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        connection.close()
        raise ValueError("Snapshot integrity check failed")
    return connection, {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "database_path": str(database),
        "database_sha256": hashlib.sha256(raw).hexdigest(),
        "wal_sha256": hashlib.sha256(log).hexdigest(),
        "committed_wal_frames": committed,
        "integrity_check": "ok",
    }
