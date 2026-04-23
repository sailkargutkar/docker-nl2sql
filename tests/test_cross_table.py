"""End-to-end: the original failing prompt should now produce cross-table SQL."""

from __future__ import annotations

import pytest

from app.generator import generate_sql
from app.validator import validate_and_rewrite


@pytest.fixture(autouse=True)
def _isolate_classifier():
    from app import generator

    generator._singleton = None
    yield
    generator._singleton = None


def _ask(question: str, schema) -> str:
    r = generate_sql(question, schema, 500, "/tmp/nonexistent-model.joblib")
    return r.sql


def test_total_users_belongs_to_org_swaraj(schema):
    """The regression that triggered this change."""
    sql = _ask("total users belongs to org swaraj", schema)
    u = sql.upper()
    assert "COUNT(*)" in u, f"expected count aggregate, got:\n{sql}"
    assert '"User"' in sql and '"Organization"' in sql
    # WHERE should filter Organization.name = 'swaraj' (case-insensitive check).
    assert "swaraj" in sql.lower()
    assert "ORGANIZATION" in u and "NAME" in u
    validate_and_rewrite(sql, schema, 500)


def test_employees_for_client_acme(schema):
    sql = _ask("list employees for client acme", schema)
    u = sql.upper()
    assert '"Employee"' in sql
    assert '"Client"' in sql
    assert "acme" in sql.lower()
    validate_and_rewrite(sql, schema, 500)


def test_named_pattern_with_join(schema):
    sql = _ask("list employees in client named acme", schema)
    assert "acme" in sql.lower()
    assert '"Client"' in sql
    validate_and_rewrite(sql, schema, 500)


def test_total_plural_without_value_still_counts(schema):
    sql = _ask("total clients", schema)
    assert "COUNT(*)" in sql.upper()
    validate_and_rewrite(sql, schema, 500)
