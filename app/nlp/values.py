"""
Value extraction: pull literals (numbers, dates, booleans, quoted strings) out
of a question so the builder can bind them into WHERE clauses.

Returns all candidates — the builder decides which apply based on the column
types it picked. We never interpolate raw strings into SQL: values ride through
SQLAlchemy `bindparam` so injection surface stays zero even if a quoted value
contains an apostrophe.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any


_NUMBER_RE = re.compile(r"(?<![A-Za-z_])-?\d+(?:\.\d+)?(?![A-Za-z_])")
_YES_WORDS = {"true", "yes", "active", "enabled", "on"}
# Note: 'no' deliberately omitted — it almost always functions as a negation
# marker ('no warranty') rather than a standalone boolean value. The
# negative-predicate regex (_BOOL_PRED_NEGATIVE_RE) handles those cases.
_NO_WORDS = {"false", "inactive", "disabled", "off"}


@dataclass
class ExtractedValues:
    quoted: list[str]
    numbers: list[float]
    integers: list[int]
    dates: list[date]
    booleans: list[bool]
    limit: int | None = None
    top_n: int | None = None
    # Words appearing right after "is/are/has/have/with/without". Bound to
    # boolean columns at builder time, so phrasings like "products that
    # are services" get `WHERE isservice = TRUE`. Sign is True for positive
    # phrasing (is/are/has) and False for negative (without/no).
    boolean_predicates: list[tuple[str, bool]] = field(default_factory=list)

    def has_any(self) -> bool:
        return bool(
            self.quoted or self.numbers or self.integers or self.dates or self.booleans
        )


def _parse_date(text: str) -> date | None:
    try:
        import dateparser
    except ImportError:
        return None
    dt = dateparser.parse(
        text,
        settings={"PREFER_DATES_FROM": "past", "RETURN_AS_TIMEZONE_AWARE": False},
    )
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt.date()
    return dt


_BOOL_PRED_POSITIVE_RE = re.compile(
    r"\b(?:is|are|has|have|with)\s+(?:a\s+|an\s+|the\s+|any\s+)?([a-zA-Z]+)\b",
    re.I,
)
_BOOL_PRED_NEGATIVE_RE = re.compile(
    r"\b(?:without|not|no)\s+(?:a\s+|an\s+|the\s+|any\s+)?([a-zA-Z]+)\b",
    re.I,
)


def extract(question: str, quoted_literals: list[str] | None = None) -> ExtractedValues:
    q = question

    # Booleans.
    lowered = q.lower()
    booleans: list[bool] = []
    words = re.findall(r"[A-Za-z]+", lowered)
    for w in words:
        if w in _YES_WORDS:
            booleans.append(True)
        elif w in _NO_WORDS:
            booleans.append(False)

    # Boolean predicates — words following "is/are/has/have/with" (positive)
    # or "without/not/no" (negative). The builder binds these to boolean
    # columns at WHERE-time so phrasings like "products that are services"
    # produce `WHERE isservice = TRUE`.
    boolean_predicates: list[tuple[str, bool]] = []
    seen_predicates: set[str] = set()
    for m in _BOOL_PRED_POSITIVE_RE.finditer(lowered):
        word = m.group(1).lower()
        if word in _YES_WORDS or word in _NO_WORDS:
            continue  # 'is active' already captured by booleans above
        if word in seen_predicates:
            continue
        seen_predicates.add(word)
        boolean_predicates.append((word, True))
    for m in _BOOL_PRED_NEGATIVE_RE.finditer(lowered):
        word = m.group(1).lower()
        if word in seen_predicates:
            continue
        seen_predicates.add(word)
        boolean_predicates.append((word, False))

    # Limit / top-N — these are control modifiers, not filter values.
    limit = None
    top_n = None
    m = re.search(r"\btop\s+(\d+)\b", lowered)
    if m:
        top_n = int(m.group(1))
    m = re.search(r"\b(?:first|only)\s+(\d+)\b", lowered)
    if m and top_n is None:
        top_n = int(m.group(1))
    m = re.search(r"\blimit\s+(\d+)\b", lowered)
    if m:
        limit = int(m.group(1))

    # Remove control phrases before harvesting numbers so "top 5" doesn't
    # leak into integer filter candidates.
    stripped = re.sub(r"\btop\s+\d+\b", " ", lowered)
    stripped = re.sub(r"\b(?:first|only)\s+\d+\b", " ", stripped)
    stripped = re.sub(r"\blimit\s+\d+\b", " ", stripped)

    numbers: list[float] = []
    integers: list[int] = []
    for m in _NUMBER_RE.finditer(stripped):
        v = m.group(0)
        if "." in v:
            numbers.append(float(v))
        else:
            integers.append(int(v))

    # Dates — try parsing every 2–4 word window. Cheap on short questions.
    dates: list[date] = []
    tokens = re.findall(r"\S+", stripped)
    seen = set()
    for size in (4, 3, 2, 1):
        for i in range(len(tokens) - size + 1):
            chunk = " ".join(tokens[i : i + size])
            if any(c.isdigit() for c in chunk) or _looks_like_date_word(chunk):
                d = _parse_date(chunk)
                if d is not None and d.isoformat() not in seen:
                    dates.append(d)
                    seen.add(d.isoformat())

    return ExtractedValues(
        quoted=list(quoted_literals or []),
        numbers=numbers,
        integers=integers,
        dates=dates,
        booleans=booleans,
        limit=limit,
        top_n=top_n,
        boolean_predicates=boolean_predicates,
    )


_DATE_WORDS = {
    "today", "yesterday", "tomorrow", "now",
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "week", "month", "year", "day", "ago",
}


def _looks_like_date_word(chunk: str) -> bool:
    return any(w in _DATE_WORDS for w in chunk.lower().split())
