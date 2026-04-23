"""Lightweight SQLite-backed query history."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS queries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    question TEXT NOT NULL,
    sql TEXT,
    status TEXT NOT NULL,
    error TEXT,
    row_count INTEGER,
    elapsed_ms INTEGER,
    tables_used TEXT,
    model TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER
);
CREATE INDEX IF NOT EXISTS idx_queries_created_at ON queries(created_at DESC);
"""


def init(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with _conn(path) as c:
        c.executescript(SCHEMA)


@contextmanager
def _conn(path: str) -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(path, isolation_level=None)
    c.row_factory = sqlite3.Row
    try:
        yield c
    finally:
        c.close()


def record(path: str, **fields: Any) -> int:
    tables = fields.get("tables_used")
    if isinstance(tables, list):
        fields["tables_used"] = json.dumps(tables)
    cols = ",".join(fields.keys())
    ph = ",".join("?" for _ in fields)
    with _conn(path) as c:
        cur = c.execute(f"INSERT INTO queries ({cols}) VALUES ({ph})", tuple(fields.values()))
        return int(cur.lastrowid or 0)


def list_recent(path: str, limit: int = 50) -> list[dict[str, Any]]:
    with _conn(path) as c:
        rows = c.execute(
            "SELECT id, created_at, question, sql, status, error, row_count, elapsed_ms, "
            "tables_used, model, prompt_tokens, completion_tokens "
            "FROM queries ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            if d.get("tables_used"):
                try:
                    d["tables_used"] = json.loads(d["tables_used"])
                except json.JSONDecodeError:
                    pass
            out.append(d)
        return out
