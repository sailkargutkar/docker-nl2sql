"""
SQLite-backed registry of configured databases.

Shares the history.db file — one more table, no extra dependency. Stores
connection credentials in plaintext; this service is intended to run behind
your gateway on an internal network. Harden the file permissions on
`/data/history.db` (restrict to the service user) if that matters for your
deployment.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS databases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    host TEXT NOT NULL,
    port INTEGER NOT NULL,
    dbname TEXT NOT NULL,
    username TEXT NOT NULL,
    password TEXT NOT NULL,
    schema_file TEXT NOT NULL,
    table_count INTEGER DEFAULT 0,
    is_active INTEGER DEFAULT 0,
    dialect TEXT NOT NULL DEFAULT 'postgres',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


SUPPORTED_DIALECTS = ("postgres", "mysql")


@dataclass
class DatabaseEntry:
    id: int
    name: str
    host: str
    port: int
    dbname: str
    username: str
    password: str
    schema_file: str
    table_count: int
    is_active: bool
    created_at: str
    updated_at: str
    dialect: str = "postgres"

    def url(self) -> str:
        if self.dialect == "mysql":
            return (
                f"mysql+pymysql://{self.username}:{self.password}"
                f"@{self.host}:{self.port}/{self.dbname}"
            )
        return (
            f"postgresql+psycopg2://{self.username}:{self.password}"
            f"@{self.host}:{self.port}/{self.dbname}"
        )

    def to_public_dict(self) -> dict:
        d = self.__dict__.copy()
        d.pop("password", None)  # never leak passwords to the UI
        return d


def init(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with _conn(path) as c:
        c.executescript(SCHEMA)
        # Idempotent migration for installs that pre-date the dialect column.
        existing_cols = {
            r[1] for r in c.execute("PRAGMA table_info(databases)").fetchall()
        }
        if "dialect" not in existing_cols:
            c.execute(
                "ALTER TABLE databases ADD COLUMN dialect TEXT "
                "NOT NULL DEFAULT 'postgres'"
            )


@contextmanager
def _conn(path: str) -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(path, isolation_level=None)
    c.row_factory = sqlite3.Row
    try:
        yield c
    finally:
        c.close()


def _row_to_entry(row: sqlite3.Row) -> DatabaseEntry:
    d = dict(row)
    d["is_active"] = bool(d["is_active"])
    return DatabaseEntry(**d)


def sanitize_name(name: str) -> str:
    """Safe filename-component for per-DB DSL file paths."""
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", name.strip())
    return s or "db"


def list_all(path: str) -> list[DatabaseEntry]:
    with _conn(path) as c:
        rows = c.execute(
            "SELECT * FROM databases ORDER BY is_active DESC, name ASC"
        ).fetchall()
    return [_row_to_entry(r) for r in rows]


def get_by_name(path: str, name: str) -> DatabaseEntry | None:
    with _conn(path) as c:
        row = c.execute("SELECT * FROM databases WHERE name = ?", (name,)).fetchone()
    return _row_to_entry(row) if row else None


def get_active(path: str) -> DatabaseEntry | None:
    with _conn(path) as c:
        row = c.execute(
            "SELECT * FROM databases WHERE is_active = 1 LIMIT 1"
        ).fetchone()
    return _row_to_entry(row) if row else None


def insert(
    path: str,
    *,
    name: str,
    host: str,
    port: int,
    dbname: str,
    username: str,
    password: str,
    schema_file: str,
    table_count: int,
    dialect: str = "postgres",
) -> DatabaseEntry:
    if dialect not in SUPPORTED_DIALECTS:
        raise ValueError(
            f"Unsupported dialect '{dialect}'. "
            f"Must be one of: {', '.join(SUPPORTED_DIALECTS)}"
        )
    with _conn(path) as c:
        c.execute(
            """
            INSERT INTO databases
                (name, host, port, dbname, username, password,
                 schema_file, table_count, dialect)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (name, host, port, dbname, username, password,
             schema_file, table_count, dialect),
        )
    entry = get_by_name(path, name)
    assert entry is not None
    # If this is the only entry, make it active automatically.
    if len(list_all(path)) == 1:
        set_active(path, name)
        entry = get_by_name(path, name)
        assert entry is not None
    return entry


def update_table_count(path: str, name: str, table_count: int) -> None:
    with _conn(path) as c:
        c.execute(
            "UPDATE databases SET table_count = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE name = ?",
            (table_count, name),
        )


def set_active(path: str, name: str) -> None:
    with _conn(path) as c:
        c.execute("UPDATE databases SET is_active = 0")
        c.execute(
            "UPDATE databases SET is_active = 1, updated_at = CURRENT_TIMESTAMP "
            "WHERE name = ?",
            (name,),
        )


def delete(path: str, name: str) -> bool:
    with _conn(path) as c:
        cur = c.execute("DELETE FROM databases WHERE name = ?", (name,))
        return cur.rowcount > 0
