"""
Runtime deterministic schema annotator — no LLM, no API calls.

Lighter twin of `tools/auto_annotate.py`: the dev-time CLI tool uses
ruamel.yaml to preserve comments when re-annotating a hand-edited
schema. This module runs *immediately after introspection* on a freshly
written YAML where there are no comments to preserve, so PyYAML
(already in runtime deps) is sufficient — no ruamel.

Called from /api/databases (add) and /api/databases/{name}/regenerate
so registering a new database produces a usable, synonym-rich schema
without a separate manual step.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from .nlp._acronyms import acronym_lookup, word_lookup


_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|[_\-\s]+")
_STOPWORDS = {
    "id", "the", "a", "an", "of", "to", "in", "on", "at", "by", "for",
    "and", "or", "is", "are", "was", "were", "be", "been", "being",
    "this", "that", "these", "those", "it", "its", "with", "from",
    "up", "down", "out", "off",
}
_BORING_SYNONYMS = {
    "thing", "object", "entity", "matter", "substance",
    "river", "city", "county", "mountain", "ocean", "lake", "island",
    "gens", "netmail", "ostiary",
}


_TYPE_DESCRIPTIONS = {
    "uuid":        "Unique identifier",
    "boolean":     "Yes/no flag",
    "integer":     "Integer value",
    "bigint":      "Large integer (often a timestamp in epoch ms)",
    "smallint":    "Small integer",
    "numeric":     "Decimal number",
    "float":       "Floating-point number",
    "string":      "Free-text value",
    "text":        "Free-text value",
    "varchar":     "Free-text value",
    "date":        "Calendar date",
    "timestamp":   "Date and time",
    "timestamptz": "Date and time (with timezone)",
    "jsonb":       "JSON document",
    "json":        "JSON document",
    "enum":        "Enumerated value",
}


def _split_id(name: str) -> list[str]:
    if not name:
        return []
    return [p for p in _CAMEL_RE.split(name) if p]


def _humanize(name: str) -> str:
    parts = _split_id(name)
    return " ".join(p.lower() for p in parts) if parts else name.lower()


def _wordnet_synonyms(word: str, max_n: int = 3) -> list[str]:
    """Single-sense WordNet expansion with proper-noun and common-noise filters."""
    try:
        from nltk.corpus import wordnet
    except (ImportError, LookupError):
        return []

    out: list[str] = []
    seen: set[str] = set()
    try:
        synsets = wordnet.synsets(word.lower())
    except LookupError:
        return []

    for syn in synsets[:1]:  # most common sense only
        for lemma in syn.lemmas():
            raw = lemma.name()
            if any(c.isupper() for c in raw):
                continue
            cand = raw.replace("_", " ").lower()
            if cand == word.lower() or cand in seen or cand in _STOPWORDS:
                continue
            if any(b in cand.split() for b in _BORING_SYNONYMS):
                continue
            if cand in _BORING_SYNONYMS:
                continue
            if len(cand) < 3:
                continue
            seen.add(cand)
            out.append(cand)
            if len(out) >= max_n:
                return out
    return out


def _make_description(
    column_name: str,
    column_type: str,
    is_pk: bool,
    fk: str | None,
) -> str:
    name_human = _humanize(column_name)
    parts = _split_id(column_name)

    if is_pk:
        return f"Primary key — {name_human}."
    if fk:
        ref = fk.split(".", 1) if "." in fk else (fk, "id")
        return f"Foreign key to {ref[0]}.{ref[1]}."

    if len(parts) == 1 and acronym_lookup(parts[0].lower()):
        canonical = acronym_lookup(parts[0].lower())[0]
        return f"{column_name} — {canonical}."

    base = _TYPE_DESCRIPTIONS.get(column_type.lower(), "")
    if base:
        return f"{name_human} — {base.lower()}."
    return f"{name_human}."


def _generate_synonyms(
    column_name: str,
    table_name: str,
    other_columns_on_table: set[str],
    max_n: int = 5,
) -> list[str]:
    parts = [p.lower() for p in _split_id(column_name)]
    name_lc = column_name.lower()
    other_cols_lc = {c.lower() for c in other_columns_on_table if c != column_name}

    out: list[str] = []

    def _add(syn: str) -> None:
        s = syn.strip().lower()
        if not s or len(s) < 2 or s in _STOPWORDS:
            return
        if s == name_lc or s in other_cols_lc:
            return
        if s in {x.lower() for x in out}:
            return
        out.append(s)

    # 1. Whole-name acronym
    if len(parts) == 1:
        for syn in acronym_lookup(parts[0]):
            _add(syn)
            if len(out) >= max_n:
                return out[:max_n]

    # 2. Humanized whole name
    if len(parts) > 1:
        _add(" ".join(parts))
        if len(out) >= max_n:
            return out[:max_n]

    # 3. Per-word synonym dict
    for part in parts:
        for syn in word_lookup(part):
            _add(syn)
            if len(out) >= max_n:
                return out[:max_n]

    # 4. WordNet (single-word common nouns only)
    if len(parts) == 1 and len(parts[0]) >= 4:
        for syn in _wordnet_synonyms(parts[0], max_n=3):
            _add(syn)
            if len(out) >= max_n:
                return out[:max_n]

    # 5. Composite ("client name", "products code")
    if len(parts) == 1 and parts[0] in {
        "name", "id", "code", "no", "type", "status", "date",
        "amount", "price", "address", "phone", "email",
    }:
        composite = f"{_humanize(table_name)} {parts[0]}"
        if composite != name_lc:
            _add(composite)

    return out[:max_n]


def _annotate_table(table: dict) -> dict:
    """Mutate `table` in place with description + synonyms. Returns counts."""
    columns = list(table.get("columns") or [])
    other_cols = {c.get("name", "") for c in columns}

    if not (table.get("description") or "").strip():
        table["description"] = _humanize(table["name"]).capitalize() + "."

    syn_count = 0
    for col in columns:
        col_name = col.get("name", "")
        col_type = col.get("type", "unknown")
        is_pk = bool(col.get("pk"))
        fk = col.get("fk")

        if not (col.get("description") or "").strip():
            col["description"] = _make_description(col_name, col_type, is_pk, fk)

        # Skip ID/timestamp/bookkeeping for synonyms
        if is_pk or fk:
            continue
        if col_type.lower() in {"timestamp", "timestamptz", "date"}:
            continue
        if col_name.lower() in {
            "createdat", "updatedat", "createdby", "updatedby", "revision", "id",
            "created_at", "updated_at", "created_by", "updated_by",
        }:
            continue

        new_syns = _generate_synonyms(col_name, table["name"], other_cols)
        existing = list(col.get("synonyms") or [])
        seen = {s.lower() for s in existing}
        for s in new_syns:
            if s.lower() not in seen:
                existing.append(s)
                seen.add(s.lower())
        if existing:
            col["synonyms"] = existing
            syn_count += len(new_syns)

    return {"columns": len(columns), "synonyms_added": syn_count}


def annotate_yaml_in_place(path: Path) -> dict:
    """Read YAML, annotate every table, write back. Returns aggregate stats.

    Idempotent: a second run on an already-annotated file is a no-op
    (existing descriptions/synonyms are preserved; only empties get filled).
    """
    if not path.is_file():
        raise FileNotFoundError(f"schema file not found: {path}")
    doc = yaml.safe_load(path.read_text())
    if not doc or "tables" not in doc:
        return {"tables": 0, "columns": 0, "synonyms_added": 0}

    total_cols = 0
    total_syns = 0
    for t in doc.get("tables") or []:
        stats = _annotate_table(t)
        total_cols += stats["columns"]
        total_syns += stats["synonyms_added"]

    path.write_text(yaml.safe_dump(doc, sort_keys=False, width=120))
    return {
        "tables": len(doc.get("tables") or []),
        "columns": total_cols,
        "synonyms_added": total_syns,
    }
