"""
Backwards-compat shim — the real acronym tables live in
`app/nlp/_acronyms.py` so the runtime can use them at /api/databases
time without depending on this dev-only directory.

Existing tools (auto_annotate.py, etc.) keep working unchanged.
"""

from app.nlp._acronyms import (  # noqa: F401
    ACRONYM_SYNONYMS,
    WORD_SYNONYMS,
    acronym_lookup,
    word_lookup,
)
