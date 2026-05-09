"""
Tests for Phase-16 top-K alternatives — the foundation for the planned
sketch-and-repair loop and the user-facing MISP top-K UI.
"""

from __future__ import annotations

import pytest

from app.generator import (
    GenerationResult,
    generate_alternatives,
    generate_sql,
)
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
                name="products",
                columns=[
                    Column(name="id", type="integer", pk=True),
                    Column(name="name", type="string"),
                    Column(name="category", type="string"),
                    Column(name="pricesell", type="numeric"),
                ],
            ),
            Table(
                name="auxproduct",
                columns=[
                    Column(name="id", type="integer", pk=True),
                    Column(name="productId", type="string"),
                ],
            ),
        ],
    )


# ---------- generate_sql with force_* params ----------


class TestForcedGeneration:
    def test_force_intent_overrides_classifier(self, shop_schema):
        # Default for "list products" → list. Force it to count.
        r = generate_sql(
            "list products", shop_schema, 100, "/tmp/none.joblib",
            dialect="mysql", force_intent="count",
        )
        assert r.intent == "count"
        assert "COUNT(*)" in r.sql.upper()

    def test_force_unknown_intent_falls_through(self, shop_schema):
        # Bogus intent should be ignored — generator falls back to classifier.
        r = generate_sql(
            "list products", shop_schema, 100, "/tmp/none.joblib",
            dialect="mysql", force_intent="bogus_intent",
        )
        # Generator chose its own intent — must be a real one.
        assert r.intent in ("count", "list", "sum", "avg", "min", "max",
                             "top", "exists")

    def test_force_primary_table(self, shop_schema):
        # "list" is ambiguous between products and auxproduct.
        # Force it to auxproduct.
        r = generate_sql(
            "list products", shop_schema, 100, "/tmp/none.joblib",
            dialect="mysql", force_primary_table="auxproduct",
        )
        assert "auxproduct" in r.sql.lower()
        assert "auxproduct" in [t.lower() for t in (r.tables_used or [])]


# ---------- generate_alternatives ----------


class TestGenerateAlternatives:
    def test_returns_at_most_n(self, shop_schema):
        out = generate_alternatives(
            "list products", shop_schema, 100, "/tmp/none.joblib",
            dialect="mysql", n=3,
        )
        assert len(out) <= 3
        assert all(isinstance(r, GenerationResult) for r in out)

    def test_primary_stays_at_index_zero(self, shop_schema):
        """Even when an alternative has higher raw confidence, primary
        must come first — it's the system's best guess."""
        out = generate_alternatives(
            "list products", shop_schema, 100, "/tmp/none.joblib",
            dialect="mysql", n=3,
        )
        assert out, "expected at least the primary candidate"
        # The primary candidate is the call without force_* params.
        # Its result should be re-derivable.
        baseline = generate_sql(
            "list products", shop_schema, 100, "/tmp/none.joblib",
            dialect="mysql",
        )
        assert out[0].sql == baseline.sql

    def test_alternatives_are_distinct(self, shop_schema):
        out = generate_alternatives(
            "list products", shop_schema, 100, "/tmp/none.joblib",
            dialect="mysql", n=3,
        )
        sqls = [r.sql for r in out]
        assert len(set(sqls)) == len(sqls), f"duplicates: {sqls}"

    def test_no_results_when_unmatchable(self, shop_schema):
        out = generate_alternatives(
            "tell me about the thing", shop_schema, 100, "/tmp/none.joblib",
            dialect="mysql", n=3,
        )
        # Either zero candidates or all with empty SQL — just must not raise.
        assert isinstance(out, list)


# ---------- API surface (smoke) ----------


class TestApiAlternatives:
    """Smoke test that AskResponse carries an `alternatives` field.

    We patch settings rather than relying on env-var overrides, because
    Settings() is constructed at import time.
    """

    def test_response_includes_alternatives_field(self, tmp_path, shop_schema, monkeypatch):
        import yaml
        schema_path = tmp_path / "shop.yml"
        schema_path.write_text(yaml.safe_dump({
            "database": "shop",
            "description": "",
            "tables": [
                {"name": t.name, "description": t.description,
                 "columns": [{"name": c.name, "type": c.type,
                              **({"pk": True} if c.pk else {})}
                             for c in t.columns]}
                for t in shop_schema.tables
            ],
        }))
        history_db = tmp_path / "history.db"
        intent_path = tmp_path / "intent.joblib"

        from app import config
        monkeypatch.setattr(config.settings, "history_db", str(history_db))
        monkeypatch.setattr(config.settings, "intent_model_path", str(intent_path))
        monkeypatch.setattr(config.settings, "schema_file", str(schema_path))

        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as c:
            r = c.post("/api/ask", json={
                "question": "list products", "execute": False,
            })
            assert r.status_code == 200, r.text
            body = r.json()
            assert "alternatives" in body
            assert isinstance(body["alternatives"], list)
