"""
Read-only query executor.

Every connection enforces, per dialect:
  Postgres:
    - default_transaction_read_only = on
    - statement_timeout
    - idle_in_transaction_session_timeout
  MySQL:
    - SESSION TRANSACTION READ ONLY
    - max_execution_time (in ms)

This is the last line of defense behind the validator. Even if a malformed
UPDATE somehow got past sqlglot, the database would refuse to execute it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine


def detect_dialect(url: str) -> str:
    """Return 'postgres' or 'mysql' from a SQLAlchemy URL prefix."""
    if url.startswith("mysql"):
        return "mysql"
    return "postgres"


@dataclass
class ExecutionResult:
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    elapsed_ms: int
    truncated: bool


_engines: dict[tuple[str, int], Engine] = {}


def _build_engine(url: str, statement_timeout_ms: int) -> Engine:
    # Do NOT pass session settings via startup options ("-c ..."): pgbouncer
    # in transaction/statement pooling mode rejects unknown startup params.
    # Apply all session settings post-connect instead.
    engine = create_engine(
        url,
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=2,
    )

    dialect = detect_dialect(url)

    @event.listens_for(engine, "connect")
    def _set_session(dbapi_connection, _):
        with dbapi_connection.cursor() as cur:
            if dialect == "mysql":
                # MySQL 5.7+: max_execution_time in milliseconds (SELECT only).
                cur.execute("SET SESSION TRANSACTION READ ONLY")
                cur.execute(
                    f"SET SESSION max_execution_time = {int(statement_timeout_ms)}"
                )
            else:
                cur.execute("SET default_transaction_read_only = on")
                cur.execute(
                    f"SET statement_timeout = {int(statement_timeout_ms)}"
                )
                cur.execute("SET idle_in_transaction_session_timeout = 30000")

    return engine


def get_engine(url: str, statement_timeout_ms: int) -> Engine:
    """Return a cached engine for this exact (url, timeout) pair.

    Keying by URL is essential: switching the active DB must not reuse the
    previous DB's engine. A naive single-global cache caused cross-DB queries
    to silently hit the wrong database.
    """
    key = (url, statement_timeout_ms)
    eng = _engines.get(key)
    if eng is None:
        eng = _build_engine(url, statement_timeout_ms)
        _engines[key] = eng
    return eng


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def execute(sql: str, url: str, statement_timeout_ms: int, max_rows: int) -> ExecutionResult:
    engine = get_engine(url, statement_timeout_ms)
    start = time.monotonic()
    with engine.connect() as conn:
        result = conn.execute(text(sql))
        columns = list(result.keys())
        rows: list[list[Any]] = []
        truncated = False
        for i, row in enumerate(result):
            if i >= max_rows:
                truncated = True
                break
            rows.append([_jsonable(v) for v in row])
    elapsed = int((time.monotonic() - start) * 1000)
    return ExecutionResult(
        columns=columns,
        rows=rows,
        row_count=len(rows),
        elapsed_ms=elapsed,
        truncated=truncated,
    )
