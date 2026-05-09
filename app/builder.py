"""
Template-driven SQL builder.

Input: an intent ('count' / 'list' / 'sum' / ...), a primary table match, a
list of column matches, and extracted values. Output: a single SELECT.

We build the statement out of sqlglot AST nodes rather than string-concat —
that way the same representation the validator inspects is the thing we emit,
and we can't accidentally forge a shape (UNION of UPDATE, semicolon-stuffed
comment, etc.) the validator has to police.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlglot import exp

from .nlp.implicit import ImplicitValue
from .nlp.matcher import ColumnMatch, resolve_join_path
from .nlp.values import ExtractedValues
from .schema_dsl import Column, Schema, Table


NUMERIC_TYPES = {
    "integer", "bigint", "smallint", "numeric", "float", "double precision",
    "real", "serial", "bigserial",
}
DATE_TYPES = {"date", "timestamp", "timestamptz"}
STRING_TYPES = {"string", "text", "varchar", "uuid"}
BOOLEAN_TYPES = {"boolean"}


@dataclass
class BuildResult:
    sql: str
    explanation: str
    confidence: float
    tables_used: list[str]
    used_columns: list[str] = field(default_factory=list)


class BuildError(ValueError):
    """Raised when we can't confidently build SQL for the intent + matches."""


def _quoted_ident(name: str) -> exp.Expression:
    # Postgres folds bare identifiers to lowercase. The schema uses mixed case,
    # so we always quote — keeps the output portable.
    return exp.to_identifier(name, quoted=True)


def _column_ref(table: str, col: str) -> exp.Column:
    return exp.Column(
        this=_quoted_ident(col),
        table=_quoted_ident(table),
    )


def _literal(v: Any) -> exp.Expression:
    if isinstance(v, bool):
        return exp.Boolean(this=v)
    if isinstance(v, int):
        return exp.Literal.number(v)
    if isinstance(v, float):
        return exp.Literal.number(v)
    if isinstance(v, date):
        return exp.Cast(
            this=exp.Literal.string(v.isoformat()),
            to=exp.DataType.build("date"),
        )
    return exp.Literal.string(str(v))


def _is_numeric(col: Column) -> bool:
    return col.type.lower() in NUMERIC_TYPES


def _is_date(col: Column) -> bool:
    return col.type.lower() in DATE_TYPES


def _is_boolean(col: Column) -> bool:
    return col.type.lower() in BOOLEAN_TYPES


_BOOL_COL_PREFIXES = ("is", "has", "had", "have", "should", "can", "will", "did")


def _bool_col_matches_predicate(col_name: str, pred_lemma: str) -> bool:
    """Does this boolean column name correspond to the predicate token?

    Handles snake_case, camelCase, AND no-separator names (`isservice`,
    `haslicense`) by stripping a known is_/has_-style prefix.
    """
    from .nlp.matcher import _split_identifier
    from .nlp.preprocess import _lemma

    if pred_lemma in _split_identifier(col_name):
        return True

    name_lc = col_name.lower()
    for marker in _BOOL_COL_PREFIXES:
        if name_lc.startswith(marker) and len(name_lc) > len(marker) + 1:
            suffix = name_lc[len(marker):].lstrip("_")
            if not suffix:
                continue
            if pred_lemma == _lemma(suffix):
                return True
    return False


def _is_string(col: Column) -> bool:
    return col.type.lower() in STRING_TYPES


def _lookup(schema: Schema, table_name: str) -> Table | None:
    for t in schema.tables:
        if t.name.lower() == table_name.lower():
            return t
    return None


def _find_column(schema: Schema, table: str, col: str) -> Column | None:
    t = _lookup(schema, table)
    if t is None:
        return None
    for c in t.columns:
        if c.name.lower() == col.lower():
            return c
    return None


_NAME_LIKE = ("name", "title", "label", "code", "username", "email", "fullname")


def _name_like_column(schema: Schema, table: str) -> Column | None:
    """Return the best 'name' column for binding an unquoted literal."""
    t = _lookup(schema, table)
    if t is None:
        return None
    for candidate in _NAME_LIKE:
        for c in t.columns:
            if c.name.lower() == candidate:
                return c
    # Fall back: first string-typed column that isn't an id.
    for c in t.columns:
        if _is_string(c) and not c.name.lower().endswith("id"):
            return c
    return None


def _default_list_columns(schema: Schema, table: str, max_cols: int = 6) -> list[str]:
    """For a LIST intent with no explicit columns, pick sensible defaults."""
    t = _lookup(schema, table)
    if t is None:
        return []
    out: list[str] = []
    # Prefer: id, name, email, then first few non-FK columns.
    preferred = ["id", "name", "title", "code", "email", "mobile", "phone"]
    names = {c.name for c in t.columns}
    for p in preferred:
        for c in t.columns:
            if c.name == p and c.name not in out:
                out.append(c.name)
                break
    for c in t.columns:
        if len(out) >= max_cols:
            break
        if c.name in out:
            continue
        # Skip huge blobs and bookkeeping by default.
        if c.name.lower() in {"createdat", "updatedat", "createdby", "updatedby", "revision"}:
            continue
        out.append(c.name)
    return out[:max_cols]


def _build_where(
    schema: Schema,
    primary: str,
    columns: list[ColumnMatch],
    values: ExtractedValues,
    implicit_values: list[ImplicitValue],
) -> tuple[exp.Expression | None, list[str], list[str]]:
    """Construct WHERE from matched columns + typed values + implicit literals.

    Returns (where_expr, used_column_refs, extra_tables).
      - `used_column_refs` is "Table.col" per binding (for the explanation).
      - `extra_tables` is the set of secondary tables the WHERE references;
        the caller joins them before emitting SQL.
    """
    conditions: list[exp.Expression] = []
    used: list[str] = []
    extra_tables: list[str] = []

    def _record(table: str, col: str, expr: exp.Expression) -> None:
        conditions.append(expr)
        used.append(f"{table}.{col}")
        if table.lower() != primary.lower() and table not in extra_tables:
            extra_tables.append(table)

    # Implicit values first — highest signal: user named an entity explicitly.
    for iv in implicit_values:
        name_col = _name_like_column(schema, iv.table_hint)
        if name_col is None:
            continue
        _record(
            iv.table_hint,
            name_col.name,
            exp.EQ(
                this=_column_ref(iv.table_hint, name_col.name),
                expression=_literal(iv.value),
            ),
        )

    # Quoted strings → prefer primary-table string columns, fall back to any.
    primary_cols = [m for m in columns if m.table.lower() == primary.lower()]
    any_cols = columns
    for qv in list(values.quoted):
        bound = False
        for pool in (primary_cols, any_cols):
            for m in pool:
                if f"{m.table}.{m.column}" in used:
                    continue
                c = _find_column(schema, m.table, m.column)
                if c and _is_string(c):
                    _record(
                        m.table,
                        m.column,
                        exp.EQ(
                            this=_column_ref(m.table, m.column),
                            expression=_literal(qv),
                        ),
                    )
                    bound = True
                    break
            if bound:
                break

    # Booleans → primary boolean columns only (cross-table booleans are too
    # ambiguous to guess).
    for b in list(values.booleans):
        for m in primary_cols:
            if f"{m.table}.{m.column}" in used:
                continue
            c = _find_column(schema, m.table, m.column)
            if c and _is_boolean(c):
                _record(
                    m.table,
                    m.column,
                    exp.EQ(
                        this=_column_ref(m.table, m.column),
                        expression=_literal(b),
                    ),
                )
                break

    # Boolean predicates from "is/are/has" phrasing → bind to a boolean
    # column whose name maps to the predicate token. Handles three shapes:
    #   1. is_service / has_license      (snake_case)
    #   2. isService / hasLicense        (camelCase)
    #   3. isservice / haslicense        (no separator — common in MySQL)
    from .nlp.preprocess import _lemma  # local to avoid cycle

    for predicate_word, sign in (values.boolean_predicates or []):
        pred_lemma = _lemma(predicate_word)
        for m in primary_cols:
            if f"{m.table}.{m.column}" in used:
                continue
            c = _find_column(schema, m.table, m.column)
            if not c or not _is_boolean(c):
                continue
            if not _bool_col_matches_predicate(c.name, pred_lemma):
                continue
            _record(
                m.table,
                m.column,
                exp.EQ(
                    this=_column_ref(m.table, m.column),
                    expression=_literal(bool(sign)),
                ),
            )
            break

    # Negative predicates ("without X", "no X") on NON-boolean columns →
    # emit `WHERE col IS NULL`. Catches "drivers without a license",
    # "products without a category", etc.
    from .nlp.matcher import _split_identifier as _split_id
    for predicate_word, sign in (values.boolean_predicates or []):
        if sign:
            continue
        pred_lemma = _lemma(predicate_word)
        for m in primary_cols:
            if f"{m.table}.{m.column}" in used:
                continue
            c = _find_column(schema, m.table, m.column)
            if not c or _is_boolean(c):  # boolean handled above
                continue
            if pred_lemma not in _split_id(c.name):
                continue
            _record(
                m.table,
                m.column,
                exp.Is(
                    this=_column_ref(m.table, m.column),
                    expression=exp.Null(),
                ),
            )
            break

    # Dates → date/timestamp columns on any matched table.
    for d in list(values.dates):
        bound = False
        for pool in (primary_cols, any_cols):
            for m in pool:
                if f"{m.table}.{m.column}" in used:
                    continue
                c = _find_column(schema, m.table, m.column)
                if c and _is_date(c):
                    _record(
                        m.table,
                        m.column,
                        exp.EQ(
                            this=_column_ref(m.table, m.column),
                            expression=_literal(d),
                        ),
                    )
                    bound = True
                    break
            if bound:
                break

    # Numerics → any matched numeric column.
    numeric_inputs = list(values.integers) + [
        int(x) for x in values.numbers if x.is_integer()
    ]
    for n in numeric_inputs:
        bound = False
        for pool in (primary_cols, any_cols):
            for m in pool:
                if f"{m.table}.{m.column}" in used:
                    continue
                c = _find_column(schema, m.table, m.column)
                if c and _is_numeric(c):
                    _record(
                        m.table,
                        m.column,
                        exp.EQ(
                            this=_column_ref(m.table, m.column),
                            expression=_literal(n),
                        ),
                    )
                    bound = True
                    break
            if bound:
                break

    if not conditions:
        return None, used, extra_tables
    combined = conditions[0]
    for cond in conditions[1:]:
        combined = exp.And(this=combined, expression=cond)
    return combined, used, extra_tables


def _first_numeric_column(matches: list[ColumnMatch], schema: Schema) -> ColumnMatch | None:
    for m in matches:
        c = _find_column(schema, m.table, m.column)
        if c and _is_numeric(c):
            return m
    return None


def _primary_table(
    table_scores: list[tuple[str, float]],
    column_matches: list[ColumnMatch],
) -> str | None:
    if table_scores:
        return table_scores[0][0]
    if column_matches:
        return column_matches[0].table
    return None


def _apply_joins(
    select: exp.Select,
    schema: Schema,
    primary: str,
    tables_needed: list[str],
) -> list[str]:
    """Add JOINs that connect `primary` to every other referenced table.

    Returns the list of tables actually joined (including `primary`).
    """
    joined: list[str] = [primary]
    for other in tables_needed:
        if other.lower() == primary.lower() or other in joined:
            continue
        path = resolve_join_path(schema, primary, other)
        if path is None:
            raise BuildError(
                f"No FK path from {primary} to {other} in the schema — cannot join."
            )
        for (from_t, from_c, to_t, to_c) in path:
            if to_t in joined:
                continue
            on = exp.EQ(
                this=_column_ref(from_t, from_c),
                expression=_column_ref(to_t, to_c),
            )
            select.join(
                exp.Table(this=_quoted_ident(to_t)),
                on=on,
                join_type="INNER",
                copy=False,
            )
            joined.append(to_t)
    return joined


def _pick_aggregate_column(
    intent: str,
    primary: str,
    column_matches: list[ColumnMatch],
    schema: Schema,
) -> ColumnMatch | None:
    """Highest-scoring numeric column (or date, for min/max), primary first."""
    ordered = [m for m in column_matches if m.table == primary] + [
        m for m in column_matches if m.table != primary
    ]
    target = _first_numeric_column(ordered, schema)
    if target is None and intent in ("min", "max", "top"):
        for m in ordered:
            c = _find_column(schema, m.table, m.column)
            if c and _is_date(c):
                target = m
                break
    return target


def build(
    intent: str,
    schema: Schema,
    table_scores: list[tuple[str, float]],
    column_matches: list[ColumnMatch],
    values: ExtractedValues,
    max_rows: int,
    implicit_values: list[ImplicitValue] | None = None,
    dialect: str = "postgres",
) -> BuildResult:
    implicit_values = implicit_values or []
    primary = _primary_table(table_scores, column_matches)
    if primary is None:
        raise BuildError(
            "Could not identify a table in the question. "
            "Try naming a table or entity explicitly."
        )

    # --- Decide projection + WHERE first. Only THEN decide which joins we need.
    select = exp.Select()
    explanation_bits: list[str] = []
    referenced_tables: set[str] = {primary}

    if intent == "count":
        select = select.select(
            exp.Alias(this=exp.Count(this=exp.Star()), alias=_quoted_ident("count")),
            copy=False,
        )
        explanation_bits.append(f"COUNT(*) on {primary}")

    elif intent == "exists":
        select = select.select(
            exp.Alias(
                this=exp.GT(
                    this=exp.Count(this=exp.Star()),
                    expression=exp.Literal.number(0),
                ),
                alias=_quoted_ident("exists"),
            ),
            copy=False,
        )
        explanation_bits.append(f"EXISTS check on {primary}")

    elif intent in ("sum", "avg", "min", "max"):
        target = _pick_aggregate_column(intent, primary, column_matches, schema)
        if target is None:
            raise BuildError(
                f"{intent.upper()} needs a numeric"
                f"{' or date' if intent in ('min', 'max') else ''} column; "
                "none matched the question."
            )
        agg_cls = {"sum": exp.Sum, "avg": exp.Avg, "min": exp.Min, "max": exp.Max}[intent]
        select = select.select(
            exp.Alias(
                this=agg_cls(this=_column_ref(target.table, target.column)),
                alias=_quoted_ident(intent),
            ),
            copy=False,
        )
        referenced_tables.add(target.table)
        explanation_bits.append(f"{intent.upper()}({target.table}.{target.column})")

    elif intent == "top":
        order_col = _pick_aggregate_column("top", primary, column_matches, schema)
        if order_col is None:
            raise BuildError("TOP N needs a numeric or date column to order by.")
        for c in _default_list_columns(schema, primary):
            select = select.select(_column_ref(primary, c), copy=False)
        if order_col.table != primary or order_col.column not in _default_list_columns(schema, primary):
            select = select.select(_column_ref(order_col.table, order_col.column), copy=False)
        select = select.order_by(
            exp.Ordered(this=_column_ref(order_col.table, order_col.column), desc=True),
            copy=False,
        )
        referenced_tables.add(order_col.table)
        explanation_bits.append(
            f"top {values.top_n or max_rows} from {primary} by {order_col.column} desc"
        )

    else:  # "list" and any unknown falls here.
        proj_cols = [m for m in column_matches if m.table == primary]
        if proj_cols:
            for m in proj_cols[:6]:
                select = select.select(_column_ref(m.table, m.column), copy=False)
        else:
            defaults = _default_list_columns(schema, primary)
            if not defaults:
                select = select.select(exp.Star(), copy=False)
            else:
                for c in defaults:
                    select = select.select(_column_ref(primary, c), copy=False)
        explanation_bits.append(f"list from {primary}")

    # WHERE — cross-table allowed. Any secondary tables the WHERE touches get
    # added to `referenced_tables` before joins are resolved.
    where, used_for_where, extra_where_tables = _build_where(
        schema, primary, column_matches, values, implicit_values
    )
    if where is not None:
        select = select.where(where, copy=False)
        explanation_bits.append("filtered by " + ", ".join(used_for_where))
    for t in extra_where_tables:
        referenced_tables.add(t)

    # Now compute joins based solely on tables actually referenced.
    select = select.from_(exp.Table(this=_quoted_ident(primary)), copy=False)
    needed = [t for t in referenced_tables if t.lower() != primary.lower()]
    joined = _apply_joins(select, schema, primary, needed)

    # LIMIT — explicit request wins over max_rows cap.
    if intent == "top":
        select = select.limit(exp.Literal.number(values.top_n or 10), copy=False)
    elif values.limit is not None:
        select = select.limit(exp.Literal.number(values.limit), copy=False)
    elif intent in ("count", "sum", "avg", "min", "max", "exists"):
        pass
    else:
        select = select.limit(exp.Literal.number(max_rows), copy=False)

    sql = select.sql(dialect=dialect)
    conf = 0.8 if where is not None else 0.6
    if intent in ("sum", "avg", "min", "max", "top"):
        conf += 0.05
    return BuildResult(
        sql=sql,
        explanation="; ".join(explanation_bits),
        confidence=min(conf, 0.95),
        tables_used=sorted(set(joined)),
        used_columns=used_for_where,
    )
