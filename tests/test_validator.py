import pytest

from app.schema_dsl import Column, Schema, Table
from app.validator import ValidationError, validate_and_rewrite


@pytest.fixture
def schema() -> Schema:
    return Schema(
        database="tmt",
        description="test",
        tables=[
            Table(name="Employee", columns=[Column(name="id"), Column(name="name"), Column(name="clientId")]),
            Table(name="Client", columns=[Column(name="id"), Column(name="name")]),
        ],
    )


def test_simple_select_ok(schema):
    r = validate_and_rewrite('SELECT id, name FROM "Employee"', schema, max_rows=100)
    assert "LIMIT 100" in r.sql.upper()
    assert r.tables_used == ["Employee"]


def test_join_ok(schema):
    sql = 'SELECT e.name FROM "Employee" e JOIN "Client" c ON e."clientId" = c.id'
    r = validate_and_rewrite(sql, schema, max_rows=50)
    assert r.limit_applied == 50
    assert set(r.tables_used) == {"Employee", "Client"}


def test_existing_small_limit_preserved(schema):
    r = validate_and_rewrite('SELECT id FROM "Employee" LIMIT 5', schema, max_rows=100)
    assert r.limit_applied == 5


def test_existing_huge_limit_capped(schema):
    r = validate_and_rewrite('SELECT id FROM "Employee" LIMIT 999999', schema, max_rows=100)
    assert r.limit_applied == 100


def test_reject_insert(schema):
    with pytest.raises(ValidationError):
        validate_and_rewrite('INSERT INTO "Employee" (name) VALUES (\'x\')', schema, max_rows=10)


def test_reject_update(schema):
    with pytest.raises(ValidationError):
        validate_and_rewrite('UPDATE "Employee" SET name = \'x\'', schema, max_rows=10)


def test_reject_delete(schema):
    with pytest.raises(ValidationError):
        validate_and_rewrite('DELETE FROM "Employee"', schema, max_rows=10)


def test_reject_drop(schema):
    with pytest.raises(ValidationError):
        validate_and_rewrite('DROP TABLE "Employee"', schema, max_rows=10)


def test_reject_multi_statement(schema):
    with pytest.raises(ValidationError):
        validate_and_rewrite(
            'SELECT 1 FROM "Employee"; SELECT 2 FROM "Employee"',
            schema,
            max_rows=10,
        )


def test_reject_unknown_table(schema):
    with pytest.raises(ValidationError) as exc:
        validate_and_rewrite("SELECT * FROM secrets", schema, max_rows=10)
    assert "unknown table" in str(exc.value).lower()


def test_reject_dangerous_function(schema):
    with pytest.raises(ValidationError):
        validate_and_rewrite(
            "SELECT pg_read_file('/etc/passwd')", schema, max_rows=10
        )


def test_reject_empty(schema):
    with pytest.raises(ValidationError):
        validate_and_rewrite("  ;  ", schema, max_rows=10)


def test_reject_garbage(schema):
    with pytest.raises(ValidationError):
        validate_and_rewrite("not sql at all ###", schema, max_rows=10)


def test_count_star_no_limit_injected(schema):
    r = validate_and_rewrite('SELECT COUNT(*) FROM "Client"', schema, max_rows=500)
    assert "LIMIT" not in r.sql.upper()


def test_count_star_preserves_explicit_llm_limit(schema):
    # If the LLM (reflecting the user's request) emits an explicit LIMIT, we
    # keep it — even on an aggregate where it's semantically a no-op.
    r = validate_and_rewrite('SELECT COUNT(*) FROM "Client" LIMIT 9', schema, max_rows=500)
    assert "LIMIT 9" in r.sql.upper()


def test_group_by_still_limited(schema):
    r = validate_and_rewrite(
        'SELECT "clientId", COUNT(*) FROM "Employee" GROUP BY "clientId"',
        schema,
        max_rows=100,
    )
    assert "LIMIT 100" in r.sql.upper()
