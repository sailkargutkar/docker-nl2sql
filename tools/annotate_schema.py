"""
Enrich the schema YAML with column descriptions + synonyms via OpenAI.

Output: schema/<name>.proposed.yml — review and merge with apply_annotations.py.

Usage:
  python tools/annotate_schema.py --tables Client,Organization,Employee
  python tools/annotate_schema.py --tables all --dry-run
  python tools/annotate_schema.py --redact --no-samples
  nohup python tools/annotate_schema.py --tables all > tools/.cache/annotate.log 2>&1 &

Pricing (gpt-4o-mini, default): ~$0.005 per 5-table batch, ~$0.07 for 93 tables.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import ruamel.yaml

# Make the parent dir importable when run as `python tools/annotate_schema.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools._common import (  # noqa: E402
    CACHE_DIR,
    CostMeter,
    RedactionMap,
    ROOT,
    acquire_lock,
    cache_get,
    cache_key,
    cache_set,
    get_client,
    rough_token_count,
)
from tools._prompts import (  # noqa: E402
    ANNOTATE_SYSTEM,
    TEMPLATE_VERSION,
    annotate_user_prompt,
    format_table_block,
)
from tools._validate import (  # noqa: E402
    AnnotateResponse,
    cross_check_synonyms,
    validate_annotation,
)


# ---------- arg parsing ----------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--schema", default="schema/tmt_schema.yml",
                   help="Path to schema YAML (default: %(default)s)")
    p.add_argument("--tables", default="all",
                   help="Comma-separated table names, or 'all' (default: %(default)s)")
    p.add_argument("--out", default=None,
                   help="Output path. Default: <schema>.proposed.yml")
    p.add_argument("--model", default="gpt-4o-mini",
                   help="OpenAI model (default: %(default)s)")
    p.add_argument("--batch-size", type=int, default=5,
                   help="Tables per LLM call (default: %(default)s)")
    p.add_argument("--with-samples", dest="with_samples", action="store_true",
                   default=True, help="Pull sample values from DB (default)")
    p.add_argument("--no-samples", dest="with_samples", action="store_false",
                   help="Don't query DB; annotate from names alone")
    p.add_argument("--sample-size", type=int, default=5,
                   help="Distinct values per column (default: %(default)s)")
    p.add_argument("--max-value-len", type=int, default=30,
                   help="Truncate sample chars (default: %(default)s)")
    p.add_argument("--redact", action="store_true",
                   help="Send sanitized names (T1, C1) to the LLM; map back locally")
    p.add_argument("--max-cost", type=float, default=1.0,
                   help="Hard cost cap in USD (default: %(default)s)")
    p.add_argument("--mode", choices=["merge", "replace"], default="merge",
                   help="merge: keep existing synonyms (default); "
                        "replace: only emit LLM output")
    p.add_argument("--concurrency", type=int, default=3,
                   help="Parallel API calls (default: %(default)s)")
    p.add_argument("--dry-run", action="store_true",
                   help="Print what would be sent + estimated cost; no API calls")
    return p.parse_args(argv)


# ---------- schema I/O ----------

_yaml = ruamel.yaml.YAML(typ="rt")  # round-trip preserves comments/order
_yaml.default_flow_style = False
_yaml.indent(mapping=2, sequence=2, offset=0)
_yaml.width = 120
# Match PyYAML's escaping style — keeps diffs against the original file
# minimal so reviewers see annotation changes, not encoding churn.
_yaml.allow_unicode = False


def _load_schema(path: Path) -> Any:
    if not path.is_file():
        sys.stderr.write(f"ERROR: schema file not found: {path}\n")
        sys.exit(2)
    return _yaml.load(path)


def _save_schema(doc: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        _yaml.dump(doc, f)


def _select_tables(doc: Any, requested: str) -> list[Any]:
    all_tables = list(doc.get("tables") or [])
    if requested.strip().lower() == "all":
        return all_tables
    wanted = {t.strip() for t in requested.split(",") if t.strip()}
    matched = [t for t in all_tables if t.get("name") in wanted]
    missing = wanted - {t.get("name") for t in matched}
    if missing:
        sys.stderr.write(
            f"WARN: tables not found in schema: {', '.join(sorted(missing))}\n"
        )
    return matched


# ---------- DB sample values (optional) ----------


def _sample_db_values(
    table_name: str,
    columns: list[dict],
    sample_size: int,
    max_value_len: int,
) -> dict[str, list[str]]:
    """Return {column: [str, ...]} of distinct samples. Empty on any failure."""
    try:
        from sqlalchemy import create_engine, text
    except ImportError:
        return {}

    # Reuse the runtime config so we hit the same DB the matcher uses.
    try:
        sys.path.insert(0, str(ROOT))
        from app.config import settings  # noqa: WPS433 (intentional)
        url = settings.db_url
    except Exception:  # noqa: BLE001
        return {}

    out: dict[str, list[str]] = {}
    try:
        engine = create_engine(url, connect_args={"connect_timeout": 3})
        with engine.connect() as conn:
            for col in columns:
                col_type = (col.get("type") or "").lower()
                # Only sample text-ish columns; numeric/timestamp/boolean don't help.
                if not any(t in col_type for t in ("string", "text", "varchar", "uuid", "enum")):
                    continue
                if col_type == "uuid":
                    # UUIDs are noise; skip.
                    continue
                # Use double-quoted identifiers for mixed-case PG schemas.
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


# ---------- batching ----------


def _batches(items: list[Any], size: int) -> list[list[Any]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _build_batch_prompt(
    batch: list[Any],
    args: argparse.Namespace,
    redaction: RedactionMap | None,
) -> tuple[str, dict[str, str]]:
    """Return (prompt_block, table_name_real_to_sent)."""
    blocks: list[str] = []
    name_map: dict[str, str] = {}  # name we sent to the LLM → real name
    for table in batch:
        real_name = table["name"]
        cols = list(table.get("columns") or [])

        sent_table_name = real_name
        sent_cols: list[dict] = []
        if redaction is not None:
            sent_table_name = redaction.redact_table(real_name)
            for c in cols:
                sent_cols.append({
                    "name": redaction.redact_column(c["name"]),
                    "type": c.get("type", "unknown"),
                    "pk": c.get("pk", False),
                    "fk": redaction.redact_table(c["fk"].split(".")[0]) + "." +
                          redaction.redact_column(c["fk"].split(".")[1])
                          if c.get("fk") else None,
                })
        else:
            for c in cols:
                sent_cols.append({
                    "name": c["name"],
                    "type": c.get("type", "unknown"),
                    "pk": c.get("pk", False),
                    "fk": c.get("fk"),
                })

        samples: dict[str, list[str]] = {}
        if args.with_samples and redaction is None:
            # Don't sample when redacting — values would leak the very thing
            # we just hid.
            samples = _sample_db_values(
                real_name, sent_cols, args.sample_size, args.max_value_len,
            )

        name_map[sent_table_name] = real_name
        blocks.append(format_table_block(sent_table_name, sent_cols, samples))

    return "\n\n".join(blocks), name_map


# ---------- LLM call ----------


def _call_one_batch(
    batch_idx: int,
    batch: list[Any],
    args: argparse.Namespace,
    redaction: RedactionMap | None,
    meter: CostMeter,
) -> tuple[int, AnnotateResponse, dict[str, str]]:
    block, name_map = _build_batch_prompt(batch, args, redaction)
    user_prompt = annotate_user_prompt(block)
    full_prompt = f"{ANNOTATE_SYSTEM}\n---\n{user_prompt}"

    key = cache_key(TEMPLATE_VERSION, args.model, full_prompt)
    cached = cache_get(key)
    if cached is not None:
        meter.add(cached.get("prompt_tokens", 0), cached.get("completion_tokens", 0))
        validated = validate_annotation(cached["payload"])
        return batch_idx, validated, name_map

    if args.dry_run:
        approx_in = rough_token_count(full_prompt)
        approx_out = sum(len(t.get("columns") or []) for t in batch) * 30
        meter.add(approx_in, approx_out)
        # Stub response so the downstream merge path still runs end-to-end.
        return batch_idx, validate_annotation({
            "tables": [
                {"name": t["name"], "description": "[dry-run]", "columns": []}
                for t in batch
            ]
        }), name_map

    client = get_client()
    resp = client.chat.completions.create(
        model=args.model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": ANNOTATE_SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
    )

    raw = resp.choices[0].message.content or "{}"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        # One stricter retry.
        retry = client.chat.completions.create(
            model=args.model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": ANNOTATE_SYSTEM},
                {"role": "user", "content": user_prompt
                                          + "\n\nReturn ONLY the JSON object, nothing else."},
            ],
        )
        raw = retry.choices[0].message.content or "{}"
        payload = json.loads(raw)
        if retry.usage:
            meter.add(retry.usage.prompt_tokens, retry.usage.completion_tokens)

    if resp.usage:
        meter.add(resp.usage.prompt_tokens, resp.usage.completion_tokens)

    validated = validate_annotation(payload)

    cache_set(key, {
        "payload": payload,
        "prompt_tokens": resp.usage.prompt_tokens if resp.usage else 0,
        "completion_tokens": resp.usage.completion_tokens if resp.usage else 0,
        "model": args.model,
        "ts": int(time.time()),
    })
    return batch_idx, validated, name_map


# ---------- merging ----------


def _apply_to_doc(
    doc: Any,
    annotated: AnnotateResponse,
    name_map: dict[str, str],
    mode: str,
    redaction: RedactionMap | None,
) -> None:
    by_real_name = {t.get("name"): t for t in (doc.get("tables") or [])}

    for t in annotated.tables:
        real_table = name_map.get(t.name, t.name)
        # When redacting, also unmap column names per response.
        if redaction is not None:
            for col in t.columns:
                back = redaction.unredact_column(col.name)
                if back is not None:
                    col.name = back

        target = by_real_name.get(real_table)
        if target is None:
            continue

        # Table-level description.
        if t.description and (mode == "replace" or not (target.get("description") or "").strip()):
            target["description"] = t.description

        # Column-level merge.
        col_map = {c.get("name"): c for c in (target.get("columns") or [])}
        for ann_col in t.columns:
            target_col = col_map.get(ann_col.name)
            if target_col is None:
                continue
            if ann_col.description and (
                mode == "replace" or not (target_col.get("description") or "").strip()
            ):
                target_col["description"] = ann_col.description
            if ann_col.synonyms:
                if mode == "replace":
                    target_col["synonyms"] = list(ann_col.synonyms)
                else:
                    existing = list(target_col.get("synonyms") or [])
                    seen = {s.lower() for s in existing}
                    for syn in ann_col.synonyms:
                        if syn.lower() not in seen:
                            existing.append(syn)
                            seen.add(syn.lower())
                    target_col["synonyms"] = existing


# ---------- main ----------


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    schema_path = Path(args.schema)
    if not schema_path.is_absolute():
        schema_path = ROOT / schema_path

    out_path = Path(args.out) if args.out else schema_path.with_suffix(".proposed.yml")
    if not out_path.is_absolute():
        out_path = ROOT / out_path

    with acquire_lock("annotate_schema"):
        doc = _load_schema(schema_path)
        tables = _select_tables(doc, args.tables)
        if not tables:
            sys.stderr.write("ERROR: no tables matched. Nothing to do.\n")
            return 2

        meter = CostMeter(model=args.model, max_cost_usd=args.max_cost)
        redaction = RedactionMap() if args.redact else None
        batches = _batches(tables, args.batch_size)

        # Pre-call estimate.
        approx_input_tokens = 0
        for batch in batches:
            block, _ = _build_batch_prompt(batch, args, redaction)
            approx_input_tokens += rough_token_count(
                ANNOTATE_SYSTEM + annotate_user_prompt(block)
            )
        approx_output_tokens = sum(
            len(t.get("columns") or []) * 30 for t in tables
        )
        est_cost = meter.estimate(approx_input_tokens, approx_output_tokens)

        sys.stderr.write(
            f"Plan: {len(tables)} tables · {len(batches)} batches · "
            f"~{approx_input_tokens:,} in tokens / ~{approx_output_tokens:,} out · "
            f"est. cost ${est_cost:.4f}\n"
        )
        if est_cost > args.max_cost and not args.dry_run:
            sys.stderr.write(
                f"ERROR: estimate ${est_cost:.4f} > --max-cost ${args.max_cost:.4f}.\n"
                "  Reduce --tables, lower --sample-size, or raise --max-cost.\n"
            )
            return 3
        if args.dry_run:
            sys.stderr.write("--dry-run: not calling the API.\n")

        # Reset name_map collection — we'll rebuild it during real calls so each
        # batch's redaction reuse is consistent with what was actually sent.
        if args.redact:
            redaction = RedactionMap()

        results: list[tuple[int, AnnotateResponse, dict[str, str]]] = []
        with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as ex:
            futures = [
                ex.submit(_call_one_batch, idx, batch, args, redaction, meter)
                for idx, batch in enumerate(batches)
            ]
            for fut in as_completed(futures):
                results.append(fut.result())
                meter.assert_under_budget()

        results.sort(key=lambda r: r[0])

        # Validate cross-column collisions once we have the real names.
        real_cols_by_table: dict[str, set[str]] = {
            t["name"]: {c["name"] for c in (t.get("columns") or [])}
            for t in tables
        }
        all_warnings: list[str] = []
        for _, validated, name_map in results:
            # Translate validated.tables[*].name back to real names for the check.
            for at in validated.tables:
                at.name = name_map.get(at.name, at.name)
            warnings = cross_check_synonyms(validated, real_cols_by_table)
            all_warnings.extend(warnings)
            _apply_to_doc(doc, validated, name_map, args.mode, redaction)

        for w in all_warnings:
            sys.stderr.write(f"WARN: {w}\n")

        _save_schema(doc, out_path)
        sys.stderr.write(
            f"\nWrote: {out_path}\n"
            f"{meter.report()}\n"
            f"Next: review with `diff -u {schema_path} {out_path}`, "
            f"then merge via `python tools/apply_annotations.py --apply`\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
