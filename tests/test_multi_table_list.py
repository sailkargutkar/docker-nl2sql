"""
Phase-16a: multi-table LIST queries.

When the user says "X with their Y" or similar — and both tables are
FK-connected — the builder should JOIN both and project default columns
from each, instead of returning only the primary table.
"""

from __future__ import annotations

import pytest

from app.generator import generate_sql
from app.schema_dsl import Column, Schema, Table


@pytest.fixture(autouse=True)
def _reset_classifier():
    from app import generator
    generator._singleton = None
    yield
    generator._singleton = None


@pytest.fixture
def shop_schema() -> Schema:
    """Two FK-connected tables: products belong to a category."""
    return Schema(
        database="shop",
        description="",
        tables=[
            Table(
                name="categories",
                columns=[
                    Column(name="id", type="integer", pk=True),
                    Column(name="name", type="string"),
                    Column(name="parent_id", type="integer"),
                ],
            ),
            Table(
                name="products",
                columns=[
                    Column(name="id", type="integer", pk=True),
                    Column(name="name", type="string"),
                    Column(name="category_id", type="integer", fk="categories.id"),
                    Column(name="price", type="numeric"),
                ],
            ),
            Table(
                name="orphan",
                columns=[
                    Column(name="id", type="integer", pk=True),
                    Column(name="label", type="string"),
                ],
            ),
        ],
    )


def _ask(q: str, schema, dialect="postgres"):
    return generate_sql(q, schema, 100, "/tmp/none.joblib", dialect=dialect)


# ---------- positive cases: should JOIN ----------


class TestMultiTableListJoin:
    def test_show_X_with_their_Y(self, shop_schema):
        sql = _ask("Show all categories with their products", shop_schema).sql
        assert sql is not None
        u = sql.upper()
        assert "JOIN" in u, f"expected JOIN, got: {sql}"
        # Both tables in projection
        assert "categories" in sql.lower()
        assert "products" in sql.lower()

    def test_list_X_and_their_Y(self, shop_schema):
        sql = _ask("list categories and their products", shop_schema).sql
        assert "JOIN" in sql.upper()
        assert "categories" in sql.lower()
        assert "products" in sql.lower()

    def test_works_in_either_direction(self, shop_schema):
        # "products with their category" — primary likely flips
        sql = _ask("list products with their category", shop_schema).sql
        assert "JOIN" in sql.upper()
        assert "categories" in sql.lower()
        assert "products" in sql.lower()


# ---------- negative cases: should NOT JOIN ----------


class TestSingleTableListUnchanged:
    def test_plain_list_stays_single_table(self, shop_schema):
        """Regression: a normal `list X` query must still be single-table."""
        sql = _ask("list all products", shop_schema).sql
        assert "JOIN" not in sql.upper()
        assert "products" in sql.lower()
        assert "categories" not in sql.lower()

    def test_count_stays_single_table(self, shop_schema):
        """Aggregations are unaffected by the multi-table-list logic."""
        sql = _ask("how many products are there", shop_schema).sql
        assert "JOIN" not in sql.upper()
        assert "COUNT(*)" in sql.upper()

    def test_no_join_when_no_fk_path(self, shop_schema):
        """When the second table has no FK path, single-table list."""
        sql = _ask("list categories with their orphan", shop_schema).sql
        # Should not crash; either a single-table list on whichever scored
        # higher, or no SQL at all if matching fails. Must NOT JOIN to orphan.
        if sql:
            assert "JOIN" not in sql.upper() or "orphan" not in sql.lower()
