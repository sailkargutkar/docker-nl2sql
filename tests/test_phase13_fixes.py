"""
Regression tests for the three Phase-13 weaknesses fixed in Phase 14:

  1. Spurious `WHERE name = '<typo>'` when the user's "value" was actually
     a typo of a column name on the same table.

  2. Boolean column matching no longer adds bogus `name = '<value>'` filter
     when the literal corresponds to a boolean column (sister-fix to #1).

  3. `are there any X` was classified as `list`; now classified as `exists`.

Each test is run with the regex-fallback path (no trained classifier
loaded) to keep them fully deterministic.
"""

from __future__ import annotations

import pytest

from app.generator import generate_sql
from app.nlp.implicit import detect as detect_implicit
from app.nlp.preprocess import preprocess
from app.schema_dsl import Column, Schema, Table


@pytest.fixture(autouse=True)
def _reset_classifier():
    from app import generator
    generator._singleton = None
    yield
    generator._singleton = None


@pytest.fixture
def shop_schema() -> Schema:
    """Minimal e-commerce schema mirroring the jodhpur shape."""
    return Schema(
        database="shop",
        description="",
        tables=[
            Table(
                name="products",
                columns=[
                    Column(name="id", type="integer", pk=True),
                    Column(name="name", type="string"),
                    Column(name="supplier", type="string"),
                    Column(name="category", type="string"),
                    Column(name="pricesell", type="numeric"),
                    Column(name="isservice", type="boolean"),
                ],
            ),
            Table(
                name="onlineorders",
                columns=[
                    Column(name="id", type="bigint", pk=True),
                    Column(name="orderId", type="string"),
                    Column(name="paymentStatus", type="string"),
                ],
            ),
        ],
    )


# ---------- Phase 13 weakness #1: spurious WHERE name='<typo>' ----------


def test_typo_of_column_name_does_not_become_value(shop_schema):
    """`list products by suplier` must NOT bind `name = 'suplier'`.

    `suplier` should be recognised as a typo of the `supplier` column,
    not as a literal value. Resulting SQL should select supplier and
    have no WHERE clause filtering on `suplier`.
    """
    r = generate_sql(
        "list products by suplier",
        shop_schema, 100, "/tmp/none.joblib", dialect="mysql",
    )
    assert r.sql is not None
    assert "supplier" in r.sql.lower()
    assert "suplier" not in r.sql.lower(), (
        f"Typo'd literal leaked into SQL: {r.sql}"
    )
    assert "where" not in r.sql.lower() or "= 'suplier'" not in r.sql.lower()


def test_implicit_skips_typo_of_known_lemma(shop_schema):
    """The implicit-value detector itself must reject typo'd schema lemmas."""
    pre = preprocess("list products by suplier")
    hints = detect_implicit(pre, shop_schema)
    bound_values = {h.value for h in hints}
    assert "suplier" not in bound_values, (
        f"Implicit detector emitted a typo as a value: {hints}"
    )


def test_implicit_still_emits_real_proper_nouns(shop_schema):
    """Sanity: legitimate proper-noun values must still be picked up.

    Uses an unknown literal that follows a table word directly. (Schema-
    known intervening words like 'supplier' would, by design, block the
    value pickup — see the phase-13-#1 fix.)
    """
    pre = preprocess("show products fooooobar")
    hints = detect_implicit(pre, shop_schema)
    by_value = {h.value: h.table_hint for h in hints}
    assert "fooooobar" in by_value, f"Real value rejected: {hints}"


# ---------- Phase 13 weakness #2: boolean-column false WHERE ----------


def test_boolean_column_no_spurious_name_filter(shop_schema):
    """`list products that are services` must not bind `name = 'services'`.

    Even though we don't yet auto-emit `isservice = TRUE`, the spurious
    name-filter is gone now (`service` is a part of `isservice` so
    _is_value_candidate rejects it as schema-known).
    """
    r = generate_sql(
        "list products that are services",
        shop_schema, 100, "/tmp/none.joblib", dialect="mysql",
    )
    assert r.sql is not None
    assert "= 'services'" not in r.sql.lower()
    # Should at least surface the boolean column in the projection.
    assert "isservice" in r.sql.lower()


# ---------- Phase 13 weakness #3: 'are there' → exists ----------


def test_are_there_any_routes_to_exists(shop_schema):
    r = generate_sql(
        "are there any pending orders",
        shop_schema, 100, "/tmp/none.joblib", dialect="mysql",
    )
    assert r.intent == "exists", f"expected exists, got {r.intent}"
    assert "count(*)" in r.sql.lower()
    # The exists builder emits `COUNT(*) > 0 AS "exists"` (or backtick on MySQL)
    assert "> 0" in r.sql or "> 0" in r.sql.lower()


def test_do_we_have_routes_to_exists(shop_schema):
    r = generate_sql(
        "do we have any pending orders",
        shop_schema, 100, "/tmp/none.joblib", dialect="mysql",
    )
    assert r.intent == "exists"


def test_is_there_still_routes_to_exists(shop_schema):
    """Regression: the original 'is there' phrasing must still work."""
    r = generate_sql(
        "is there an order with status pending",
        shop_schema, 100, "/tmp/none.joblib", dialect="mysql",
    )
    assert r.intent == "exists"
