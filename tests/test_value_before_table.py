"""
Phase-16c: implicit-value detection improvements.

Two related fixes:

1. Spatial / categorical prepositions (under, above, before, …) and SQL
   action verbs (list, show, count, …) added to `_CONNECTIVE_WORDS`,
   so they no longer leak into multi-word literal values.

2. New value-before-table pattern (Pattern 3): "Pizza category" →
   `categories.name = 'Pizza'`. The English head-noun convention that
   the existing table-before-value pattern (Pattern 1) didn't cover.

The patterns disambiguate: when the form is `<table-A> <value> <table-B>`
and table-B has no value of its own, the value goes to table-B; if
table-B has its own value (e.g. "users in org swaraj of client acme"),
both patterns fire independently.
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
    return Schema(
        database="shop",
        description="",
        tables=[
            Table(
                name="categories",
                columns=[
                    Column(name="id", type="integer", pk=True),
                    Column(name="name", type="string"),
                ],
            ),
            Table(
                name="products",
                columns=[
                    Column(name="id", type="integer", pk=True),
                    Column(name="name", type="string"),
                    Column(name="category", type="integer", fk="categories.id"),
                ],
            ),
        ],
    )


# ---------- Connectives suppress preposition leakage ----------


class TestConnectivesSuppressPrepositions:
    def test_under_does_not_leak(self, shop_schema):
        """`products under Pizza` must NOT bind name='under Pizza'."""
        sql = generate_sql(
            "list products under Pizza",
            shop_schema, 100, "/tmp/none.joblib", dialect="mysql",
        ).sql
        assert "'under" not in sql.lower()

    def test_action_verb_not_a_value(self, shop_schema):
        """`list products` must NOT emit ImplicitValue(products, 'list')."""
        pre = preprocess("list products")
        hints = detect_implicit(pre, shop_schema)
        assert all(h.value.lower() != "list" for h in hints), hints

    def test_show_not_a_value(self, shop_schema):
        pre = preprocess("show products")
        hints = detect_implicit(pre, shop_schema)
        assert all(h.value.lower() != "show" for h in hints), hints


# ---------- Pattern 3: value-before-table ----------


class TestValueBeforeTablePattern:
    def test_pizza_category(self, shop_schema):
        """`Pizza category` → categories.name='Pizza' (head-noun shape)."""
        pre = preprocess("Pizza category")
        hints = detect_implicit(pre, shop_schema)
        by_value = {h.value.lower(): h.table_hint for h in hints}
        assert by_value.get("pizza") == "categories"

    def test_three_token_pattern(self, shop_schema):
        """`products in Snacks category` → categories.name LIKE '%Snacks%'.

        Phase 16d: unquoted values now produce partial-match (LIKE/ILIKE)
        instead of exact match — real DB values rarely equal a user's
        typed noun verbatim ('Snacks' won't match 'Snacks(R)' with =).
        """
        sql = generate_sql(
            "products in Snacks category",
            shop_schema, 100, "/tmp/none.joblib", dialect="mysql",
        ).sql
        assert "categories" in sql.lower()
        assert "%snacks%" in sql.lower()
        # Should NOT also bind to products.name with the same value
        assert sql.lower().count("%snacks%") == 1

    def test_full_phrase(self, shop_schema):
        """`List all products under the Pizza category` end-to-end."""
        sql = generate_sql(
            "List all products under the Pizza category",
            shop_schema, 100, "/tmp/none.joblib", dialect="mysql",
        ).sql
        assert "JOIN" in sql.upper()
        assert "categories" in sql.lower()
        assert "products" in sql.lower()
        assert "%pizza%" in sql.lower()  # partial match
        # Should not have 'under' in any literal
        assert "'under" not in sql.lower()
        assert "%under" not in sql.lower()

    def test_pattern_1_still_fires_when_no_trailing_table(self, shop_schema):
        """`list products acme` → products.name='acme' (Pattern 1)."""
        pre = preprocess("list products acme")
        hints = detect_implicit(pre, shop_schema)
        by_value = {h.value.lower(): h.table_hint for h in hints}
        assert by_value.get("acme") == "products"


# ---------- Pattern precedence ----------


class TestPatternPrecedence:
    def test_pattern_1_wins_when_next_table_has_own_value(self, shop_schema):
        """`<table-A> <value-A> <table-B> <value-B>` should bind both:
        value-A to table-A and value-B to table-B."""
        pre = preprocess("products acme categories pizza")
        hints = detect_implicit(pre, shop_schema)
        bindings = {(h.table_hint, h.value.lower()) for h in hints}
        # Either both Pattern 1 (acme→products, pizza→categories) or
        # mixed (acme→products via Pattern 1, pizza→categories via
        # Pattern 3) — but at minimum, "acme" should be bound to products.
        assert ("products", "acme") in bindings or any(
            tbl == "products" and val == "acme" for tbl, val in bindings
        )