"""
Phase 16b: when /api/databases adds a new DB, the freshly introspected
YAML must come back with descriptions and synonyms — not bare names.

Also exercises app/annotate.py directly for finer-grained checks.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.annotate import (
    _generate_synonyms,
    _make_description,
    annotate_yaml_in_place,
)


# ---------- Direct unit tests on the annotator ----------


class TestAnnotator:
    def test_acronym_synonyms_picked_up(self):
        out = _generate_synonyms("GSTIN", "Client", set())
        # Acronym table entries for GSTIN
        assert "gst number" in out or "tax id" in out

    def test_pan_acronym(self):
        out = _generate_synonyms("PAN", "Client", set())
        assert any(s.startswith("pan") or s == "tax id" for s in out)

    def test_humanized_camel_case(self):
        out = _generate_synonyms("paymentTerm", "Invoice", set())
        assert "payment term" in out

    def test_skip_synonyms_for_pk(self):
        # _generate_synonyms still returns; the SKIP happens in _annotate_table.
        # Here we just verify pk descriptions are clear.
        d = _make_description("id", "uuid", is_pk=True, fk=None)
        assert "primary key" in d.lower()

    def test_fk_description(self):
        d = _make_description("clientId", "uuid", is_pk=False, fk="Client.id")
        assert "foreign key" in d.lower()
        assert "Client" in d

    def test_no_collision_with_other_columns(self):
        # If table has 'name' column, 'title' should NOT be emitted as a
        # synonym for 'name' (would collide). Actually the annotator allows
        # 'title' for 'name' — but it shouldn't collide with another column.
        # Test: 'description' for column 'name' won't appear if there's also
        # a 'description' column.
        out = _generate_synonyms("name", "Item", {"name", "description"})
        assert "description" not in out


# ---------- File-level annotation ----------


class TestAnnotateYamlInPlace:
    def test_round_trip_adds_synonyms(self, tmp_path: Path):
        path = tmp_path / "demo.yml"
        path.write_text(yaml.safe_dump({
            "database": "demo",
            "description": "",
            "tables": [{
                "name": "Client",
                "description": "",
                "columns": [
                    {"name": "id", "type": "uuid", "pk": True},
                    {"name": "name", "type": "string"},
                    {"name": "GSTIN", "type": "string"},
                    {"name": "paymentTerm", "type": "integer"},
                    {"name": "createdAt", "type": "timestamptz"},
                ],
            }],
        }))
        stats = annotate_yaml_in_place(path)
        assert stats["tables"] == 1
        assert stats["columns"] == 5

        loaded = yaml.safe_load(path.read_text())
        cols = loaded["tables"][0]["columns"]
        by_name = {c["name"]: c for c in cols}

        # PK has a description but no synonyms
        assert "primary key" in by_name["id"]["description"].lower()
        assert not by_name["id"].get("synonyms")

        # GSTIN has acronym synonyms
        assert by_name["GSTIN"].get("synonyms")
        gstin_syns = {s.lower() for s in by_name["GSTIN"]["synonyms"]}
        assert "gst number" in gstin_syns or "tax id" in gstin_syns

        # paymentTerm has the humanized form
        pterm_syns = {s.lower() for s in by_name["paymentTerm"].get("synonyms") or []}
        assert "payment term" in pterm_syns

        # createdAt — bookkeeping, no synonyms expected
        assert not by_name["createdAt"].get("synonyms")

    def test_idempotent(self, tmp_path: Path):
        """Running annotate twice should not duplicate synonyms."""
        path = tmp_path / "demo.yml"
        path.write_text(yaml.safe_dump({
            "database": "demo",
            "description": "",
            "tables": [{
                "name": "Client",
                "columns": [{"name": "GSTIN", "type": "string"}],
            }],
        }))
        annotate_yaml_in_place(path)
        first = yaml.safe_load(path.read_text())
        annotate_yaml_in_place(path)
        second = yaml.safe_load(path.read_text())
        assert first == second

    def test_preserves_existing_descriptions(self, tmp_path: Path):
        """Manually-edited descriptions must NOT be overwritten."""
        path = tmp_path / "demo.yml"
        path.write_text(yaml.safe_dump({
            "database": "demo",
            "description": "",
            "tables": [{
                "name": "Client",
                "columns": [{
                    "name": "name", "type": "string",
                    "description": "Customer-provided display name (manually edited)",
                }],
            }],
        }))
        annotate_yaml_in_place(path)
        loaded = yaml.safe_load(path.read_text())
        col = loaded["tables"][0]["columns"][0]
        assert "manually edited" in col["description"]
