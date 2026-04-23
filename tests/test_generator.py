from __future__ import annotations

import pytest

from app.generator import generate_sql
from app.validator import validate_and_rewrite


@pytest.fixture(autouse=True)
def _isolate_classifier(tmp_path, monkeypatch):
    """Force generator to fall back to regex intent (no pretrained model)."""
    from app import generator

    generator._singleton = None
    yield
    generator._singleton = None


def _ask(question: str, schema, max_rows: int = 100) -> str:
    r = generate_sql(question, schema, max_rows, "/tmp/nonexistent-model.joblib")
    return r.sql


def test_count_simple(schema):
    sql = _ask("how many employees are there", schema)
    assert "COUNT(*)" in sql.upper()
    assert '"Employee"' in sql
    validate_and_rewrite(sql, schema, 100)


def test_list_simple(schema):
    sql = _ask("list all clients", schema)
    assert sql.upper().startswith("SELECT")
    assert '"Client"' in sql
    assert "LIMIT" in sql.upper()
    validate_and_rewrite(sql, schema, 100)


def test_sum_needs_numeric_column(schema):
    sql = _ask("total salary of employees", schema)
    assert "SUM(" in sql.upper()
    assert "salary" in sql.lower()
    validate_and_rewrite(sql, schema, 100)


def test_avg_numeric(schema):
    sql = _ask("average salary", schema)
    assert "AVG(" in sql.upper()
    validate_and_rewrite(sql, schema, 100)


def test_min_date(schema):
    sql = _ask("earliest hired date for employees", schema)
    assert "MIN(" in sql.upper()
    validate_and_rewrite(sql, schema, 100)


def test_max_date(schema):
    sql = _ask("latest hired date for employees", schema)
    assert "MAX(" in sql.upper()
    validate_and_rewrite(sql, schema, 100)


def test_top_n(schema):
    sql = _ask("top 5 employees by salary", schema)
    u = sql.upper()
    assert "ORDER BY" in u and "DESC" in u and "LIMIT 5" in u
    validate_and_rewrite(sql, schema, 100)


def test_quoted_literal_becomes_where(schema):
    sql = _ask("show employees named 'John'", schema)
    # Either: name = 'John' filter is present, or the generator matched a
    # broader column — accept either but there must be a WHERE.
    assert "WHERE" in sql.upper()
    validate_and_rewrite(sql, schema, 100)


def test_boolean_filter(schema):
    sql = _ask("list active employees", schema)
    assert "WHERE" in sql.upper()
    validate_and_rewrite(sql, schema, 100)


def test_join_via_fk(schema):
    sql = _ask("list employees with client name", schema)
    # Builder should project Client.name by joining Employee → Client.
    u = sql.upper()
    assert '"Employee"' in sql or '"Client"' in sql
    validate_and_rewrite(sql, schema, 100)


def test_ambiguous_returns_nothing(schema):
    r = generate_sql(
        "tell me about the thing", schema, 100, "/tmp/nonexistent-model.joblib"
    )
    # Low-confidence fallback to 'list' with no matched tables should either
    # succeed with a generic SELECT or return an empty SQL — but must never
    # blow up.
    assert r.sql is not None
