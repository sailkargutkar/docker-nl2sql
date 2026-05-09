"""
Dialect-aware plumbing tests — verify Postgres and MySQL paths produce
the right URLs, session settings, validator output, and type mappings,
all without requiring a live database.
"""

from __future__ import annotations

import pytest

from app.db_registry import (
    SUPPORTED_DIALECTS,
    DatabaseEntry,
)
from app.executor import detect_dialect
from app.main import _build_url
from app.schema_dsl import Column, Schema, Table
from app.schema_introspect import _map_mysql_type
from app.validator import ValidationError, validate_and_rewrite


# ---------- detect_dialect ----------


def test_detect_dialect_postgres():
    assert detect_dialect("postgresql+psycopg2://u:p@h:5432/d") == "postgres"
    assert detect_dialect("postgresql://u:p@h:5432/d") == "postgres"


def test_detect_dialect_mysql():
    assert detect_dialect("mysql+pymysql://u:p@h:3306/d") == "mysql"
    assert detect_dialect("mysql://u:p@h:3306/d") == "mysql"


def test_detect_dialect_unknown_falls_back_to_postgres():
    assert detect_dialect("sqlite:///./x.db") == "postgres"


# ---------- _build_url ----------


def test_build_url_default_is_postgres():
    url = _build_url("h", 5432, "d", "u", "p")
    assert url.startswith("postgresql+psycopg2://")
    assert "u:p@h:5432/d" in url


def test_build_url_mysql():
    url = _build_url("h", 3306, "d", "u", "p", dialect="mysql")
    assert url.startswith("mysql+pymysql://")
    assert "u:p@h:3306/d" in url


# ---------- DatabaseEntry.url() ----------


def _make_entry(dialect: str, port: int) -> DatabaseEntry:
    return DatabaseEntry(
        id=1, name="x", host="h", port=port, dbname="d",
        username="u", password="p", schema_file="",
        table_count=0, is_active=False,
        created_at="", updated_at="", dialect=dialect,
    )


def test_db_entry_url_postgres():
    assert _make_entry("postgres", 5432).url().startswith("postgresql+psycopg2://")


def test_db_entry_url_mysql():
    assert _make_entry("mysql", 3306).url().startswith("mysql+pymysql://")


def test_supported_dialects_contains_both():
    assert "postgres" in SUPPORTED_DIALECTS
    assert "mysql" in SUPPORTED_DIALECTS


# ---------- validator dialect ----------


@pytest.fixture
def schema_users() -> Schema:
    return Schema(
        database="t", description="",
        tables=[Table(name="Users", columns=[
            Column(name="id", type="int", pk=True),
            Column(name="name", type="string"),
            Column(name="age", type="int"),
        ])],
    )


def test_validator_emits_postgres_dialect(schema_users):
    r = validate_and_rewrite(
        'SELECT id, name FROM "Users" LIMIT 5', schema_users, 100,
        dialect="postgres",
    )
    # Postgres canonical form keeps double quotes.
    assert '"Users"' in r.sql or "Users" in r.sql
    assert "LIMIT 5" in r.sql.upper()


def test_validator_accepts_mysql_dialect(schema_users):
    r = validate_and_rewrite(
        "SELECT id, name FROM `Users` LIMIT 5", schema_users, 100,
        dialect="mysql",
    )
    assert "LIMIT 5" in r.sql.upper()
    assert r.tables_used == ["Users"]


def test_validator_rejects_dml_per_dialect(schema_users):
    with pytest.raises(ValidationError):
        validate_and_rewrite(
            "INSERT INTO `Users` (name) VALUES ('x')",
            schema_users, 100, dialect="mysql",
        )


# ---------- MySQL type mapping ----------


def test_tinyint_one_is_boolean():
    assert _map_mysql_type("tinyint", "tinyint(1)") == "boolean"


def test_tinyint_other_widths_are_smallint():
    assert _map_mysql_type("tinyint", "tinyint(4)") == "smallint"
    assert _map_mysql_type("tinyint", "tinyint") == "smallint"


def test_varchar_is_string():
    assert _map_mysql_type("varchar", "varchar(255)") == "string"


def test_datetime_is_timestamp():
    assert _map_mysql_type("datetime", "datetime") == "timestamp"


def test_timestamp_is_timestamptz():
    assert _map_mysql_type("timestamp", "timestamp") == "timestamptz"


def test_json_normalizes_to_jsonb():
    assert _map_mysql_type("json", "json") == "jsonb"


def test_unknown_type_passes_through():
    # Unknown types fall back to whatever MySQL reports — not "unknown" — so
    # downstream code can still see the original spelling.
    out = _map_mysql_type("geometry", "geometry")
    assert out == "geometry"
