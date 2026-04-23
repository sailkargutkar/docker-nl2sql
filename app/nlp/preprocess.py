"""
Preprocessing: tokenize, lemmatize, split off quoted literals.

We keep the original casing and the lowercased lemma side-by-side. The
matcher compares lemmas; the value extractor uses originals for case-sensitive
literals.

NLTK is initialized lazily from the bundled corpora at NLTK_DATA_PATH so the
first request isn't blocked on downloads.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from functools import lru_cache


# NLTK looks in NLTK_DATA env var too, but we also explicitly seed its path
# from settings so the container's baked-in corpora are found even if the
# environment isn't set.
def _bootstrap_nltk() -> None:
    from .. import config  # local to avoid circular

    path = config.settings.nltk_data_path
    if path and os.path.isdir(path):
        import nltk

        if path not in nltk.data.path:
            nltk.data.path.insert(0, path)


_bootstrap_nltk()


_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
# Single- and double-quoted literals survive tokenization as one token.
_QUOTED_RE = re.compile(r"'([^']*)'|\"([^\"]*)\"")


@dataclass
class Token:
    raw: str
    lemma: str
    is_quoted: bool = False


@dataclass
class Preprocessed:
    question: str
    tokens: list[Token]
    quoted_literals: list[str] = field(default_factory=list)

    def lemmas(self) -> list[str]:
        return [t.lemma for t in self.tokens if not t.is_quoted]

    def lemma_set(self) -> set[str]:
        return {t.lemma for t in self.tokens if not t.is_quoted}


@lru_cache(maxsize=1)
def _lemmatizer():
    from nltk.stem import WordNetLemmatizer

    return WordNetLemmatizer()


@lru_cache(maxsize=1)
def _stopwords() -> set[str]:
    # Not all stopwords are noise for SQL. Keep a few that matter
    # ("all", "each", "more", "most") out of the stoplist.
    from nltk.corpus import stopwords

    base = set(stopwords.words("english"))
    keep = {
        "all", "each", "more", "most", "less", "least", "no", "not",
        "between", "before", "after", "above", "below", "over", "under",
        "top", "bottom", "first", "last",
    }
    return base - keep


def _lemma(word: str) -> str:
    lem = _lemmatizer()
    # Try noun then verb — whichever yields a shorter form wins for our needs.
    n = lem.lemmatize(word, pos="n")
    v = lem.lemmatize(n, pos="v")
    return v


def preprocess(question: str) -> Preprocessed:
    quoted: list[str] = []
    for m in _QUOTED_RE.finditer(question):
        quoted.append(m.group(1) if m.group(1) is not None else m.group(2))

    # Remove the quoted sections before word tokenization so "John's" quoted
    # value doesn't leak into schema matching.
    cleaned = _QUOTED_RE.sub(" ", question)

    stop = _stopwords()
    tokens: list[Token] = []
    for raw in _TOKEN_RE.findall(cleaned):
        low = raw.lower()
        if low in stop:
            continue
        tokens.append(Token(raw=raw, lemma=_lemma(low)))

    for q in quoted:
        tokens.append(Token(raw=q, lemma=q.lower(), is_quoted=True))

    return Preprocessed(question=question, tokens=tokens, quoted_literals=quoted)
