"""
NL → SQL orchestrator.

Replaces the LLM call. Pipeline:

    question
      → preprocess (tokens, lemmas, quoted literals)
      → intent classifier (cached TF-IDF + LinearSVC)
      → schema matcher (tables, columns; WordNet + rapidfuzz)
      → value extractor (numbers, dates, booleans, limits)
      → builder (sqlglot AST → SQL)

If the classifier isn't trained yet, we fall back to regex-based intent
detection. Every layer is optional-tolerant so a cold-started image still
answers questions (less precisely) while the classifier trains.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .builder import BuildError, build
from .nlp.implicit import detect as detect_implicit
from .nlp.intent import INTENT_LABELS, IntentClassifier
from .nlp.matcher import score_columns, score_tables
from .nlp.preprocess import preprocess
from .nlp.values import extract
from .schema_dsl import Schema


@dataclass
class GenerationResult:
    sql: str
    explanation: str
    confidence: float
    intent: str = ""
    tables_used: list[str] = field(default_factory=list)
    model: str = "local-rule+tfidf"


_FALLBACK_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(how many|count( of)?|number of)\b", re.I), "count"),
    # "total users" (plural, no numeric column ahead) means count. We bias this
    # before `sum` so "total X <plural>" doesn't always resolve to sum. A later
    # override downgrades it to sum if a numeric column is clearly referenced.
    (re.compile(r"\btotal\s+\w+s\b", re.I), "count"),
    (re.compile(r"\b(sum of|aggregate|total)\b", re.I), "sum"),
    (re.compile(r"\b(average|avg|mean)\b", re.I), "avg"),
    (re.compile(r"\b(lowest|earliest|min(imum)?|smallest|first)\b", re.I), "min"),
    (re.compile(r"\b(highest|latest|max(imum)?|largest|most recent)\b", re.I), "max"),
    (re.compile(r"\btop\s+\d+|\btop\b.*\bby\b", re.I), "top"),
    (re.compile(
        r"\b(is\s+there|are\s+there|do\s+we\s+have|"
        r"does\s+(any|\w+)\s+(exist|have)|"
        r"any\s+\w+\s+without)\b",
        re.I,
    ), "exists"),
]


def _fallback_intent(question: str) -> str:
    for pat, label in _FALLBACK_PATTERNS:
        if pat.search(question):
            return label
    return "list"


_singleton: IntentClassifier | None = None


def get_classifier(model_path: str) -> IntentClassifier:
    global _singleton
    if _singleton is None or _singleton.model_path != model_path:
        _singleton = IntentClassifier(model_path)
    return _singleton


def reset_classifier() -> None:
    global _singleton
    if _singleton is not None:
        _singleton.reload()


_TOTAL_PLURAL_RE = re.compile(r"\btotal\s+([A-Za-z]+s)\b", re.I)


def _override_intent_for_total(intent: str, question: str, column_matches) -> str:
    """If the question is 'total <plural-noun>' and no numeric column scored
    high, prefer `count` over `sum`/`list`. Covers 'total users belongs to
    org swaraj' without hand-labelling every phrasing.
    """
    if not _TOTAL_PLURAL_RE.search(question):
        return intent
    if intent == "count":
        return intent
    has_strong_numeric_match = any(
        m.score >= 8.0 and m.reason == "exact" for m in column_matches
    )
    if has_strong_numeric_match:
        return intent  # user really did mean "total <numeric column>"
    return "count"


def generate_sql(
    question: str,
    schema: Schema,
    max_rows: int,
    intent_model_path: str,
    dialect: str = "postgres",
    force_intent: str | None = None,
    force_primary_table: str | None = None,
) -> GenerationResult:
    """Produce a single SQL candidate for `question`.

    `force_intent` and `force_primary_table` let the alternatives orchestrator
    explore neighbouring interpretations of the same question (top-K).
    """
    pre = preprocess(question)
    values = extract(question, pre.quoted_literals)
    implicit_values = detect_implicit(pre, schema)

    # If a word will be used as a boolean predicate (e.g. "are services"
    # → bind isservice=TRUE), strip it from implicit-value candidates so
    # we don't *also* emit a bogus `name = 'services'` filter.
    predicate_words = {p[0].lower() for p in (values.boolean_predicates or [])}
    if predicate_words:
        implicit_values = [
            iv for iv in implicit_values
            if iv.value.lower() not in predicate_words
        ]

    if force_intent and force_intent in INTENT_LABELS:
        intent = force_intent
        intent_conf = 0.6  # forced — reasonable confidence, not max
    else:
        clf = get_classifier(intent_model_path)
        prediction = clf.predict(question) if clf.available else None
        if prediction is not None and prediction.confidence >= 0.3:
            intent = prediction.label
            intent_conf = prediction.confidence
        else:
            intent = _fallback_intent(question)
            intent_conf = 0.4  # low-confidence fallback
        if intent not in INTENT_LABELS:
            intent = "list"

    table_matches = score_tables(pre, schema)
    table_scores = [(m.table, m.score) for m in table_matches]

    primary_tables = [t for t, _ in table_scores[:3]] or None
    column_matches = score_columns(pre, schema, primary_tables)

    # If we found strong column matches on tables we hadn't ranked high,
    # include those tables too.
    known_tables = {t for t, _ in table_scores}
    for m in column_matches:
        if m.table not in known_tables:
            table_scores.append((m.table, m.score / 2))
            known_tables.add(m.table)

    # Implicit-value tables also bias the primary pick: if "org swaraj" pointed
    # at Organization but Organization wasn't scored yet, seed it so the join
    # path is reachable from whichever primary table we pick.
    for iv in implicit_values:
        if iv.table_hint not in known_tables:
            table_scores.append((iv.table_hint, 1.0))
            known_tables.add(iv.table_hint)

    # If a specific primary table was forced, hoist it to the top so the
    # builder picks it. We boost rather than replace so the matcher's
    # other tables remain available for joins.
    if force_primary_table:
        if force_primary_table not in {t for t, _ in table_scores}:
            table_scores.append((force_primary_table, 100.0))
        else:
            table_scores = [
                (t, 100.0 if t == force_primary_table else s)
                for t, s in table_scores
            ]
        table_scores.sort(key=lambda ts: ts[1], reverse=True)

    if not force_intent:
        intent = _override_intent_for_total(intent, question, column_matches)

    try:
        result = build(
            intent=intent,
            schema=schema,
            table_scores=table_scores,
            column_matches=column_matches,
            values=values,
            max_rows=max_rows,
            implicit_values=implicit_values,
            dialect=dialect,
        )
    except BuildError as e:
        return GenerationResult(
            sql="",
            explanation=str(e),
            confidence=0.0,
            intent=intent,
        )

    return GenerationResult(
        sql=result.sql,
        explanation=result.explanation,
        confidence=min(result.confidence, intent_conf + 0.2),
        intent=intent,
        tables_used=result.tables_used,
    )


# ---------- Top-K candidate generation ----------


def _top_alt_intents(question: str, model_path: str, k: int = 2) -> list[str]:
    """Return the top-k intent labels from the classifier (best first),
    excluding the top-1. Falls back to a small handcrafted list if the
    classifier isn't trained.
    """
    clf = get_classifier(model_path)
    if clf.available:
        pipe = clf._ensure()  # type: ignore[union-attr]
        if pipe is not None:
            probs = pipe.predict_proba([question])[0]
            ranked = sorted(
                zip(pipe.classes_, probs), key=lambda cp: cp[1], reverse=True
            )
            # Skip the first (already used by the primary pass)
            return [str(c) for c, _ in ranked[1: 1 + k]]

    # Fallback — neighbouring intents to count/list.
    primary = _fallback_intent(question)
    fallback_neighbours = {
        "list": ["count", "top"],
        "count": ["list", "exists"],
        "top": ["list", "max"],
        "exists": ["count", "list"],
        "sum": ["avg", "max"],
        "avg": ["sum", "max"],
        "min": ["max", "list"],
        "max": ["min", "list"],
    }
    return fallback_neighbours.get(primary, ["list", "count"])[:k]


def _top_alt_tables(question: str, schema: Schema, k: int = 2) -> list[str]:
    pre = preprocess(question)
    matches = score_tables(pre, schema)
    # Skip the first (already used by the primary pass)
    return [m.table for m in matches[1: 1 + k] if m.score > 0]


def generate_alternatives(
    question: str,
    schema: Schema,
    max_rows: int,
    intent_model_path: str,
    dialect: str = "postgres",
    n: int = 3,
) -> list[GenerationResult]:
    """Generate up to `n` distinct SQL candidates for the same question.

    Strategy: run the primary generator once, then probe a small number
    of neighbouring (intent, primary_table) combinations. De-dupe by
    final SQL string. Sort by confidence descending.

    Cheap: 3-5 generator invocations per call. Each invocation is the
    same cost as today's single pass (~10-50 ms), all symbolic.
    """
    seen: set[str] = set()
    primary: GenerationResult | None = None
    alternatives: list[GenerationResult] = []

    # 1) Primary candidate — uses the classifier's top intent + top table.
    p = generate_sql(
        question, schema, max_rows, intent_model_path, dialect=dialect,
    )
    if p.sql:
        seen.add(p.sql)
        primary = p

    # 2) Vary intent.
    for alt_intent in _top_alt_intents(question, intent_model_path, k=2):
        r = generate_sql(
            question, schema, max_rows, intent_model_path,
            dialect=dialect, force_intent=alt_intent,
        )
        if r.sql and r.sql not in seen:
            seen.add(r.sql)
            alternatives.append(r)

    # 3) Vary primary table.
    for alt_table in _top_alt_tables(question, schema, k=2):
        r = generate_sql(
            question, schema, max_rows, intent_model_path,
            dialect=dialect, force_primary_table=alt_table,
        )
        if r.sql and r.sql not in seen:
            seen.add(r.sql)
            alternatives.append(r)

    # Primary always stays at position 0 — it's the system's best guess.
    # The remaining slots get the highest-confidence alternatives.
    alternatives.sort(key=lambda c: c.confidence, reverse=True)
    if primary is not None:
        return [primary] + alternatives[: n - 1]
    return alternatives[:n]
