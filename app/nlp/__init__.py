"""Lightweight NLP primitives — tokenize, lemmatize, match, classify.

Deliberately avoids heavyweight deps (no spacy, no torch). Uses NLTK's
WordNet + stopwords, scikit-learn TF-IDF, and rapidfuzz for fuzzy match.
"""
