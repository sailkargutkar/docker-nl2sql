from __future__ import annotations

from pathlib import Path

import pytest

from app.nlp.intent import IntentClassifier, train
from app.training.seed import SEED


@pytest.fixture
def trained_model(tmp_path: Path) -> Path:
    out = tmp_path / "intent.joblib"
    train(SEED, str(out))
    return out


def test_classifier_round_trip(trained_model):
    clf = IntentClassifier(str(trained_model))
    r = clf.predict("how many employees are there")
    assert r is not None
    assert r.label == "count"
    assert r.confidence > 0.3


def test_top_intent(trained_model):
    clf = IntentClassifier(str(trained_model))
    r = clf.predict("top 5 clients by revenue")
    assert r is not None
    assert r.label == "top"


def test_avg_intent(trained_model):
    clf = IntentClassifier(str(trained_model))
    r = clf.predict("average trip duration")
    assert r is not None
    assert r.label == "avg"


def test_model_missing_returns_none():
    clf = IntentClassifier("/tmp/does-not-exist-intent.joblib")
    assert clf.predict("how many?") is None
