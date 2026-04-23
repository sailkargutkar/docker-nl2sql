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
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any


_NUMBER_RE = re.compile(r"(?<![A-Za-z_])-?\d+(?:\.\d+)?(?![A-Za-z_])")
_YES_WORDS = {"true", "yes", "active", "enabled", "on"}
_NO_WORDS = {"false", "no", "inactive", "disabled", "off"}


@dataclass
class ExtractedValues:
    quoted: list[str]
    numbers: list[float]
    integers: list[int]
    dates: list[date]
    booleans: list[bool]
    limit: int | None = None
    top_n: int | None = None

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
