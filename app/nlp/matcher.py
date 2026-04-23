"""
Schema matcher: map natural-language tokens to schema tables/columns.

For each token, we score every (table, column) by:
  1. exact lemma match (10)
  2. rapidfuzz token_set_ratio >= 85 against table/column names (0–8)
  3. WordNet synonym / hypernym hit against declared synonyms (3)
  4. description substring (2)

Top matches become the projected/filterable columns for the SQL builder.

This sidesteps needing a semantic embedding model for schemas with descriptive
identifiers (which most business schemas have) — but stays extensible if we
later want to bolt on ONNX sentence embeddings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from rapidfuzz import fuzz

from ..schema_dsl import Schema
from .preprocess import Preprocessed, Token, _lemma


_CAMEL_SPLIT_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|[_\-\s]+")


@lru_cache(maxsize=2048)
def _split_identifier(name: str) -> tuple[str, ...]:
    """camelCase / snake_case / kebab-case identifier → lemma tokens."""
    if not name:
        return tuple()
    parts = [p for p in _CAMEL_SPLIT_RE.split(name) if p]
    return tuple(_lemma(p.lower()) for p in parts)


@dataclass
class ColumnMatch:
    table: str
    column: str
    score: float
    reason: str  # "exact", "fuzzy", "synonym", "description"


@dataclass
class TableMatch:
    table: str
    score: float


@lru_cache(maxsize=64)
def _wordnet_synonyms(word: str) -> frozenset[str]:
    try:
        from nltk.corpus import wordnet
    except LookupError:
        return frozenset()

    out: set[str] = set()
    for syn in wordnet.synsets(word):
        for lemma in syn.lemmas():
            out.add(lemma.name().replace("_", " ").lower())
        for hyper in syn.hypernyms():
            for lemma in hyper.lemmas():
                out.add(lemma.name().replace("_", " ").lower())
    return frozenset(out)


def _norm(s: str) -> str:
    return _lemma(s.lower())


def _column_synonym_hit(token: Token, declared: list[str]) -> bool:
    if not declared:
        return False
    tok = token.lemma
    declared_norm = {_norm(d) for d in declared}
    if tok in declared_norm:
        return True
    for syn in _wordnet_synonyms(tok):
        if syn in declared_norm or syn.split()[-1] in declared_norm:
            return True
    return False


def _fuzzy(a: str, b: str) -> float:
    """0.0–1.0 similarity using rapidfuzz token-set ratio."""
    return fuzz.token_set_ratio(a, b) / 100.0


def score_tables(pre: Preprocessed, schema: Schema) -> list[TableMatch]:
    """Rank tables by relevance to the question."""
    tokens = [t for t in pre.tokens if not t.is_quoted]
    out: list[TableMatch] = []
    for t in schema.tables:
        parts = _split_identifier(t.name)
        name_joined = "".join(parts)
        score = 0.0
        for tok in tokens:
            if tok.lemma in parts or tok.lemma == name_joined:
                score += 10.0
            else:
                f = max((_fuzzy(tok.lemma, p) for p in parts), default=0.0)
                if f >= 0.85:
                    score += f * 4.0
        if t.description:
            desc_l = t.description.lower()
            for tok in tokens:
                if tok.lemma in desc_l:
                    score += 0.5
        if score > 0:
            out.append(TableMatch(table=t.name, score=score))
    out.sort(key=lambda m: m.score, reverse=True)
    return out


def score_columns(
    pre: Preprocessed,
    schema: Schema,
    tables: list[str] | None = None,
) -> list[ColumnMatch]:
    tokens = [t for t in pre.tokens if not t.is_quoted]
    table_filter = {t.lower() for t in tables} if tables else None
    out: list[ColumnMatch] = []

    for table in schema.tables:
        if table_filter is not None and table.name.lower() not in table_filter:
            continue
        for col in table.columns:
            parts = _split_identifier(col.name)
            joined = "".join(parts)
            best: ColumnMatch | None = None
            for tok in tokens:
                if tok.lemma in parts or tok.lemma == joined:
                    c = ColumnMatch(table.name, col.name, 10.0, "exact")
                elif _column_synonym_hit(tok, col.synonyms + [col.name]):
                    c = ColumnMatch(table.name, col.name, 6.0, "synonym")
                else:
                    f = max((_fuzzy(tok.lemma, p) for p in parts), default=0.0)
                    if f >= 0.85:
                        c = ColumnMatch(table.name, col.name, f * 5.0, "fuzzy")
                    elif col.description and tok.lemma in col.description.lower():
                        c = ColumnMatch(table.name, col.name, 2.0, "description")
                    else:
                        continue
                if best is None or c.score > best.score:
                    best = c
            if best is not None:
                out.append(best)
    out.sort(key=lambda m: m.score, reverse=True)
    return out


def resolve_join_path(schema: Schema, a: str, b: str) -> list[tuple[str, str, str, str]] | None:
    """Return a list of (from_table, from_col, to_table, to_col) edges that
    connect `a` to `b` using declared FKs or junction tables. None if unreachable.

    Breadth-first over the schema graph — keeps the first path found.
    """
    if a == b:
        return []
    by_name = schema.table_map_ci()
    if a.lower() not in by_name or b.lower() not in by_name:
        return None

    # Build adjacency: direct FKs in either direction.
    edges: dict[str, list[tuple[str, str, str, str]]] = {}
    for table in schema.tables:
        for col in table.columns:
            if col.fk:
                try:
                    other, other_col = col.fk.split(".", 1)
                except ValueError:
                    continue
                edges.setdefault(table.name.lower(), []).append(
                    (table.name, col.name, other, other_col)
                )
                edges.setdefault(other.lower(), []).append(
                    (other, other_col, table.name, col.name)
                )

    # BFS.
    queue: list[tuple[str, list[tuple[str, str, str, str]]]] = [(a.lower(), [])]
    seen = {a.lower()}
    while queue:
        cur, path = queue.pop(0)
        if cur == b.lower():
            return path
        for edge in edges.get(cur, []):
            nxt = edge[2].lower()
            if nxt in seen:
                continue
            seen.add(nxt)
            queue.append((nxt, path + [edge]))
    return None
