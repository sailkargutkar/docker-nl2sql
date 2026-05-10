#!/usr/bin/env python3
"""
LLM vs local nl2sql evaluation harness.

For each prompt we:
  1. Ask the LLM (gpt-4o-mini by default) for SQL — schema is in the
     system message and is auto-cached by OpenAI's prompt cache.
  2. Ask the local generator (app.generator.generate_sql) for SQL.
  3. Execute both against MariaDB (bombayhouse) read-only.
  4. Record rowcount + a stable signature so we can call (a) strong match,
     (b) weak match (count only), or (c) divergence.

Outputs a CSV per-prompt and a JSON summary in tools/.cache/eval/<run-id>/.

Run:
  python -m tools.eval_llm_vs_local --db bombayhouse --n 2000 \\
         --model gpt-4o-mini --max-cost 2.00
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import sys
import time
import traceback
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db_registry, executor  # noqa: E402
from app.generator import generate_sql  # noqa: E402
from app.schema_dsl import Schema, load_schema  # noqa: E402
from tools._common import (  # noqa: E402
    CostMeter,
    PRICING,
    cache_get,
    cache_key,
    cache_set,
    get_client,
    rough_token_count,
)


# ---------- schema rendering ----------


def _render_schema_for_llm(schema: Schema) -> str:
    """Compact one-line-per-table view. Fits ~6K tokens for 77 tables."""
    lines = [f"Database: {schema.database}  (dialect: mysql)"]
    for t in schema.tables:
        cols = []
        for c in t.columns:
            tag = c.type
            if c.pk:
                tag += " PK"
            if c.fk:
                tag += f" FK→{c.fk}"
            cols.append(f"{c.name} {tag}")
        lines.append(f"{t.name}({', '.join(cols)})")
    return "\n".join(lines)


def _load_schema(path: Path) -> Schema:
    return load_schema(path)


# ---------- prompt corpus ----------


def _sample_string_values(
    url: str, schema: Schema, per_table: int = 8, max_tables: int = 25,
    timeout_ms: int = 5000,
) -> dict[str, dict[str, list[str]]]:
    """Pull a few real string values per (table, name-like column) so prompts
    return non-empty results. Best-effort; failures are silently skipped.
    Returns {table: {col: [values...]}}.
    """
    out: dict[str, dict[str, list[str]]] = {}
    seen = 0
    for t in schema.tables:
        if seen >= max_tables:
            break
        col_picks = [
            c for c in t.columns
            if c.type == "string" and c.name.lower() in {
                "name", "title", "description", "code", "label", "type",
            }
        ]
        if not col_picks:
            continue
        out[t.name] = {}
        for c in col_picks[:2]:
            try:
                sql = (
                    f"SELECT DISTINCT `{c.name}` FROM `{t.name}` "
                    f"WHERE `{c.name}` IS NOT NULL "
                    f"AND `{c.name}` <> '' "
                    f"LIMIT {per_table}"
                )
                res = executor.execute(sql, url, timeout_ms, per_table)
                vals = [str(r[0]) for r in res.rows if r[0]]
                # Skip very long values — they make awkward NL prompts.
                vals = [v for v in vals if len(v) <= 30]
                if vals:
                    out[t.name][c.name] = vals
            except Exception:  # noqa: BLE001
                continue
        if not out[t.name]:
            del out[t.name]
        else:
            seen += 1
    return out


def _build_prompts(schema: Schema, samples: dict, target: int) -> list[str]:
    """Emit `target` distinct prompts derived from the schema.

    Spread roughly evenly across:
      - simple list/count/limit
      - string filters with real values
      - numeric filters
      - top-K with order
      - aggregates (count/avg/max/min/sum)
      - joins ("X with their Y" via FK)
      - boolean predicates
      - negations / IS NULL
      - typo'd column names (stress test)
    """
    rng = random.Random(42)
    prompts: list[str] = []

    table_names = [t.name for t in schema.tables]
    table_by_name = {t.name: t for t in schema.tables}

    def _pretty(name: str) -> str:
        return name.replace("_", " ").lower()

    # Bucket 1: simple ones — every table gets a few base prompts.
    for t in schema.tables:
        nm = _pretty(t.name)
        prompts += [
            f"list all {nm}",
            f"show {nm}",
            f"count {nm}",
            f"how many {nm} are there",
            f"first 10 {nm}",
            f"top 5 {nm}",
        ]

    # Bucket 2: string filters using sampled real values.
    for tbl, cols in samples.items():
        nm = _pretty(tbl)
        for col, vals in cols.items():
            for v in vals[:4]:
                prompts.append(f"{nm} with {col} {v}")
                prompts.append(f"{nm} where {col} is {v}")
                prompts.append(f"find {nm} named {v}")
                prompts.append(f"{nm} with {col} '{v}'")  # quoted = exact match

    # Bucket 3: numeric filters / aggregates.
    for t in schema.tables:
        nm = _pretty(t.name)
        numeric_cols = [
            c for c in t.columns if c.type in {"integer", "bigint", "float", "decimal"}
            and not c.pk and not c.fk
        ]
        for c in numeric_cols[:2]:
            prompts.append(f"{nm} where {c.name} > 0")
            prompts.append(f"average {c.name} in {nm}")
            prompts.append(f"max {c.name} in {nm}")
            prompts.append(f"sum of {c.name} in {nm}")
            prompts.append(f"top 10 {nm} by {c.name}")

    # Bucket 4: joins via FK.
    for t in schema.tables:
        for c in t.columns:
            if c.fk:
                other = c.fk.split(".")[0]
                if other in table_by_name:
                    nm_t = _pretty(t.name)
                    nm_o = _pretty(other)
                    prompts.append(f"{nm_t} with their {nm_o}")
                    prompts.append(f"list {nm_t} along with {nm_o}")
                    prompts.append(f"{nm_t} joined with {nm_o}")

    # Bucket 5: boolean predicates — real "is*" / "has*" cols.
    for t in schema.tables:
        nm = _pretty(t.name)
        bool_cols = [c for c in t.columns if c.type == "boolean"]
        for c in bool_cols[:2]:
            stem = c.name.lower()
            for prefix in ("is", "has", "iscom"):
                if stem.startswith(prefix):
                    qualifier = stem[len(prefix):] or stem
                    prompts.append(f"{qualifier} {nm}")
                    prompts.append(f"not {qualifier} {nm}")
                    break
            else:
                prompts.append(f"{nm} where {c.name} is true")
                prompts.append(f"{nm} where {c.name} is false")

    # Bucket 6: negations / IS NULL probes.
    for t in schema.tables[:30]:
        nm = _pretty(t.name)
        for c in t.columns:
            if not c.pk and c.type == "string":
                prompts.append(f"{nm} without {c.name}")
                prompts.append(f"{nm} where {c.name} is null")
                break

    # Bucket 7: trickier phrasings & paraphrases.
    for tbl, cols in samples.items():
        nm = _pretty(tbl)
        for col, vals in cols.items():
            for v in vals[:2]:
                prompts.append(f"give me {nm} called {v}")
                prompts.append(f"can you list {nm} for {col} = {v}")

    # Bucket 8: stress — typos / abbreviations.
    rng.shuffle(table_names)
    for tn in table_names[:40]:
        nm = _pretty(tn)
        if len(nm) > 4:
            typo = nm[:-1]  # drop last char
            prompts.append(f"list {typo}")
            prompts.append(f"count {typo}")

    # De-dup, shuffle deterministically, trim to target.
    seen: set[str] = set()
    unique: list[str] = []
    for p in prompts:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    rng.shuffle(unique)
    return unique[:target]


# ---------- LLM call ----------


SYSTEM_TMPL = """\
You are an expert MariaDB SQL generator. The user will ask questions in
plain English about the schema below. Output ONE valid MariaDB SELECT
statement that answers the question, using ONLY the tables and columns
from the schema. No explanations.

Constraints:
  - Read-only SELECT only. No DDL, no DML.
  - Always include LIMIT (default 100) unless the user asks for an aggregate.
  - For string filters where the user did NOT quote the literal, prefer
    LIKE with wildcards (case-insensitive) so partial matches work.
  - For string filters where the user DID quote the literal (single
    quotes in the question), use exact equality.
  - Use backticks for table/column names.

Respond as a JSON object with a single field: {"sql": "<the SELECT>"}.

Schema:
%s
"""


def _llm_sql(
    client, model: str, system_prompt: str, question: str,
    meter: CostMeter, cache_version: int = 1,
) -> tuple[str, str]:
    """Return (sql, error_or_empty). Cached by (template_version, model, q+schema-hash)."""
    schema_hash = hashlib.sha256(system_prompt.encode()).hexdigest()[:12]
    ck = cache_key(cache_version, model, f"{schema_hash}|{question}")
    hit = cache_get(ck)
    if hit is not None:
        return hit.get("sql", ""), hit.get("error", "")

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": question},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=400,
        )
    except Exception as e:  # noqa: BLE001
        return "", f"llm_call_error: {e}"

    usage = resp.usage
    if usage is not None:
        meter.add(usage.prompt_tokens, usage.completion_tokens)
        meter.assert_under_budget()

    content = resp.choices[0].message.content or ""
    try:
        parsed = json.loads(content)
        sql = (parsed.get("sql") or "").strip().rstrip(";")
    except Exception:  # noqa: BLE001
        sql = ""

    cache_set(ck, {"sql": sql, "error": "" if sql else "empty_response"})
    return sql, "" if sql else "empty_response"


# ---------- comparison ----------


@dataclass
class RunRow:
    id: int
    prompt: str
    llm_sql: str
    llm_error: str
    llm_rows: int
    llm_sig: str
    local_sql: str
    local_error: str
    local_rows: int
    local_sig: str
    verdict: str  # match / count_match / diverge / llm_only / local_only / both_fail


def _result_sig(columns: list[str], rows: list[list]) -> str:
    body = "|".join(columns) + "\n"
    norm = sorted("".join(str(v) for v in r) for r in rows[:100])
    body += "\n".join(norm)
    return hashlib.sha256(body.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _safe_execute(sql: str, url: str, timeout_ms: int, max_rows: int) -> tuple[int, str, str]:
    if not sql.strip():
        return 0, "empty_sql", ""
    try:
        res = executor.execute(sql, url, timeout_ms, max_rows)
        return res.row_count, "", _result_sig(res.columns, res.rows)
    except Exception as e:  # noqa: BLE001
        msg = str(e).splitlines()[0][:200]
        return 0, msg, ""


def _verdict(llm_err: str, llm_sig: str, local_err: str, local_sig: str,
             llm_rows: int, local_rows: int) -> str:
    llm_ok = not llm_err
    local_ok = not local_err
    if not llm_ok and not local_ok:
        return "both_fail"
    if llm_ok and not local_ok:
        return "llm_only"
    if not llm_ok and local_ok:
        return "local_only"
    # both ok
    if llm_sig == local_sig:
        return "match"
    if llm_rows == local_rows:
        return "count_match"
    return "diverge"


# ---------- main ----------


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="bombayhouse",
                    help="Registry name of the active DB (default bombayhouse).")
    ap.add_argument("--registry", default="/tmp/nl2sql_live.db")
    ap.add_argument("--intent-model", default="/tmp/nl2sql_live.joblib")
    ap.add_argument("--n", type=int, default=2000, help="Target prompt count.")
    ap.add_argument("--model", default="gpt-4o-mini")
    ap.add_argument("--max-cost", type=float, default=2.0,
                    help="Hard cap in USD; harness aborts if exceeded.")
    ap.add_argument("--smoke", type=int, default=0,
                    help="If >0, run only that many prompts (smoke test).")
    ap.add_argument("--timeout-ms", type=int, default=8000)
    ap.add_argument("--max-rows", type=int, default=200)
    ap.add_argument("--out-dir", default="tools/.cache/eval")
    args = ap.parse_args()

    if args.model not in PRICING:
        sys.stderr.write(
            f"WARN: unknown model '{args.model}' — pricing fallback will be used.\n"
        )

    # 1) DB entry + schema
    entry = db_registry.get_by_name(args.registry, args.db)
    if entry is None:
        sys.stderr.write(f"DB '{args.db}' not in registry {args.registry}\n")
        sys.exit(1)
    schema_path = ROOT / entry.schema_file
    if not schema_path.is_file():
        # registry stores relative paths from /data; fall back to schema/<name>.yml
        schema_path = ROOT / "schema" / f"{entry.name}.yml"
    schema = _load_schema(schema_path)
    url = entry.url()

    print(f"DB: {entry.name} dialect={entry.dialect} url=mysql://***@{entry.host}:{entry.port}/{entry.dbname}")
    print(f"Schema: {schema_path} ({len(schema.tables)} tables)")

    # 2) Sample real values + build prompts
    print("Sampling real string values for realistic prompts...")
    samples = _sample_string_values(url, schema)
    print(f"  sampled values from {len(samples)} tables")

    target = args.smoke if args.smoke > 0 else args.n
    prompts = _build_prompts(schema, samples, target=target)
    print(f"Generated {len(prompts)} unique prompts (target {target})")
    if len(prompts) < target:
        print(f"  (corpus exhausted; running with {len(prompts)})")

    # 3) Build LLM context
    schema_text = _render_schema_for_llm(schema)
    system_prompt = SYSTEM_TMPL % schema_text
    sys_tokens = rough_token_count(system_prompt)
    print(f"LLM system prompt ≈ {sys_tokens} tokens (cached after first call)")

    client = get_client()
    meter = CostMeter(model=args.model, max_cost_usd=args.max_cost)

    # 4) Output dir
    run_id = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    out_dir = ROOT / args.out_dir / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "prompts.csv"
    summary_path = out_dir / "summary.json"
    failures_path = out_dir / "failures.csv"
    print(f"Output: {out_dir}")

    # 5) Loop
    rows: list[RunRow] = []
    verdicts: dict[str, int] = {
        "match": 0, "count_match": 0, "diverge": 0,
        "llm_only": 0, "local_only": 0, "both_fail": 0,
    }
    started = time.monotonic()
    progress_every = max(1, len(prompts) // 50)

    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "id", "prompt", "verdict",
            "llm_rows", "local_rows",
            "llm_error", "local_error",
            "llm_sql", "local_sql",
            "llm_sig", "local_sig",
        ])

        for i, q in enumerate(prompts):
            try:
                # LLM
                llm_sql, llm_err = _llm_sql(client, args.model, system_prompt, q, meter)
                llm_rows, llm_run_err, llm_sig = (0, "", "")
                if llm_sql and not llm_err:
                    llm_rows, llm_run_err, llm_sig = _safe_execute(
                        llm_sql, url, args.timeout_ms, args.max_rows
                    )
                final_llm_err = llm_err or llm_run_err

                # local
                local_sql = ""
                local_err = ""
                try:
                    g = generate_sql(
                        q, schema, args.max_rows, args.intent_model, dialect="mysql",
                    )
                    local_sql = g.sql
                    if not local_sql:
                        local_err = (g.explanation or "no_sql")[:200]
                except Exception as e:  # noqa: BLE001
                    local_err = f"local_gen_error: {e}"

                local_rows, local_run_err, local_sig = (0, "", "")
                if local_sql and not local_err:
                    local_rows, local_run_err, local_sig = _safe_execute(
                        local_sql, url, args.timeout_ms, args.max_rows
                    )
                final_local_err = local_err or local_run_err

                v = _verdict(
                    final_llm_err, llm_sig, final_local_err, local_sig,
                    llm_rows, local_rows,
                )
                verdicts[v] += 1

                row = RunRow(
                    id=i, prompt=q,
                    llm_sql=llm_sql, llm_error=final_llm_err,
                    llm_rows=llm_rows, llm_sig=llm_sig,
                    local_sql=local_sql, local_error=final_local_err,
                    local_rows=local_rows, local_sig=local_sig,
                    verdict=v,
                )
                rows.append(row)
                w.writerow([
                    row.id, row.prompt, row.verdict,
                    row.llm_rows, row.local_rows,
                    row.llm_error, row.local_error,
                    row.llm_sql, row.local_sql,
                    row.llm_sig, row.local_sig,
                ])
            except SystemExit:
                raise
            except Exception as e:  # noqa: BLE001
                sys.stderr.write(f"[{i}] harness error: {e}\n{traceback.format_exc()}\n")

            if (i + 1) % progress_every == 0 or (i + 1) == len(prompts):
                elapsed = time.monotonic() - started
                rate = (i + 1) / elapsed if elapsed > 0 else 0
                eta = (len(prompts) - (i + 1)) / rate if rate > 0 else 0
                print(
                    f"  [{i+1}/{len(prompts)}] "
                    f"match={verdicts['match']} cnt={verdicts['count_match']} "
                    f"div={verdicts['diverge']} llm_only={verdicts['llm_only']} "
                    f"local_only={verdicts['local_only']} bf={verdicts['both_fail']} "
                    f"| {meter.report()} | eta {int(eta)}s"
                )

    # 6) Summary
    total = len(rows)
    summary = {
        "run_id": run_id,
        "db": args.db,
        "model": args.model,
        "n_prompts": total,
        "verdicts": verdicts,
        "verdict_pct": {
            k: round(v * 100 / total, 2) if total else 0 for k, v in verdicts.items()
        },
        "cost_usd": round(meter.cost_usd(), 4),
        "tokens_in": meter.prompt_tokens,
        "tokens_out": meter.completion_tokens,
        "wall_seconds": round(time.monotonic() - started, 1),
        "csv": str(csv_path.relative_to(ROOT)),
    }
    summary_path.write_text(json.dumps(summary, indent=2))

    # 7) Top divergences
    diverges = [r for r in rows if r.verdict in ("diverge", "local_only", "llm_only")]
    diverges.sort(key=lambda r: abs(r.llm_rows - r.local_rows), reverse=True)
    with open(failures_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["prompt", "verdict", "llm_rows", "local_rows", "llm_sql", "local_sql"])
        for r in diverges[:50]:
            w.writerow([r.prompt, r.verdict, r.llm_rows, r.local_rows, r.llm_sql, r.local_sql])

    print()
    print("=" * 70)
    print("Run summary")
    print("=" * 70)
    for k, v in verdicts.items():
        pct = summary["verdict_pct"][k]
        print(f"  {k:12s}: {v:5d}  ({pct:.1f}%)")
    print(f"  cost: ${meter.cost_usd():.4f}  tokens in/out: {meter.prompt_tokens:,}/{meter.completion_tokens:,}")
    print(f"  wall: {summary['wall_seconds']}s")
    print(f"  artefacts: {out_dir}")


if __name__ == "__main__":
    main()
