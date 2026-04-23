"""
Retrain the intent classifier from SEED + history.db successes.

The history table records every (question, status, sql) tuple. We can't
auto-label the intent from a successful SQL string — but we can infer it
heuristically from the projection (COUNT → count, SUM → sum, ORDER BY LIMIT
with desc → top, etc.). The labels that don't match any heuristic are
skipped. This keeps the training set honest even as the service runs.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from ..nlp.intent import INTENT_LABELS, train
from .seed import SEED


_COUNT_RE = re.compile(r"\bCOUNT\s*\(", re.I)
_SUM_RE = re.compile(r"\bSUM\s*\(", re.I)
_AVG_RE = re.compile(r"\bAVG\s*\(", re.I)
_MIN_RE = re.compile(r"\bMIN\s*\(", re.I)
_MAX_RE = re.compile(r"\bMAX\s*\(", re.I)
_ORDER_DESC_LIMIT_RE = re.compile(r"ORDER\s+BY[^;]+DESC[^;]+LIMIT", re.I)


def _infer_intent(sql: str) -> str | None:
    if not sql:
        return None
    if _ORDER_DESC_LIMIT_RE.search(sql):
        return "top"
    if _COUNT_RE.search(sql):
        return "count"
    if _SUM_RE.search(sql):
        return "sum"
    if _AVG_RE.search(sql):
        return "avg"
    if _MIN_RE.search(sql):
        return "min"
    if _MAX_RE.search(sql):
        return "max"
    if sql.strip().upper().startswith("SELECT"):
        return "list"
    return None


def collect_from_history(history_db: str, limit: int = 5000) -> list[tuple[str, str]]:
    path = Path(history_db)
    if not path.exists():
        return []
    out: list[tuple[str, str]] = []
    with sqlite3.connect(history_db) as c:
        c.row_factory = sqlite3.Row
        for row in c.execute(
            "SELECT question, sql FROM queries "
            "WHERE status = 'ok' AND question IS NOT NULL AND sql IS NOT NULL "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ):
            q = (row["question"] or "").strip()
            s = (row["sql"] or "").strip()
            intent = _infer_intent(s)
            if q and intent and intent in INTENT_LABELS:
                out.append((q, intent))
    return out


def fit(history_db: str, model_path: str) -> dict:
    seeded = list(SEED)
    derived = collect_from_history(history_db)

    # De-dupe while preserving seed priority.
    seen: set[str] = set()
    combined: list[tuple[str, str]] = []
    for q, label in seeded + derived:
        key = q.lower().strip()
        if key in seen:
            continue
        seen.add(key)
        combined.append((q, label))

    report = train(combined, model_path)
    report["from_seed"] = len(seeded)
    report["from_history"] = len(derived)
    report["total_unique"] = len(combined)
    return report


def report_json(history_db: str, model_path: str) -> str:
    return json.dumps(fit(history_db, model_path), indent=2)
