"""
Intent classifier — TF-IDF char n-grams + LinearSVC with calibration.

Labels:
  count      "how many X"
  list       "show me / give me / list X"
  sum        "total / sum of X"
  avg        "average / mean of X"
  min        "lowest / earliest / minimum X"
  max        "highest / latest / maximum X"
  top        "top N X by Y"
  exists     "is there / does X exist"

The classifier is small (< 100 KB once pickled) and fits in ~1 second on a
few hundred examples. It is the closest thing to a "model" in the whole stack.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import joblib
from sklearn.calibration import CalibratedClassifierCV
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.pipeline import Pipeline
from sklearn.svm import LinearSVC


INTENT_LABELS = ("count", "list", "sum", "avg", "min", "max", "top", "exists")


@dataclass
class IntentPrediction:
    label: str
    confidence: float


def build_pipeline() -> Pipeline:
    return Pipeline(
        [
            (
                "tfidf",
                TfidfVectorizer(
                    ngram_range=(1, 3),
                    analyzer="char_wb",
                    min_df=1,
                    sublinear_tf=True,
                    lowercase=True,
                ),
            ),
            (
                "clf",
                CalibratedClassifierCV(
                    estimator=LinearSVC(C=1.0),
                    cv=3,
                    method="sigmoid",
                ),
            ),
        ]
    )


def train(
    examples: Iterable[tuple[str, str]],
    out_path: str,
) -> dict:
    xs: list[str] = []
    ys: list[str] = []
    for q, label in examples:
        xs.append(q)
        ys.append(label)
    if not xs:
        raise ValueError("No training examples provided.")

    pipe = build_pipeline()
    pipe.fit(xs, ys)

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(pipe, out_path, compress=3)
    return {"examples": len(xs), "labels": sorted(set(ys)), "path": out_path}


class IntentClassifier:
    """Lazy-loaded singleton wrapper so the model loads once per process."""

    def __init__(self, model_path: str):
        self.model_path = model_path
        self._pipeline: Pipeline | None = None

    @property
    def available(self) -> bool:
        return os.path.isfile(self.model_path)

    def _ensure(self) -> Pipeline | None:
        if self._pipeline is not None:
            return self._pipeline
        if not self.available:
            return None
        self._pipeline = joblib.load(self.model_path)
        return self._pipeline

    def reload(self) -> None:
        self._pipeline = None

    def predict(self, question: str) -> IntentPrediction | None:
        pipe = self._ensure()
        if pipe is None:
            return None
        probs = pipe.predict_proba([question])[0]
        classes = pipe.classes_
        idx = int(probs.argmax())
        return IntentPrediction(label=str(classes[idx]), confidence=float(probs[idx]))
