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
    (re.compile(r"\b(total|sum of|aggregate)\b", re.I), "sum"),
    (re.compile(r"\b(average|avg|mean)\b", re.I), "avg"),
    (re.compile(r"\b(lowest|earliest|min(imum)?|smallest|first)\b", re.I), "min"),
    (re.compile(r"\b(highest|latest|max(imum)?|largest|most recent)\b", re.I), "max"),
    (re.compile(r"\btop\s+\d+|\btop\b.*\bby\b", re.I), "top"),
    (re.compile(r"\b(is there|does (any|\w+) (exist|have)|any\s+\w+\s+without)\b", re.I), "exists"),
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


def generate_sql(
    question: str,
    schema: Schema,
    max_rows: int,
    intent_model_path: str,
) -> GenerationResult:
    pre = preprocess(question)
    values = extract(question, pre.quoted_literals)

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

    try:
        result = build(
            intent=intent,
            schema=schema,
            table_scores=table_scores,
            column_matches=column_matches,
            values=values,
            max_rows=max_rows,
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
