"""Introspect a live Postgres or MySQL database into the DSL format.

Factored out of scripts/bootstrap_schema.py so the API can call it
when a user adds/regenerates a DB from the UI. Dialect is detected
from the SQLAlchemy URL prefix (or explicitly passed).
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


# MySQL types differ from Postgres; map onto the same DSL vocabulary.
MYSQL_TYPE_MAP = {
    "tinyint":     "boolean",  # tinyint(1) is the boolean convention
    "smallint":    "smallint",
    "mediumint":   "integer",
    "int":         "integer",
    "integer":     "integer",
    "bigint":      "bigint",
    "decimal":     "numeric",
    "numeric":     "numeric",
    "float":       "float",
    "double":      "float",
    "real":        "float",
    "char":        "string",
    "varchar":     "string",
    "tinytext":    "string",
    "text":        "text",
    "mediumtext":  "text",
    "longtext":    "text",
    "enum":        "enum",
    "set":         "enum",
    "date":        "date",
    "time":        "string",
    "datetime":    "timestamp",
    "timestamp":   "timestamptz",
    "year":        "integer",
    "binary":      "string",
    "varbinary":   "string",
    "blob":        "string",
    "tinyblob":    "string",
    "mediumblob":  "string",
    "longblob":    "string",
    "json":        "jsonb",
}


def _detect_dialect_from_url(url: str) -> str:
    return "mysql" if url.startswith("mysql") else "postgres"


def _map_mysql_type(data_type: str, column_type: str) -> str:
    base = (data_type or "").lower()
    full = (column_type or "").lower()
    if base == "tinyint":
        return "boolean" if "tinyint(1)" in full else "smallint"
    return MYSQL_TYPE_MAP.get(base, base or "unknown")


BOOKKEEPING_COLS = {
    "revision", "enabled", "createdAt", "updatedAt", "createdBy", "updatedBy",
    "created_at", "updated_at", "created_by", "updated_by",
    "deleted_at", "deletedAt",
}


def introspect(url: str, dialect: str | None = None) -> list[dict]:
    if dialect is None:
        dialect = _detect_dialect_from_url(url)
    if dialect == "mysql":
        return _introspect_mysql(url)
    return _introspect_postgres(url)


def _introspect_mysql(url: str) -> list[dict]:
    """MySQL information_schema differs slightly from Postgres."""
    from sqlalchemy.engine.url import make_url
    db_name = make_url(url).database
    engine = create_engine(url)
    with engine.connect() as conn:
        cols = conn.execute(
            text(
                """
                SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, COLUMN_TYPE,
                       ORDINAL_POSITION
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = :schema
                ORDER BY TABLE_NAME, ORDINAL_POSITION
                """
            ),
            {"schema": db_name},
        ).fetchall()

        pks_raw = conn.execute(
            text(
                """
                SELECT TABLE_NAME, COLUMN_NAME
                FROM information_schema.KEY_COLUMN_USAGE
                WHERE TABLE_SCHEMA = :schema
                  AND CONSTRAINT_NAME = 'PRIMARY'
                """
            ),
            {"schema": db_name},
        ).fetchall()
        pk_set = {(r[0], r[1]) for r in pks_raw}

        fks_raw = conn.execute(
            text(
                """
                SELECT TABLE_NAME, COLUMN_NAME,
                       REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
                FROM information_schema.KEY_COLUMN_USAGE
                WHERE TABLE_SCHEMA = :schema
                  AND REFERENCED_TABLE_NAME IS NOT NULL
                """
            ),
            {"schema": db_name},
        ).fetchall()
        fk_map = {(r[0], r[1]): f"{r[2]}.{r[3]}" for r in fks_raw}

    tables_raw: dict[str, list[dict]] = {}
    for table_name, col_name, data_type, column_type, _ in cols:
        tables_raw.setdefault(table_name, []).append({
            "name": col_name,
            "type": _map_mysql_type(data_type, column_type),
            "pk": (table_name, col_name) in pk_set,
            "fk": fk_map.get((table_name, col_name)),
        })

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


def _introspect_postgres(url: str) -> list[dict]:
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


def generate_and_write(
    url: str, out_path: Path, db_name: str,
    dialect: str | None = None,
) -> int:
    """Introspect `url` and write the DSL YAML to `out_path`. Returns table count."""
    tables = introspect(url, dialect=dialect)
    doc = {
        "database": db_name,
        "description": "Auto-generated by nl2sql — edit descriptions/synonyms as needed.",
        "tables": tables,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(yaml.safe_dump(doc, sort_keys=False, width=120))
    return len(tables)
