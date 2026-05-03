"""
Pydantic validators + sanity checks for LLM JSON output.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


_STOPWORDS = {
    "id", "the", "a", "an", "of", "to", "in", "on", "at", "by", "for",
    "and", "or", "is", "are", "was", "were", "be", "been", "being",
    "this", "that", "these", "those", "it", "its",
}


class AnnotatedColumn(BaseModel):
    name: str
    description: str = Field(default="", max_length=200)
    synonyms: list[str] = Field(default_factory=list)

    @field_validator("synonyms")
    @classmethod
    def _check_synonyms(cls, v: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for s in v[:8]:  # tolerate slightly over-cap output, slice first
            s = (s or "").strip().lower()
            if not s:
                continue
            if len(s) < 2:
                continue
            if s in _STOPWORDS:
                continue
            if s in seen:
                continue
            seen.add(s)
            cleaned.append(s)
            if len(cleaned) >= 5:
                break
        return cleaned


class AnnotatedTable(BaseModel):
    name: str
    description: str = Field(default="", max_length=200)
    columns: list[AnnotatedColumn] = Field(default_factory=list)


class AnnotateResponse(BaseModel):
    tables: list[AnnotatedTable]


def validate_annotation(payload: dict) -> AnnotateResponse:
    """Parse + validate an LLM response. Raises pydantic.ValidationError on bad shape."""
    return AnnotateResponse.model_validate(payload)


def cross_check_synonyms(
    annotated: AnnotateResponse,
    real_columns_by_table: dict[str, set[str]],
) -> list[str]:
    """Return a list of warning strings for synonyms that collide with
    other column names on the same table. The collisions are stripped
    from `annotated` in place.
    """
    warnings: list[str] = []
    for t in annotated.tables:
        real_cols_lc = {c.lower() for c in real_columns_by_table.get(t.name, set())}
        for col in t.columns:
            keep: list[str] = []
            for syn in col.synonyms:
                if syn in real_cols_lc and syn != col.name.lower():
                    warnings.append(
                        f"{t.name}.{col.name}: synonym '{syn}' collides with "
                        f"another column on this table — dropped."
                    )
                else:
                    keep.append(syn)
            col.synonyms = keep
    return warnings
