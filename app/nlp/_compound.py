"""
Compound-word splitter for no-separator identifiers like `onlineorders`.

Most schemas use camelCase or snake_case. The matcher handles both fine.
But occasionally a schema has table names like `onlineorders` —
all-lowercase, no boundary marker. Without help, the matcher would have
to fuzzy-match the entire string, which fails on partial-word queries.

This module provides a small curated prefix list. We deliberately don't
use a general English dictionary (e.g. WordNet) because that would
mis-split things like `auxproduct` → `aux + product` or `supplier` →
`sup + plier`. Curation is cheap and avoids surprise failures.
"""

from __future__ import annotations


# Length-ordered (longest first) so prefix matching is greedy. The
# remainder must be at least 3 chars to qualify as a real word.
COMPOUND_PREFIXES: tuple[str, ...] = (
    "electron",
    "notification",
    "registration",
    "configuration",
    "online",
    "offline",
    "vehicle",
    "product",
    "invoice",
    "vendor",
    "client",
    "driver",
    "global",
    "system",
    "config",
    "order",
    "tour",
    "duty",
    "user",
    "auto",
    "sub",
    "tax",
)


def split_compound(word: str) -> tuple[str, ...]:
    """Try to split an all-lowercase no-separator identifier into parts.

    Returns a tuple of split parts, or `(word,)` if no split applies.
    Conservative: only fires on known prefixes from COMPOUND_PREFIXES,
    and only when the remainder is at least 3 chars.

    Examples:
      onlineorders -> ('online', 'orders')
      taxcategories -> ('tax', 'categories')
      auxproduct -> ('auxproduct',)        # 'aux' not in prefix list
      supplier -> ('supplier',)            # too short to consider
    """
    if not word or not word.islower() or len(word) < 6:
        return (word,)
    for prefix in COMPOUND_PREFIXES:
        if word.startswith(prefix) and len(word) - len(prefix) >= 3:
            return (prefix, word[len(prefix):])
    return (word,)
