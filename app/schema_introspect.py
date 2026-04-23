"""Introspect a live Postgres database into the DSL format.

Same logic as scripts/bootstrap_schema.py, factored out so the API can call it
when a user adds/regenerates a DB from the UI.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from sqlalchemy import create_engine, text


TYPE_MAP = {
    "uuid": "uuid",
    "text": "text",
    "character varying": "string",
    "varchar": "string",
    "integer": "integer",
    "bigint": "bigint",
    "smallint": "smallint",
    "numeric": "numeric",
    "double precision": "float",
    "real": "float",
    "boolean": "boolean",
    "timestamp without time zone": "timestamp",
    "timestamp with time zone": "timestamptz",
    "date": "date",
    "jsonb": "jsonb",
    "json": "json",
    "USER-DEFINED": "enum",
}

BOOKKEEPING_COLS = {
    "revision", "enabled", "createdAt", "updatedAt", "createdBy", "updatedBy",
}


def introspect(url: str) -> list[dict]:
    engine = create_engine(url)
    with engine.connect() as conn:
        cols = conn.execute(
            text(
                """
                SELECT table_name, column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = 'public'
                ORDER BY table_name, ordinal_position
                """
            )
        ).fetchall()

        pks = conn.execute(
            text(
                """
                SELECT kcu.table_name, kcu.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name
                WHERE tc.constraint_type = 'PRIMARY KEY'
                  AND tc.table_schema = 'public'
                """
            )
        ).fetchall()
        pk_set = {(r[0], r[1]) for r in pks}

        fks = conn.execute(
            text(
                """
                SELECT
                    kcu.table_name, kcu.column_name,
                    ccu.table_name AS foreign_table,
                    ccu.column_name AS foreign_column
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name
                JOIN information_schema.constraint_column_usage ccu
                  ON ccu.constraint_name = tc.constraint_name
                WHERE tc.constraint_type = 'FOREIGN KEY'
                  AND tc.table_schema = 'public'
                """
            )
        ).fetchall()
        fk_map = {(r[0], r[1]): f"{r[2]}.{r[3]}" for r in fks}

    tables_raw: dict[str, list[dict]] = {}
    for table_name, col_name, data_type in cols:
        tables_raw.setdefault(table_name, []).append(
            {
                "name": col_name,
                "type": TYPE_MAP.get(data_type, data_type),
                "pk": (table_name, col_name) in pk_set,
                "fk": fk_map.get((table_name, col_name)),
            }
        )

    out: list[dict] = []
    for name, columns in tables_raw.items():
        clean_cols = []
        for c in columns:
            entry = {"name": c["name"], "type": c["type"]}
            if c["pk"]:
                entry["pk"] = True
            if c["fk"]:
                entry["fk"] = c["fk"]
            clean_cols.append(entry)
        out.append({"name": name, "description": "", "columns": clean_cols})
    out.sort(key=lambda t: t["name"])

    _annotate_junction_tables(out)
    return out


def _annotate_junction_tables(tables: list[dict]) -> None:
    """Mark M:N junction tables and mirror the relationship on both parents."""
    for t in tables:
        fk_pks = [c for c in t["columns"] if c.get("pk") and c.get("fk")]
        if len(fk_pks) != 2:
            continue
        other_cols = [
            c for c in t["columns"]
            if not c.get("pk") and c["name"] not in BOOKKEEPING_COLS
        ]
        if other_cols:
            continue

        left_tbl, _ = fk_pks[0]["fk"].split(".")
        right_tbl, _ = fk_pks[1]["fk"].split(".")
        if left_tbl == right_tbl:
            continue

        t["description"] = f"Junction table: connects {left_tbl} ↔ {right_tbl} (many-to-many)."

        for owner, other, own_col, other_col_name in (
            (left_tbl, right_tbl, fk_pks[0]["name"], fk_pks[1]["name"]),
            (right_tbl, left_tbl, fk_pks[1]["name"], fk_pks[0]["name"]),
        ):
            for target in tables:
                if target["name"] != owner:
                    continue
                rels = target.setdefault("relationships", [])
                rels.append({
                    "to": other,
                    "type": "many-to-many",
                    "via": f"{t['name']} ({own_col} → {other_col_name})",
                })


def generate_and_write(url: str, out_path: Path, db_name: str) -> int:
    """Introspect `url` and write the DSL YAML to `out_path`. Returns table count."""
    tables = introspect(url)
    doc = {
        "database": db_name,
        "description": "Auto-generated by nl2sql — edit descriptions/synonyms as needed.",
        "tables": tables,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(yaml.safe_dump(doc, sort_keys=False, width=120))
    return len(tables)
