from __future__ import annotations

from app.nlp.matcher import resolve_join_path, score_columns, score_tables
from app.nlp.preprocess import preprocess


def test_score_tables_picks_employee(schema):
    pre = preprocess("how many employees?")
    matches = score_tables(pre, schema)
    assert matches
    assert matches[0].table == "Employee"


def test_score_columns_picks_salary(schema):
    pre = preprocess("total salary of employees")
    cols = score_columns(pre, schema)
    names = [(c.table, c.column) for c in cols]
    assert ("Employee", "salary") in names


def test_synonym_match(schema):
    # 'enabled' is declared as a synonym of Employee.active via synonyms: ['enabled']
    pre = preprocess("list enabled employees")
    cols = score_columns(pre, schema, tables=["Employee"])
    names = {(c.table, c.column) for c in cols}
    assert ("Employee", "active") in names or ("Employee", "enabled") in names


def test_join_path_direct(schema):
    path = resolve_join_path(schema, "Employee", "Client")
    assert path is not None
    # Single FK edge expected.
    assert len(path) == 1
    assert path[0][0] == "Employee" and path[0][2] == "Client"


def test_join_path_through(schema):
    # Employee → Client → Organization
    path = resolve_join_path(schema, "Employee", "Organization")
    assert path is not None
    assert len(path) == 2


def test_join_path_self(schema):
    assert resolve_join_path(schema, "Client", "Client") == []
