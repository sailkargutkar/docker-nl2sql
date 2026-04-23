from __future__ import annotations

from datetime import date

from app.nlp.values import extract


def test_quoted_literal():
    v = extract("find users named 'Alice'", quoted_literals=["Alice"])
    assert v.quoted == ["Alice"]


def test_integer():
    v = extract("show employees with salary 50000")
    assert 50000 in v.integers


def test_float():
    v = extract("fare above 12.5")
    assert any(abs(x - 12.5) < 1e-9 for x in v.numbers)


def test_top_n_is_separated_from_filters():
    v = extract("top 5 clients by revenue")
    assert v.top_n == 5
    # '5' must not also leak into integer filters.
    assert 5 not in v.integers


def test_limit_modifier():
    v = extract("list employees limit 10")
    assert v.limit == 10
    assert 10 not in v.integers


def test_boolean_active():
    v = extract("list active employees")
    assert True in v.booleans


def test_boolean_inactive():
    v = extract("list disabled clients")
    assert False in v.booleans


def test_date_words():
    v = extract("bookings from today")
    assert v.dates and v.dates[0] == date.today()
