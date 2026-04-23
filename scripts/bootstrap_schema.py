"""CLI wrapper around app.schema_introspect for operators who prefer the shell.

The same logic is exposed over HTTP via POST /api/databases so the UI can
generate a DSL without touching the command line.

Usage:
    python scripts/bootstrap_schema.py --out schema/tmt_schema.yml
    python scripts/bootstrap_schema.py --append schema/tmt_schema.yml
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.schema_introspect import generate_and_write, introspect  # noqa: E402


def _db_url() -> tuple[str, str]:
    load_dotenv()
    user = os.getenv("NL2SQL_DB_USERNAME") or os.getenv("DB_USERNAME", "")
    pwd = os.getenv("NL2SQL_DB_PASSWORD") or os.getenv("DB_PASSWORD", "")
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("NL2SQL_DB_NAME") or os.getenv("DB_NAME", "tmt")
    return f"postgresql+psycopg2://{user}:{pwd}@{host}:{port}/{name}", name


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--out", type=Path, help="Write a fresh DSL file")
    group.add_argument("--append", type=Path, help="Append missing tables to existing DSL file")
    args = parser.parse_args()

    url, db_name = _db_url()
    if args.out:
        n = generate_and_write(url, args.out, db_name)
        print(f"Wrote {n} tables to {args.out}")
    else:
        new_tables = introspect(url)
        existing = yaml.safe_load(args.append.read_text())
        have = {t["name"] for t in existing.get("tables", [])}
        added = [t for t in new_tables if t["name"] not in have]
        existing.setdefault("tables", []).extend(added)
        args.append.write_text(yaml.safe_dump(existing, sort_keys=False, width=120))
        print(f"Appended {len(added)} new tables to {args.append}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
