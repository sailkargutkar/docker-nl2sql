"""
Versioned prompt templates.

Bump TEMPLATE_VERSION when changing any prompt — that invalidates the
on-disk cache so old responses don't get reused with new instructions.
"""

from __future__ import annotations

# Bump when ANY prompt below changes. Cache keys include this number.
TEMPLATE_VERSION = 1


# ---------- annotate_schema ----------

ANNOTATE_SYSTEM = (
    "You are a database schema annotation assistant. "
    "Given table and column metadata, you produce concise, accurate descriptions "
    "and synonyms suitable for natural-language querying.\n\n"
    "Hard rules:\n"
    "- Output strict JSON in the format the user requests. No prose, no markdown.\n"
    "- Descriptions: ≤ 80 chars, factual, no marketing language, no speculation.\n"
    "- Synonyms: words a user might use INSTEAD of the column name. "
    "  Empty list if none. At most 5 per column.\n"
    "- Skip synonyms for ID columns, timestamps, and bookkeeping fields "
    "  (createdAt, updatedAt, revision, etc.). Use [].\n"
    "- A synonym MUST NOT collide with another column's name on the same table.\n"
    "- Lowercase synonyms unless they are proper nouns.\n"
    "- If uncertain, prefer empty synonyms over guessing.\n"
)


def annotate_user_prompt(tables_block: str) -> str:
    """Build the user-side prompt for a batch of tables.

    `tables_block` is the formatted schema slice — one table per stanza,
    columns and types listed, optional sample values.
    """
    return (
        "Annotate the following tables. Return JSON of the form:\n"
        "{\n"
        '  "tables": [\n'
        '    {\n'
        '      "name": "<table name exactly as given>",\n'
        '      "description": "<≤80 chars>",\n'
        '      "columns": [\n'
        '        {"name": "<col name exactly as given>", '
        '"description": "<≤80 chars>", '
        '"synonyms": ["...", "..."]}\n'
        "      ]\n"
        "    }\n"
        "  ]\n"
        "}\n\n"
        "Schema:\n"
        f"{tables_block}\n"
    )


def format_table_block(
    table_name: str,
    columns: list[dict],
    samples: dict[str, list[str]] | None = None,
) -> str:
    """Format one table as a compact block for the prompt."""
    lines = [f'Table "{table_name}":']
    lines.append("  Columns:")
    for c in columns:
        flags = []
        if c.get("pk"):
            flags.append("pk")
        if c.get("fk"):
            flags.append(f"fk→{c['fk']}")
        flag_s = f" [{', '.join(flags)}]" if flags else ""
        lines.append(f"    - {c['name']} ({c.get('type', 'unknown')}){flag_s}")
    if samples:
        lines.append("  Sample values (truncated):")
        for col_name, vals in samples.items():
            if not vals:
                continue
            shown = ", ".join(repr(v) for v in vals)
            lines.append(f"    {col_name}: {shown}")
    return "\n".join(lines)
