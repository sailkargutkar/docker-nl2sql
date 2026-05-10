"""
Implicit value detection: unquoted proper nouns like "org swaraj" or
"client acme" where a schema-matching word introduces a value literal for the
named entity.

Patterns recognised:
  "org swaraj"           → Organization.name = 'swaraj'
  "client acme"          → Client.name = 'acme'
  "for client acme"      → Client.name = 'acme'
  "named swaraj"         → <inferred table>.name = 'swaraj'
  "employee john doe"    → Employee.name = 'john doe'  (multi-word value)

Emits *hints* — the builder still validates that the hinted table has a
reasonable name-like column, and falls back silently if not.
"""

from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz

from ..schema_dsl import Schema
from .matcher import _split_identifier
from .preprocess import Preprocessed, _lemma


# Above this similarity, a candidate "value" token is more likely a typo
# of a known schema lemma than an actual literal. Used to suppress
# bogus WHERE name='suplier' bindings when the user typo'd a column.
_TYPO_VS_LEMMA_THRESHOLD = 0.85


_INTRO_WORDS = {"named", "called", "name", "with"}
_CONNECTIVE_WORDS = {
    "for", "of", "in", "at", "to", "by", "belongs", "belong", "from",
    # Negation markers — these never refer to a literal value; the
    # negative-predicate logic in values.extract() handles them.
    "no", "not", "without", "any", "all",
    # Spatial / categorical prepositions. "products under the Pizza
    # category" — `under` is the relation marker, not part of the value.
    "under", "above", "below", "between", "through", "via",
    # Temporal markers commonly preceding values
    "before", "after", "since",
    # Demonstratives / determiners that survived stopword removal
    "this", "that", "these", "those",
    # SQL action / aggregation verbs — never literals, even when adjacent
    # to a table token (otherwise "list products" emits ImplicitValue with
    # value='list', and Pattern 3 below would do the same on "list" + table).
    "show", "list", "find", "get", "give", "fetch", "display", "return",
    "count", "total", "sum", "average", "max", "min", "top", "first", "last",
    "search", "select", "tell", "say", "want", "need",
    "is", "are", "was", "were", "has", "have", "had", "do", "does", "did",
}


@dataclass
class ImplicitValue:
    table_hint: str
    value: str


_MIN_PREFIX = 3  # "org" → "organization"; avoids "id" → "invoice"


def _register_alias(target: dict[str, str], key: str, table: str) -> None:
    """Ambiguous keys map to "" sentinel — treated as no-hint."""
    if key in target and target[key] != table:
        target[key] = ""
    elif key not in target:
        target[key] = table


def _known_lemmas(schema: Schema) -> tuple[set[str], dict[str, str]]:
    """Return (all-known-schema-lemmas, lemma-to-table-if-unique).

    Table-name lemmas AND meaningful prefixes (>= 3 chars) both register as
    table hints, so 'org' resolves to 'Organization', 'emp' to 'Employee'.
    """
    all_lemmas: set[str] = set()
    table_by_lemma: dict[str, str] = {}
    # Always collect all schema lemmas (from tables and columns) so values
    # aren't misidentified as literals. Register both split parts AND the
    # joined-lowercase form so e.g. "paymentTerm" (tokenized as one word)
    # still resolves.
    for t in schema.tables:
        parts = _split_identifier(t.name)
        for part in parts:
            all_lemmas.add(part)
        all_lemmas.add(t.name.lower())
        all_lemmas.add("".join(parts))
        for c in t.columns:
            col_parts = _split_identifier(c.name)
            for part in col_parts:
                all_lemmas.add(part)
            all_lemmas.add(c.name.lower())
            all_lemmas.add("".join(col_parts))

    # Register table hints in two passes, root entities winning over compound
    # tables. "Organization" claims "organization" and prefix "org" before
    # "UserOrg" (compound) has any chance — so "org swaraj" → Organization,
    # not UserOrg.
    single_part_tables = [
        t for t in schema.tables if len(_split_identifier(t.name)) == 1
    ]
    multi_part_tables = [
        t for t in schema.tables if len(_split_identifier(t.name)) > 1
    ]

    # Pass 1: root (1-part) tables claim their name AND their prefixes.
    for t in sorted(single_part_tables, key=lambda x: len(x.name)):
        word = _split_identifier(t.name)[0]
        _register_alias(table_by_lemma, word, t.name)
        for plen in range(_MIN_PREFIX, len(word)):
            prefix = word[:plen]
            if prefix not in table_by_lemma:
                table_by_lemma[prefix] = t.name
                all_lemmas.add(prefix)

    # Pass 2: compound tables claim each of their parts only if still free.
    for t in multi_part_tables:
        for part in _split_identifier(t.name):
            if part not in table_by_lemma:
                table_by_lemma[part] = t.name
            elif table_by_lemma[part] != t.name:
                # Collision — mark ambiguous so neither wins.
                # But only if the existing claim is ALSO a compound, otherwise
                # a root table's claim stays.
                existing = table_by_lemma[part]
                existing_parts = _split_identifier(existing)
                if len(existing_parts) > 1:
                    table_by_lemma[part] = ""  # ambiguous sentinel
    # Drop ambiguous sentinels AND remove any leftover empty string keys.
    table_by_lemma = {k: v for k, v in table_by_lemma.items() if v}
    return all_lemmas, table_by_lemma


def detect(pre: Preprocessed, schema: Schema) -> list[ImplicitValue]:
    all_lemmas, table_by_lemma = _known_lemmas(schema)
    # Pre-compute a list of "long" schema lemmas (≥4 chars) for cheap fuzzy
    # rejection. Short lemmas (id, vat, pan) are skipped here — they have
    # too high a false-positive rate at any reasonable threshold.
    long_lemmas = [s for s in all_lemmas if len(s) >= 4]

    tokens = [t for t in pre.tokens if not t.is_quoted]
    out: list[ImplicitValue] = []

    def _looks_like_typo_of_schema(token_lemma: str) -> bool:
        """Was this token probably meant to be a column / table name the
        user mistyped? If so, it's not a literal value to bind."""
        if len(token_lemma) < 4:
            return False
        for lemma in long_lemmas:
            if fuzz.token_set_ratio(token_lemma, lemma) / 100.0 >= _TYPO_VS_LEMMA_THRESHOLD:
                return True
        return False

    def _is_value_candidate(token_index: int) -> bool:
        """A token is a value candidate if it doesn't match any schema entity
        (exactly OR via a likely typo) and isn't a structural word."""
        t = tokens[token_index]
        if t.lemma in all_lemmas:
            return False
        if t.raw.isdigit():
            return False
        if t.lemma in _INTRO_WORDS or t.lemma in _CONNECTIVE_WORDS:
            return False
        if _looks_like_typo_of_schema(t.lemma):
            return False
        return True

    consumed: set[int] = set()
    i = 0
    while i < len(tokens):
        if i in consumed:
            i += 1
            continue
        tok = tokens[i]

        # Pattern 1: <table-word> <value...>
        # BUT defer to Pattern 3 (later iteration) when the value is
        # sandwiched as <table-A> <value> <table-B> AND table-B has no
        # value of its own at (i+3). That's the head-noun shape:
        #   "products in Snacks category"   → defer (Snacks → categories)
        #   "users in org X of client Y"    → fire (X → org, then Y → client)
        table = table_by_lemma.get(tok.lemma)
        if table and i + 1 < len(tokens) and _is_value_candidate(i + 1):
            value_after = tokens[i + 1]
            next_table_pos = (
                i + 2 if i + 2 < len(tokens) and table_by_lemma.get(tokens[i + 2].lemma)
                else None
            )
            next_table_has_own_value = (
                next_table_pos is not None
                and next_table_pos + 1 < len(tokens)
                and _is_value_candidate(next_table_pos + 1)
            )
            defer_to_pattern_3 = (
                next_table_pos is not None and not next_table_has_own_value
            )
            if not defer_to_pattern_3:
                value_parts = [value_after.raw]
                j = i + 2
                while j < len(tokens) and _is_value_candidate(j):
                    value_parts.append(tokens[j].raw)
                    j += 1
                out.append(ImplicitValue(table_hint=table, value=" ".join(value_parts)))
                consumed.update(range(i, j))
                i = j
                continue

        # Pattern 3: <value...> <table-word> — value precedes the table.
        # Catches phrasings like "Pizza category" → categories.name='Pizza',
        # "London office" → office.name='London', etc.
        if (
            table
            and i > 0
            and (i - 1) not in consumed
            and _is_value_candidate(i - 1)
        ):
            j = i - 1
            value_parts: list[str] = []
            while j >= 0 and j not in consumed and _is_value_candidate(j):
                value_parts.insert(0, tokens[j].raw)
                j -= 1
            if value_parts:
                out.append(ImplicitValue(
                    table_hint=table,
                    value=" ".join(value_parts),
                ))
                consumed.update(range(j + 1, i + 1))
                i += 1
                continue

        # Pattern 2: named/called <value> — inferred table from context.
        if tok.lemma in _INTRO_WORDS and i + 1 < len(tokens) and _is_value_candidate(i + 1):
            # Look *backwards* for the most recent table word to attach to.
            hint = None
            for k in range(i - 1, max(i - 4, -1), -1):
                cand_table = table_by_lemma.get(tokens[k].lemma)
                if cand_table:
                    hint = cand_table
                    break
            if hint is None:
                i += 1
                continue
            value_parts = [tokens[i + 1].raw]
            j = i + 2
            while j < len(tokens) and _is_value_candidate(j):
                value_parts.append(tokens[j].raw)
                j += 1
            out.append(ImplicitValue(table_hint=hint, value=" ".join(value_parts)))
            i = j
            continue

        i += 1

    return out
