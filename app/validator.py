"""
SQL validator.

Defense-in-depth goals (the read-only DB user is the last line; this is the first):

  1. Parse with sqlglot. If it doesn't parse, reject.
  2. Accept exactly one top-level SELECT statement. No multi-statement, no DML,
     no DDL, no TCL, no DCL.
  3. Every referenced table must be in the DSL whitelist (case-insensitive).
  4. Reject calls to dangerous server-side functions (pg_read_file, lo_*, etc.).
  5. Inject or cap LIMIT at MAX_ROWS so the LLM can't page a billion rows.

The validator never mutates the user's original SQL when rejecting — on success
it returns the rewritten, limit-capped SQL string for execution.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from .schema_dsl import Schema


DANGEROUS_FUNCTIONS: set[str] = {
    # File / large object access
    "pg_read_file",
    "pg_read_binary_file",
    "pg_ls_dir",
    "lo_import",
    "lo_export",
    "lo_get",
    "lo_put",
    # Catalog dumps that can exfiltrate secrets
    "pg_read_server_files",
    # dblink / FDW
    "dblink",
    "dblink_exec",
    # Misc
    "pg_sleep",
    "pg_terminate_backend",
    "pg_cancel_backend",
}


class ValidationError(ValueError):
    """Raised when a generated SQL is unsafe or invalid."""


@dataclass
class ValidationResult:
    sql: str
    tables_used: list[str]
    limit_applied: int


def _tables_in(node: exp.Expression) -> list[str]:
    return [t.name for t in node.find_all(exp.Table) if t.name]


def _contains_forbidden_statement(tree: exp.Expression) -> str | None:
    forbidden_types = (
        exp.Insert,
        exp.Update,
        exp.Delete,
        exp.Merge,
        exp.Create,
        exp.Drop,
        exp.Alter,
        exp.TruncateTable,
        exp.Command,  # raw commands like VACUUM, ANALYZE, GRANT, etc.
        exp.Transaction,
        exp.Commit,
        exp.Rollback,
    )
    found = tree.find(forbidden_types)
    if found is not None:
        return type(found).__name__.upper()
    return None


def _uses_dangerous_functions(tree: exp.Expression) -> str | None:
    for fn in tree.find_all(exp.Anonymous):
        name = (fn.name or "").lower()
        if name in DANGEROUS_FUNCTIONS:
            return name
    for fn in tree.find_all(exp.Func):
        name = getattr(fn, "name", "") or ""
        if name.lower() in DANGEROUS_FUNCTIONS:
            return name.lower()
    return None


def _is_single_row_aggregate(select: exp.Select) -> bool:
    """True when the SELECT can return at most one row regardless of data.

    That's the case when every projection is a plain aggregate function
    (COUNT/SUM/AVG/MIN/MAX) AND there is no GROUP BY. In that situation a
    LIMIT clause is meaningless noise, so we skip injection.
    """
    if select.args.get("group"):
        return False
    projections = select.expressions or []
    if not projections:
        return False
    for p in projections:
        # Peel off alias wrappers.
        inner = p.unalias() if isinstance(p, exp.Alias) else p
        if not isinstance(inner, (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max)):
            return False
    return True


def _apply_limit(select: exp.Select, max_rows: int) -> int:
    if _is_single_row_aggregate(select):
        existing = select.args.get("limit")
        if existing is not None:
            try:
                return int(existing.expression.this)  # type: ignore[attr-defined]
            except (AttributeError, ValueError, TypeError):
                pass
        return 1

    existing = select.args.get("limit")
    if existing is not None:
        # sqlglot represents LIMIT N as exp.Limit(expression=exp.Literal)
        try:
            val = int(existing.expression.this)  # type: ignore[attr-defined]
            if val <= max_rows:
                return val
        except (AttributeError, ValueError, TypeError):
            pass
    select.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))
    return max_rows


def validate_and_rewrite(
    sql: str,
    schema: Schema,
    max_rows: int,
) -> ValidationResult:
    sql = (sql or "").strip().rstrip(";").strip()
    if not sql:
        raise ValidationError("Empty SQL.")

    try:
        statements = sqlglot.parse(sql, read="postgres")
    except sqlglot.errors.ParseError as e:
        raise ValidationError(f"SQL failed to parse: {e}") from e

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise ValidationError(
            f"Exactly one statement required, got {len(statements)}."
        )
    tree = statements[0]

    bad = _contains_forbidden_statement(tree)
    if bad:
        raise ValidationError(f"Forbidden statement type: {bad}. Only SELECT is allowed.")

    top_select = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    if top_select is None or not isinstance(tree, (exp.Select, exp.Subquery, exp.Union)):
        raise ValidationError("Only SELECT queries are allowed.")

    dangerous = _uses_dangerous_functions(tree)
    if dangerous:
        raise ValidationError(f"Use of dangerous function '{dangerous}' is not allowed.")

    # Whitelist check — every referenced table must exist in the DSL.
    allowed = {t.lower() for t in schema.table_map()}
    referenced = _tables_in(tree)
    unknown = [t for t in referenced if t.lower() not in allowed]
    if unknown:
        raise ValidationError(
            f"Query references unknown table(s): {', '.join(unknown)}. "
            "Only tables declared in the schema DSL are allowed."
        )

    # Apply LIMIT to the outermost SELECT only. We never strip a LIMIT the LLM
    # emits on the user's behalf — that's a request, not noise. We only inject
    # one when the query could otherwise return unbounded rows.
    limit_applied = max_rows
    if isinstance(tree, exp.Select):
        limit_applied = _apply_limit(tree, max_rows)
    else:
        # For UNIONs sqlglot attaches LIMIT to the outer node directly.
        existing = tree.args.get("limit")
        if existing is None:
            tree.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))

    rewritten = tree.sql(dialect="postgres")
    return ValidationResult(
        sql=rewritten,
        tables_used=sorted(set(referenced)),
        limit_applied=limit_applied,
    )
