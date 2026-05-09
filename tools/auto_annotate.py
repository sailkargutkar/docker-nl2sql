"""
Deterministic schema annotator — no LLM, no API calls, no costs.

Generates `description:` and `synonyms:` for each column using only:
  1. camelCase / snake_case splitting
  2. NLTK WordNet synonyms (already a runtime dependency)
  3. A built-in lookup table of common business acronyms (tools/_acronyms.py)
  4. Optional: distinct values sampled from the live database

Output goes to schema/<name>.proposed.yml — same convention as the LLM
annotator, so apply_annotations.py works identically.

Usage:
  python tools/auto_annotate.py --tables Client,Organization,Driver,Tour,Invoice
  python tools/auto_annotate.py --tables all
  python tools/auto_annotate.py --tables all --no-samples
  python tools/auto_annotate.py --tables Client --verbose
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Make the parent dir importable when run as `python tools/...`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ruamel.yaml  # noqa: E402

from tools._acronyms import acronym_lookup, word_lookup  # noqa: E402
from tools._common import ROOT, acquire_lock  # noqa: E402


# ---------- arg parsing ----------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--schema", default="schema/tmt_schema.yml")
    p.add_argument("--tables", default="all",
                   help="Comma-separated names, or 'all' (default)")
    p.add_argument("--out", default=None,
                   help="Default: <schema>.proposed.yml")
    p.add_argument("--with-samples", dest="with_samples", action="store_true",
                   default=True,
                   help="Sample DB values to refine descriptions (default)")
    p.add_argument("--no-samples", dest="with_samples", action="store_false")
    p.add_argument("--sample-size", type=int, default=5)
    p.add_argument("--max-value-len", type=int, default=30)
    p.add_argument("--max-synonyms", type=int, default=5,
                   help="Max synonyms per column (default: %(default)s)")
    p.add_argument("--mode", choices=["merge", "replace"], default="merge",
                   help="merge: keep existing; replace: overwrite")
    p.add_argument("--verbose", action="store_true",
                   help="Print synonym derivation for each column")
    return p.parse_args(argv)


# ---------- YAML I/O ----------

_yaml = ruamel.yaml.YAML(typ="rt")
_yaml.default_flow_style = False
_yaml.indent(mapping=2, sequence=2, offset=0)
_yaml.width = 120
_yaml.allow_unicode = False


def _load_schema(path: Path):
    if not path.is_file():
        sys.stderr.write(f"ERROR: schema file not found: {path}\n")
        sys.exit(2)
    return _yaml.load(path)


def _save_schema(doc, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        _yaml.dump(doc, f)


# ---------- Identifier splitting ----------

_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|[_\-\s]+")


def split_identifier(name: str) -> list[str]:
    if not name:
        return []
    parts = [p for p in _CAMEL_RE.split(name) if p]
    return [p.lower() for p in parts]


def humanize(name: str) -> str:
    """`paymentTerm` → 'payment term', `GSTIN` → 'gstin'."""
    parts = split_identifier(name)
    if not parts:
        return name.lower()
    return " ".join(parts)


# ---------- WordNet synonym expansion ----------

_STOPWORDS = {
    "id", "the", "a", "an", "of", "to", "in", "on", "at", "by", "for",
    "and", "or", "is", "are", "was", "were", "be", "been", "being",
    "this", "that", "these", "those", "it", "its", "with", "from",
    "up", "down", "out", "off",
}

_BORING_SYNONYMS = {
    # WordNet emits these for nearly every word — useless noise.
    "thing", "object", "entity", "matter", "substance",
    # Geographic / proper-noun artifacts.
    "river", "city", "county", "mountain", "ocean", "lake", "island",
    # Archaic / unhelpful single words.
    "gens", "netmail", "ostiary",
}


def _wordnet_synonyms(word: str, max_n: int = 3) -> list[str]:
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

    # Only the FIRST sense — multi-sense expansion is where noise comes in.
    for syn in synsets[:1]:
        for lemma in syn.lemmas():
            raw = lemma.name()
            # Proper-noun heuristic: any uppercase character in the raw lemma
            # signals a proper noun (Mobile River, New York, etc.) — skip.
            if any(c.isupper() for c in raw):
                continue
            cand = raw.replace("_", " ").lower()
            if cand == word.lower():
                continue
            if cand in seen or cand in _STOPWORDS:
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


# ---------- Description generators (rule-based) ----------

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


def make_description(
    column_name: str,
    column_type: str,
    is_pk: bool,
    fk: str | None,
    sample_values: list[str] | None = None,
) -> str:
    name_human = humanize(column_name)
    parts = split_identifier(column_name)

    # PK / FK take priority
    if is_pk:
        return f"Primary key — {name_human}."
    if fk:
        ref_table, ref_col = fk.split(".", 1) if "." in fk else (fk, "id")
        return f"Foreign key to {ref_table}.{ref_col}."

    # If column matches a known acronym, prefer that.
    if len(parts) == 1 and acronym_lookup(parts[0]):
        canonical = acronym_lookup(parts[0])[0]
        return f"{column_name} — {canonical}."

    base_type_desc = _TYPE_DESCRIPTIONS.get(column_type.lower(), "")

    # Sample-driven refinement.
    if sample_values:
        # Detect enum-like columns (small set of short values).
        unique_short = [v for v in sample_values if len(v) <= 30]
        if 0 < len(unique_short) <= 8 and column_type.lower() in ("string", "text", "varchar"):
            preview = ", ".join(repr(v) for v in unique_short[:3])
            return f"{name_human} (e.g. {preview})."

    if base_type_desc:
        return f"{name_human} — {base_type_desc.lower()}."
    return f"{name_human}."


# ---------- Synonym generation ----------


def generate_synonyms(
    column_name: str,
    table_name: str,
    other_columns_on_table: set[str],
    max_n: int,
) -> tuple[list[str], list[str]]:
    """Return (final_synonyms, derivation_trace).

    `derivation_trace` lists the rule that fired for each synonym so we
    can show the user where each entry came from.
    """
    parts = split_identifier(column_name)
    name_lc = column_name.lower()
    other_cols_lc = {c.lower() for c in other_columns_on_table if c != column_name}

    out: list[str] = []
    trace: list[str] = []

    def _add(syn: str, source: str) -> None:
        s = syn.strip().lower()
        if not s or len(s) < 2 or s in _STOPWORDS:
            return
        if s == name_lc:
            return
        if s in other_cols_lc:
            return
        if s in {x.lower() for x in out}:
            return
        out.append(s)
        trace.append(f"{s!r} ← {source}")

    # 1. Whole-name acronym lookup ("GSTIN" → ["gst number", ...]).
    if len(parts) == 1:
        for syn in acronym_lookup(parts[0]):
            _add(syn, f"acronym table[{parts[0]}]")
            if len(out) >= max_n:
                return out[:max_n], trace

    # 2. Humanized whole name ("paymentTerm" → "payment term").
    if len(parts) > 1:
        _add(humanize(column_name), "humanized full name")
        if len(out) >= max_n:
            return out[:max_n], trace

    # 3. Per-word synonyms (WORD_SYNONYMS lookup).
    for part in parts:
        for syn in word_lookup(part):
            _add(syn, f"word table[{part}]")
            if len(out) >= max_n:
                return out[:max_n], trace

    # 4. Per-word WordNet expansion — applied carefully (most sensible
    #    when the column is a single common English word; less so for
    #    multi-part business identifiers).
    if len(parts) == 1 and len(parts[0]) >= 4:
        for syn in _wordnet_synonyms(parts[0], max_n=3):
            _add(syn, f"wordnet[{parts[0]}]")
            if len(out) >= max_n:
                return out[:max_n], trace

    # 5. Compose two-word synonyms by combining root noun + common
    #    qualifiers. e.g. "name" on table "Client" → "client name".
    if len(parts) == 1 and parts[0] in {
        "name", "id", "code", "no", "type", "status", "date",
        "amount", "price", "address", "phone", "email",
    }:
        table_human = humanize(table_name)
        composite = f"{table_human} {parts[0]}"
        if composite != name_lc:
            _add(composite, f"composite[table+{parts[0]}]")

    return out[:max_n], trace


# ---------- Sample-value sampler (optional) ----------


def _sample_db_values(
    table_name: str,
    columns: list[dict],
    sample_size: int,
    max_value_len: int,
) -> dict[str, list[str]]:
    try:
        from sqlalchemy import create_engine, text
    except ImportError:
        return {}
    try:
        sys.path.insert(0, str(ROOT))
        from app.config import settings  # noqa: WPS433
        url = settings.db_url
    except Exception:  # noqa: BLE001
        return {}

    out: dict[str, list[str]] = {}
    try:
        engine = create_engine(url, connect_args={"connect_timeout": 3})
        with engine.connect() as conn:
            for col in columns:
                col_type = (col.get("type") or "").lower()
                if not any(t in col_type for t in ("string", "text", "varchar", "enum")):
                    continue
                sql = (
                    f'SELECT DISTINCT "{col["name"]}" FROM "{table_name}" '
                    f'WHERE "{col["name"]}" IS NOT NULL '
                    f'LIMIT {int(sample_size)}'
                )
                try:
                    rows = conn.execute(text(sql)).fetchall()
                except Exception:  # noqa: BLE001
                    continue
                vals = []
                for r in rows:
                    v = str(r[0])
                    if len(v) > max_value_len:
                        v = v[: max_value_len - 1] + "…"
                    vals.append(v)
                if vals:
                    out[col["name"]] = vals
    except Exception:  # noqa: BLE001
        return {}
    return out


# ---------- Apply to schema doc ----------


def annotate_table(
    table: dict,
    args: argparse.Namespace,
    samples_for_table: dict[str, list[str]] | None,
) -> list[str]:
    """Annotate a single table in place. Return verbose-trace lines."""
    trace_lines: list[str] = []
    columns = list(table.get("columns") or [])
    other_cols = {c.get("name", "") for c in columns}

    # Table-level description.
    if args.mode == "replace" or not (table.get("description") or "").strip():
        table["description"] = humanize(table["name"]).capitalize() + "."

    for col in columns:
        col_name = col.get("name", "")
        col_type = col.get("type", "unknown")
        is_pk = bool(col.get("pk"))
        fk = col.get("fk")
        samples = (samples_for_table or {}).get(col_name)

        # Description
        if args.mode == "replace" or not (col.get("description") or "").strip():
            col["description"] = make_description(col_name, col_type, is_pk, fk, samples)

        # Synonyms — skip ID/timestamp/bookkeeping columns.
        if is_pk or fk:
            continue
        if col_type.lower() in {"timestamp", "timestamptz", "date"}:
            continue
        if col_name.lower() in {"createdat", "updatedat", "createdby", "updatedby",
                                "revision", "id"}:
            continue

        new_syns, trace = generate_synonyms(
            col_name, table["name"], other_cols, args.max_synonyms
        )

        if args.mode == "replace":
            col["synonyms"] = new_syns
        else:
            existing = list(col.get("synonyms") or [])
            seen = {s.lower() for s in existing}
            for s in new_syns:
                if s.lower() not in seen:
                    existing.append(s)
                    seen.add(s.lower())
            col["synonyms"] = existing[: args.max_synonyms * 2]  # generous upper

        if args.verbose and trace:
            trace_lines.append(f"  {table['name']}.{col_name}:")
            for line in trace:
                trace_lines.append(f"    {line}")

    return trace_lines


# ---------- main ----------


def _select_tables(doc, requested: str) -> list[dict]:
    all_tables = list(doc.get("tables") or [])
    if requested.strip().lower() == "all":
        return all_tables
    wanted = {t.strip() for t in requested.split(",") if t.strip()}
    matched = [t for t in all_tables if t.get("name") in wanted]
    missing = wanted - {t.get("name") for t in matched}
    if missing:
        sys.stderr.write(
            f"WARN: tables not found: {', '.join(sorted(missing))}\n"
        )
    return matched


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    schema_path = Path(args.schema)
    if not schema_path.is_absolute():
        schema_path = ROOT / schema_path
    out_path = Path(args.out) if args.out else schema_path.with_suffix(".proposed.yml")
    if not out_path.is_absolute():
        out_path = ROOT / out_path

    with acquire_lock("auto_annotate"):
        doc = _load_schema(schema_path)
        tables = _select_tables(doc, args.tables)
        if not tables:
            sys.stderr.write("ERROR: no tables matched. Nothing to do.\n")
            return 2

        sys.stderr.write(
            f"Annotating {len(tables)} tables (deterministic, no LLM, $0)\n"
        )

        all_traces: list[str] = []
        cols_total = 0
        synonyms_total = 0
        for t in tables:
            samples = None
            if args.with_samples:
                samples = _sample_db_values(
                    t["name"],
                    list(t.get("columns") or []),
                    args.sample_size,
                    args.max_value_len,
                )
            traces = annotate_table(t, args, samples)
            all_traces.extend(traces)
            cols = list(t.get("columns") or [])
            cols_total += len(cols)
            for c in cols:
                synonyms_total += len(c.get("synonyms") or [])

        _save_schema(doc, out_path)

        if args.verbose and all_traces:
            sys.stderr.write("\nDerivation trace:\n")
            for line in all_traces:
                sys.stderr.write(line + "\n")

        sys.stderr.write(
            f"\nWrote: {out_path}\n"
            f"Tables: {len(tables)} · columns: {cols_total} · "
            f"synonyms generated: {synonyms_total}\n"
            f"Cost: $0.00 (deterministic, no API calls)\n"
            f"Next: review with `diff -u {schema_path} {out_path}`, "
            f"then merge via `python tools/apply_annotations.py --apply`\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
