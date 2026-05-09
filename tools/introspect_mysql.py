"""
Introspect a MySQL database into the YAML schema format used by the rest
of the system. Read-only: only reads `information_schema`, never alters
your database.

This is a *new file* added to support trying the deterministic annotator
against MySQL schemas. The runtime (app/) remains Postgres-only — this
helper just produces YAML, which the auto-annotator and matcher can
already consume.

Usage:
  # Put credentials in .env or .claude/.env (both gitignored):
  #   MYSQL_HOST=localhost
  #   MYSQL_PORT=3306
  #   MYSQL_USER=readonly_user
  #   MYSQL_PASSWORD=...
  #   MYSQL_DATABASE=bombayhouse

  python tools/introspect_mysql.py
  python tools/introspect_mysql.py --database himalayan --out schema/himalayan.yml
  python tools/introspect_mysql.py --dry-run        # print, don't write

Output: schema/<dbname>.yml in the same shape as the existing
Postgres-introspected files, ready for `tools/auto_annotate.py`.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Make parent dir importable when run as `python tools/introspect_mysql.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools._common import ROOT, load_env  # noqa: E402


# ---------- MySQL → DSL type mapping ----------

TYPE_MAP: dict[str, str] = {
    # integer family
    "tinyint":   "boolean",   # MySQL convention: tinyint(1) is boolean
    "smallint":  "smallint",
    "mediumint": "integer",
    "int":       "integer",
    "integer":   "integer",
    "bigint":    "bigint",
    # decimal
    "decimal":   "numeric",
    "numeric":   "numeric",
    "float":     "float",
    "double":    "float",
    "real":      "float",
    # string family
    "char":      "string",
    "varchar":   "string",
    "tinytext":  "string",
    "text":      "text",
    "mediumtext": "text",
    "longtext":  "text",
    "enum":      "enum",
    "set":       "enum",
    # date/time
    "date":      "date",
    "time":      "string",
    "datetime":  "timestamp",
    "timestamp": "timestamptz",
    "year":      "integer",
    # binary / json
    "binary":    "string",
    "varbinary": "string",
    "blob":      "string",
    "tinyblob":  "string",
    "mediumblob": "string",
    "longblob":  "string",
    "json":      "jsonb",
}

BOOKKEEPING_COLS = {
    "revision", "enabled", "created_at", "createdAt",
    "updated_at", "updatedAt", "created_by", "createdBy",
    "updated_by", "updatedBy", "deleted_at", "deletedAt",
}


def _map_type(mysql_type: str, column_type: str) -> str:
    """Map a MySQL data_type + column_type into our YAML vocabulary.

    `column_type` is the full type expression (e.g. 'tinyint(1)', 'varchar(255)')
    so we can detect the boolean convention.
    """
    base = mysql_type.lower()
    full = (column_type or "").lower()
    if base == "tinyint":
        # tinyint(1) is the boolean convention; tinyint with any other display
        # width or unsigned is a small integer.
        if "tinyint(1)" in full:
            return "boolean"
        return "smallint"
    return TYPE_MAP.get(base, base)


# ---------- arg parsing ----------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--host", default=None,
                   help="MySQL host (default: $MYSQL_HOST)")
    p.add_argument("--port", type=int, default=None,
                   help="MySQL port (default: $MYSQL_PORT or 3306)")
    p.add_argument("--user", default=None,
                   help="MySQL user (default: $MYSQL_USER)")
    p.add_argument("--password", default=None,
                   help="MySQL password (default: $MYSQL_PASSWORD)")
    p.add_argument("--database", default=None,
                   help="MySQL database (default: $MYSQL_DATABASE)")
    p.add_argument("--out", default=None,
                   help="Output YAML path (default: schema/<dbname>.yml)")
    p.add_argument("--dry-run", action="store_true",
                   help="Print summary; don't write file")
    return p.parse_args(argv)


# ---------- credentials ----------


def _resolve_creds(args: argparse.Namespace) -> dict[str, str | int]:
    load_env()
    host = args.host or os.environ.get("MYSQL_HOST", "").strip()
    port = args.port or int(os.environ.get("MYSQL_PORT", "3306"))
    user = args.user or os.environ.get("MYSQL_USER", "").strip()
    password = args.password or os.environ.get("MYSQL_PASSWORD", "")
    database = args.database or os.environ.get("MYSQL_DATABASE", "").strip()

    missing = [k for k, v in {
        "MYSQL_HOST": host, "MYSQL_USER": user, "MYSQL_DATABASE": database,
    }.items() if not v]
    if missing:
        sys.stderr.write(
            "ERROR: missing credentials. Set in .env or .claude/.env:\n"
        )
        for k in missing:
            sys.stderr.write(f"  {k}=...\n")
        sys.stderr.write(
            "\nAll variables (set what you need):\n"
            "  MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE\n"
        )
        sys.exit(2)
    return {
        "host": host, "port": port, "user": user,
        "password": password, "database": database,
    }


# ---------- introspection ----------


def introspect(creds: dict) -> tuple[str, list[dict]]:
    try:
        import pymysql
    except ImportError:
        sys.stderr.write(
            "ERROR: PyMySQL not installed.\n"
            "  pip install -r tools/requirements-mysql.txt\n"
        )
        sys.exit(2)

    conn = pymysql.connect(
        host=creds["host"],
        port=int(creds["port"]),
        user=creds["user"],
        password=creds["password"],
        database=creds["database"],
        charset="utf8mb4",
        connect_timeout=5,
        read_default_file=None,
        autocommit=True,
    )
    try:
        with conn.cursor() as cur:
            # Columns + their types.
            cur.execute(
                """
                SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, COLUMN_TYPE,
                       ORDINAL_POSITION
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = %s
                ORDER BY TABLE_NAME, ORDINAL_POSITION
                """,
                (creds["database"],),
            )
            cols_raw = cur.fetchall()

            # Primary keys.
            cur.execute(
                """
                SELECT TABLE_NAME, COLUMN_NAME
                FROM information_schema.KEY_COLUMN_USAGE
                WHERE TABLE_SCHEMA = %s
                  AND CONSTRAINT_NAME = 'PRIMARY'
                """,
                (creds["database"],),
            )
            pk_set = {(r[0], r[1]) for r in cur.fetchall()}

            # Foreign keys.
            cur.execute(
                """
                SELECT TABLE_NAME, COLUMN_NAME,
                       REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
                FROM information_schema.KEY_COLUMN_USAGE
                WHERE TABLE_SCHEMA = %s
                  AND REFERENCED_TABLE_NAME IS NOT NULL
                """,
                (creds["database"],),
            )
            fk_map = {
                (r[0], r[1]): f"{r[2]}.{r[3]}"
                for r in cur.fetchall()
            }
    finally:
        conn.close()

    # Group by table.
    tables_raw: dict[str, list[dict]] = {}
    for table_name, col_name, data_type, column_type, _ in cols_raw:
        tables_raw.setdefault(table_name, []).append({
            "name": col_name,
            "type": _map_type(data_type, column_type),
            "pk": (table_name, col_name) in pk_set,
            "fk": fk_map.get((table_name, col_name)),
        })

    out: list[dict] = []
    for name, columns in tables_raw.items():
        clean = []
        for c in columns:
            entry: dict = {"name": c["name"], "type": c["type"]}
            if c["pk"]:
                entry["pk"] = True
            if c["fk"]:
                entry["fk"] = c["fk"]
            clean.append(entry)
        out.append({"name": name, "description": "", "columns": clean})
    out.sort(key=lambda t: t["name"])
    _annotate_junction_tables(out)
    return creds["database"], out


def _annotate_junction_tables(tables: list[dict]) -> None:
    """Mirror the same logic Postgres introspection uses — mark M:N tables."""
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
        left, _ = fk_pks[0]["fk"].split(".")
        right, _ = fk_pks[1]["fk"].split(".")
        if left == right:
            continue
        t["description"] = (
            f"Junction table: connects {left} <-> {right} (many-to-many)."
        )
        for owner, other, own_col, other_col_name in (
            (left, right, fk_pks[0]["name"], fk_pks[1]["name"]),
            (right, left, fk_pks[1]["name"], fk_pks[0]["name"]),
        ):
            for target in tables:
                if target["name"] != owner:
                    continue
                rels = target.setdefault("relationships", [])
                rels.append({
                    "to": other,
                    "type": "many-to-many",
                    "via": f"{t['name']} ({own_col} -> {other_col_name})",
                })


# ---------- write YAML ----------


def write_yaml(db_name: str, tables: list[dict], out_path: Path) -> None:
    import yaml as pyyaml  # PyYAML is already in main requirements.

    doc = {
        "database": db_name,
        "description": (
            f"Auto-introspected from MySQL by tools/introspect_mysql.py — "
            f"edit descriptions/synonyms or run `python tools/auto_annotate.py "
            f"--schema {out_path.relative_to(ROOT)}`."
        ),
        "tables": tables,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(pyyaml.safe_dump(doc, sort_keys=False, width=120))


# ---------- main ----------


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    creds = _resolve_creds(args)

    sys.stderr.write(
        f"Connecting to {creds['user']}@{creds['host']}:{creds['port']}"
        f"/{creds['database']} (read-only)...\n"
    )
    db_name, tables = introspect(creds)
    column_count = sum(len(t.get("columns") or []) for t in tables)
    fk_count = sum(
        1 for t in tables for c in (t.get("columns") or []) if c.get("fk")
    )

    sys.stderr.write(
        f"Found: {len(tables)} tables · {column_count} columns · {fk_count} foreign keys\n"
    )

    out_path = Path(args.out) if args.out else (ROOT / "schema" / f"{db_name}.yml")
    if not out_path.is_absolute():
        out_path = ROOT / out_path

    if args.dry_run:
        sys.stderr.write(f"--dry-run: would write {out_path}\n")
        for t in tables[:5]:
            sys.stderr.write(f"  - {t['name']} ({len(t.get('columns') or [])} cols)\n")
        if len(tables) > 5:
            sys.stderr.write(f"  ... and {len(tables) - 5} more\n")
        return 0

    write_yaml(db_name, tables, out_path)
    sys.stderr.write(
        f"\nWrote: {out_path}\n"
        f"Next: python tools/auto_annotate.py --schema {out_path.relative_to(ROOT)} "
        f"--tables all\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
