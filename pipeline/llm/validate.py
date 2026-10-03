"""Citation validator for generated text (README Section 7, use 3).

Generated text is a list of sentences, each with the edge/claim IDs it cites.
A text is valid only if every sentence cites at least one ID, every cited ID was
in the model's input, no uncited IDs or PMIDs appear inline, and nothing looks
like dosing (hard rule 7). Pure functions, no I/O.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

INLINE_ID = re.compile(r"\b(?:e|c)_\d{6}\b")
INLINE_PMID = re.compile(r"\bPMID:?\s*(\d{5,9})\b", re.I)
DOSING = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|µg|ug|g|ml|mL|units?|IU)(?:/(?:kg|m2|m²|day|d))?\b"
    r"|\b(?:twice|once|three times)\s+(?:a|per)\s+day\b|\b(?:b\.?i\.?d|t\.?i\.?d|q\.?d)\b"
    r"|\bdos(?:e|es|ing|age)\b",
    re.I,
)


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)


def validate_sentences(sentences: list[dict], allowed_ids: set[str], allowed_pmids: set[str] = frozenset()) -> ValidationResult:
    """`sentences`: [{"text": str, "cites": [id, ...]}]."""
    errors = []
    if not sentences:
        errors.append("no sentences")
    for i, s in enumerate(sentences):
        text, cites = s.get("text", "").strip(), s.get("cites") or []
        if not text:
            errors.append(f"sentence {i}: empty")
            continue
        if not cites:
            errors.append(f"sentence {i}: no citation: {text[:80]!r}")
        for c in cites:
            if c not in allowed_ids:
                errors.append(f"sentence {i}: cites {c!r}, which is not in the input")
        for m in INLINE_ID.findall(text):
            if m not in allowed_ids:
                errors.append(f"sentence {i}: mentions {m!r}, which is not in the input")
        for m in INLINE_PMID.findall(text):
            if m not in allowed_pmids:
                errors.append(f"sentence {i}: mentions PMID {m}, which is not in the input")
        if DOSING.search(text):
            errors.append(f"sentence {i}: looks like dosing information")
    return ValidationResult(not errors, errors)


def render(sentences: list[dict]) -> str:
    """Plain text with bracketed citations, e.g. 'STAT1 GoF ... [e_000012, c_000004]'."""
    return " ".join(f"{s['text'].strip()} [{', '.join(s['cites'])}]" for s in sentences)
