"""
Schema DSL loader.

The DSL is a YAML document describing tables, columns, and relationships.
It serves three purposes:
  1. Human-editable source of truth for what the LLM may query.
  2. Compact summary fed to the LLM to keep prompts small.
  3. Whitelist for the validator so the LLM can't reference unknown tables/columns.

Top-level shape:

    database: tmt
    description: Employee Transport System
    tables:
      - name: Employee
        description: Registered employees
        columns:
          - name: id
            type: uuid
          - name: name
            type: string
        relationships:
          - to: Client
            type: many-to-one
            via: clientId
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import yaml


@dataclass
class Column:
    name: str
    type: str = "unknown"
    description: str = ""
    synonyms: list[str] = field(default_factory=list)
    pk: bool = False
    fk: str | None = None


@dataclass
class Relationship:
    to: str
    type: str = "many-to-one"
    via: str | None = None


@dataclass
class Table:
    name: str
    description: str = ""
    columns: list[Column] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)

    def column_names(self) -> set[str]:
        return {c.name for c in self.columns}


@dataclass
class Schema:
    database: str
    description: str
    tables: list[Table]

    def table_map(self) -> dict[str, Table]:
        return {t.name: t for t in self.tables}

    def table_map_ci(self) -> dict[str, Table]:
        return {t.name.lower(): t for t in self.tables}


def _parse_column(raw: dict) -> Column:
    return Column(
        name=raw["name"],
        type=raw.get("type", "unknown"),
        description=raw.get("description", ""),
        synonyms=list(raw.get("synonyms") or []),
        pk=bool(raw.get("pk", False)),
        fk=raw.get("fk"),
    )


def _parse_table(raw: dict) -> Table:
    return Table(
        name=raw["name"],
        description=raw.get("description", ""),
        columns=[_parse_column(c) for c in raw.get("columns", [])],
        relationships=[
            Relationship(
                to=r["to"],
                type=r.get("type", "many-to-one"),
                via=r.get("via"),
            )
            for r in raw.get("relationships", [])
        ],
    )


def load_schema(path: str | Path) -> Schema:
    data = yaml.safe_load(Path(path).read_text())
    return Schema(
        database=data.get("database", ""),
        description=data.get("description", ""),
        tables=[_parse_table(t) for t in data.get("tables", [])],
    )


@lru_cache(maxsize=8)
def _get_schema_cached(path: str, mtime: float) -> Schema:
    return load_schema(path)


def get_schema(path: str) -> Schema:
    """Load a schema DSL, cached per (path, mtime) so edits take effect."""
    try:
        mtime = Path(path).stat().st_mtime
    except FileNotFoundError:
        mtime = 0.0
    return _get_schema_cached(path, mtime)


def invalidate_cache() -> None:
    _get_schema_cached.cache_clear()


def summarize_for_prompt(schema: Schema, tables: Iterable[str] | None = None) -> str:
    """Render a compact textual summary of the schema for LLM consumption.

    Only includes tables in `tables` (if given) so we can pre-filter large
    schemas to the ones relevant to a question and keep tokens low.
    """
    allowed = {t.lower() for t in tables} if tables else None
    lines: list[str] = []
    if schema.description:
        lines.append(f"# {schema.database} — {schema.description}")
    for t in schema.tables:
        if allowed is not None and t.name.lower() not in allowed:
            continue
        desc = f" — {t.description}" if t.description else ""
        lines.append(f'\nTable "{t.name}"{desc}')
        for c in t.columns:
            flags = []
            if c.pk:
                flags.append("pk")
            if c.fk:
                flags.append(f"fk→{c.fk}")
            flag_str = f" [{', '.join(flags)}]" if flags else ""
            cdesc = f" — {c.description}" if c.description else ""
            lines.append(f'  - "{c.name}" {c.type}{flag_str}{cdesc}')
        for r in t.relationships:
            via = f" via {r.via}" if r.via else ""
            lines.append(f"  ~ {r.type} → {r.to}{via}")
    return "\n".join(lines)


def all_table_names(schema: Schema) -> list[str]:
    return [t.name for t in schema.tables]
