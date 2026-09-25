"""Local state in SQLite: processed files, vendor registry and the outbox of rows to sync.

Processing a file and queueing its rows happen in one transaction, so a crash leaves either
nothing or a complete record. The outbox holds the desired state of every sheet row; a row is
synced when the hash last written to the sink equals its current hash.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    sha256        TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    status        TEXT NOT NULL,
    invoice_key   TEXT,
    method        TEXT,
    confidence    REAL,
    issues        TEXT NOT NULL,
    notes         TEXT NOT NULL,
    invoice       TEXT NOT NULL,
    processed_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS files_key ON files(invoice_key);
CREATE TABLE IF NOT EXISTS vendors (
    tax_id  TEXT PRIMARY KEY,
    name    TEXT NOT NULL,
    source  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox (
    sheet        TEXT NOT NULL,
    row_key      TEXT NOT NULL,
    row          TEXT NOT NULL,
    row_hash     TEXT NOT NULL,
    synced_hash  TEXT,
    PRIMARY KEY (sheet, row_key)
);
"""


def row_hash(row: list) -> str:
    return hashlib.sha256(json.dumps(row, sort_keys=True, default=str).encode()).hexdigest()[:16]


@dataclass
class FileRecord:
    sha256: str
    name: str
    status: str
    invoice_key: str | None
    invoice: dict


class State:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), isolation_level=None)  # explicit transactions only
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # --- files -------------------------------------------------------------------------------------
    def seen(self, sha: str) -> bool:
        return self.db.execute("SELECT 1 FROM files WHERE sha256=?", (sha,)).fetchone() is not None

    def by_key(self, key: str) -> FileRecord | None:
        r = self.db.execute(
            "SELECT sha256, name, status, invoice_key, invoice FROM files WHERE invoice_key=? AND status!='duplicate' "
            "ORDER BY rowid LIMIT 1", (key,)).fetchone()
        return FileRecord(r[0], r[1], r[2], r[3], json.loads(r[4])) if r else None

    def record(self, *, sha: str, name: str, status: str, key: str | None, method: str, confidence: float,
               issues: list[dict], notes: list[str], invoice: dict, processed_at: str,
               rows: dict[str, list[tuple[str, list]]]) -> None:
        """Store the file and queue its sheet rows atomically. rows: sheet -> [(row_key, row)]."""
        cur = self.db.cursor()
        cur.execute("BEGIN IMMEDIATE")
        try:
            cur.execute(
                "INSERT INTO files VALUES (?,?,?,?,?,?,?,?,?,?)",
                (sha, name, status, key, method, confidence, json.dumps(issues), json.dumps(notes),
                 json.dumps(invoice, default=str), processed_at))
            for sheet, sheet_rows in rows.items():
                for row_key, row in sheet_rows:
                    h = row_hash(row)
                    cur.execute(
                        "INSERT INTO outbox(sheet, row_key, row, row_hash) VALUES (?,?,?,?) "
                        "ON CONFLICT(sheet, row_key) DO UPDATE SET row=excluded.row, row_hash=excluded.row_hash",
                        (sheet, row_key, json.dumps(row, default=str), h))
            cur.execute("COMMIT")
        except BaseException:
            cur.execute("ROLLBACK")
            raise

    def files(self) -> list[dict]:
        cols = ["sha256", "name", "status", "invoice_key", "method", "confidence", "issues", "notes", "invoice",
                "processed_at"]
        out = []
        for r in self.db.execute(f"SELECT {', '.join(cols)} FROM files ORDER BY name"):
            d = dict(zip(cols, r))
            for k in ("issues", "notes", "invoice"):
                d[k] = json.loads(d[k])
            out.append(d)
        return out

    # --- vendor registry ---------------------------------------------------------------------------
    def vendor(self, tax_id: str) -> str | None:
        r = self.db.execute("SELECT name FROM vendors WHERE tax_id=?", (tax_id,)).fetchone()
        return r[0] if r else None

    def learn_vendor(self, tax_id: str, name: str, source: str) -> None:
        self.db.execute("INSERT OR IGNORE INTO vendors VALUES (?,?,?)", (tax_id, name, source))

    # --- outbox --------------------------------------------------------------------------------------
    def pending(self, sheet: str) -> list[tuple[str, list, str]]:
        rows = self.db.execute(
            "SELECT row_key, row, row_hash FROM outbox WHERE sheet=? AND (synced_hash IS NULL OR synced_hash!=row_hash) "
            "ORDER BY row_key", (sheet,)).fetchall()
        return [(k, json.loads(r), h) for k, r, h in rows]

    def mark_synced(self, sheet: str, done: list[tuple[str, str]]) -> None:
        cur = self.db.cursor()
        cur.execute("BEGIN IMMEDIATE")
        cur.executemany("UPDATE outbox SET synced_hash=? WHERE sheet=? AND row_key=?",
                        [(h, sheet, k) for k, h in done])
        cur.execute("COMMIT")

    def keys(self, sheet: str) -> set[str]:
        return {r[0] for r in self.db.execute("SELECT row_key FROM outbox WHERE sheet=?", (sheet,))}

    def sheets(self) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT DISTINCT sheet FROM outbox ORDER BY sheet")]
