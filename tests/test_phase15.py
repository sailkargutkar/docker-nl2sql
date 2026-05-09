"""
Regression tests for Phase-15 enhancements:

  Fix #2: compound-name splitting (`onlineorders` → `online + orders`)
  Fix #1: auto `WHERE bool_col = TRUE` on "are X" / "is X" / "has X"
  Fix #3: negative-exists `WHERE col IS NULL` on "without X" / "no X"

Tests use the regex-fallback path (no trained classifier).
"""

from __future__ import annotations

import pytest

from app.generator import generate_sql
from app.nlp._compound import split_compound
from app.nlp.matcher import _split_identifier
from app.schema_dsl import Column, Schema, Table


@pytest.fixture(autouse=True)
def _reset_classifier():
    from app import generator
    generator._singleton = None
    yield
    generator._singleton = None


# ---------- Fix #2: compound-name splitting ----------


class TestCompoundSplit:
    def test_split_known_prefix_orders(self):
        assert split_compound("onlineorders") == ("online", "orders")

    def test_split_known_prefix_categories(self):
        assert split_compound("taxcategories") == ("tax", "categories")

    def test_split_known_prefix_vehicle(self):
        assert split_compound("vehiclemodel") == ("vehicle", "model")

    def test_no_split_when_too_short(self):
        # 5 chars: below the 6-char floor.
        assert split_compound("notes") == ("notes",)

    def test_no_split_when_unknown_prefix(self):
        # `aux` not in the prefix list — leave unchanged so we don't
        # mis-split unfamiliar tables.
        assert split_compound("auxproduct") == ("auxproduct",)

    def test_no_split_when_remainder_too_short(self):
        # `tax` matches but `xx` is < 3 chars; reject.
        assert split_compound("taxxx") == ("taxxx",)

    def test_split_identifier_uses_compound(self):
        # End-to-end through the matcher's split function — should
        # propagate compound splits with lemmatisation.
        assert _split_identifier("onlineorders") == ("online", "order")
        assert _split_identifier("taxcategories") == ("tax", "category")


# ---------- Fix #1: auto `WHERE bool_col = TRUE` ----------


@pytest.fixture
def shop_schema() -> Schema:
    return Schema(
        database="shop",
        description="",
        tables=[
            Table(
                name="products",
                columns=[
                    Column(name="id", type="integer", pk=True),
                    Column(name="name", type="string"),
                    Column(name="category", type="string"),
                    Column(name="supplier", type="string"),
                    Column(name="warranty", type="integer"),
                    Column(name="isservice", type="boolean"),
                    Column(name="isscale", type="boolean"),
                    Column(name="isconstant", type="boolean"),
                    Column(name="hasdiscount", type="boolean"),
                ],
            ),
            Table(
                name="onlineorders",
                columns=[
                    Column(name="id", type="bigint", pk=True),
                    Column(name="paymentStatus", type="string"),
                ],
            ),
        ],
    )


class TestBoolAutoTrue:
    def _ask(self, q: str, schema):
        return generate_sql(q, schema, 100, "/tmp/none.joblib", dialect="mysql")

    def test_are_X_binds_isX_true(self, shop_schema):
        r = self._ask("list products that are services", shop_schema)
        assert "isservice` = TRUE" in r.sql

    def test_are_X_binds_isX_with_alt_word(self, shop_schema):
        r = self._ask("show products that are scaled", shop_schema)
        assert "isscale` = TRUE" in r.sql

    def test_are_constant(self, shop_schema):
        r = self._ask("products that are constant", shop_schema)
        assert "isconstant` = TRUE" in r.sql

    def test_negative_predicate_on_bool_col(self, shop_schema):
        r = self._ask("products that are not services", shop_schema)
        assert "isservice` = FALSE" in r.sql

    def test_has_prefix_too(self, shop_schema):
        r = self._ask("products that have discount", shop_schema)
        assert "hasdiscount` = TRUE" in r.sql

    def test_no_spurious_name_filter(self, shop_schema):
        """Predicate words must not also produce `name = '<word>'`."""
        r = self._ask("list products that are services", shop_schema)
        assert "= 'services'" not in r.sql.lower()


# ---------- Fix #3: negative-exists IS NULL ----------


class TestNegativeExists:
    def _ask(self, q: str, schema):
        return generate_sql(q, schema, 100, "/tmp/none.joblib", dialect="mysql")

    def test_without_category(self, shop_schema):
        r = self._ask("any products without a category", shop_schema)
        assert "category` IS NULL" in r.sql

    def test_without_supplier(self, shop_schema):
        r = self._ask("products without supplier", shop_schema)
        assert "supplier` IS NULL" in r.sql

    def test_no_warranty(self, shop_schema):
        r = self._ask("products with no warranty", shop_schema)
        assert "warranty` IS NULL" in r.sql

    def test_without_payment_status(self, shop_schema):
        r = self._ask("are there any orders without payment status", shop_schema)
        assert r.intent == "exists"
        assert "paymentStatus` IS NULL" in r.sql or "paymentstatus` IS NULL" in r.sql.lower()

    def test_no_does_not_pollute_random_bool_col(self, shop_schema):
        """The bare 'no' should NOT bind isservice = FALSE etc."""
        r = self._ask("products with no warranty", shop_schema)
        # Only the warranty IS NULL filter — no spurious boolean = FALSE.
        assert "isservice" not in r.sql or "= FALSE" not in r.sql
