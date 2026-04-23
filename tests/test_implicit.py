from __future__ import annotations

from app.nlp.implicit import detect
from app.nlp.preprocess import preprocess


def test_org_name_literal(schema):
    pre = preprocess("total users belongs to org swaraj")
    hints = detect(pre, schema)
    by_hint = {h.table_hint: h.value for h in hints}
    assert by_hint.get("Organization") == "swaraj"


def test_client_name_literal(schema):
    pre = preprocess("list employees of client acme")
    hints = detect(pre, schema)
    by_hint = {h.table_hint: h.value for h in hints}
    assert by_hint.get("Client") == "acme"


def test_named_pattern(schema):
    pre = preprocess("show client named acme corp")
    hints = detect(pre, schema)
    by_hint = {h.table_hint: h.value for h in hints}
    assert "acme corp" in (by_hint.get("Client") or "")


def test_no_hint_when_value_is_schema_word(schema):
    # "client name" shouldn't emit a literal — 'name' is a column.
    pre = preprocess("list client name")
    hints = detect(pre, schema)
    assert hints == []


def test_multiple_hints(schema):
    pre = preprocess("total users in org swaraj of client acme")
    hints = detect(pre, schema)
    tables = {h.table_hint for h in hints}
    assert "Organization" in tables
    assert "Client" in tables
