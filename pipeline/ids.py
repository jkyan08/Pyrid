"""Identifier conventions: CURIE normalisation and the internal mechanism id.

Pure functions, no I/O. Everything here is about id FORMATS; no identifier value is hand-typed
anywhere in the pipeline (README hard rule 2).

:func:`normalize_curie` is applied once, at load time, so every node id and edge endpoint in the
evidence store has a single spelling (pipeline/models.py "ID conventions"):

* OBO-style underscore ids (``MONDO_…``, ``EFO_…``, ``Orphanet_…``, ``HP_…``, ``GO_…``,
  ``DOID_…``, any ``PREFIX_digits``) become colon CURIEs: ``MONDO_0000000`` -> ``MONDO:0000000``.
* Bare Ensembl gene ids become ``ensembl:ENSG…``.
* Ids that already contain ``:`` (``REACT:R-HSA-…``, ``NCBIGene:…``, ``HP:…``) are kept as is.
* Any other bare id gets ``namespace_hint`` as its prefix (hint ``CHEMBL`` + ``CHEMBL0`` ->
  ``CHEMBL:CHEMBL0``). Without a hint a bare id is an error, never a guess.
* The body is never lower-cased.

Mechanism nodes are internal: ``mech:<HGNC symbol>:<GoF|LoF|DN>[:somatic]`` (README section 4);
:func:`mechanism_id` and :func:`parse_mechanism_id` are the only places that spell the format.
"""

from __future__ import annotations

import re

__all__ = [
    "ENSEMBL_PREFIX",
    "MECHANISM_DIRECTIONS",
    "MECHANISM_PREFIX",
    "SOMATIC_SUFFIX",
    "mechanism_id",
    "normalize_curie",
    "parse_mechanism_id",
    "split_curie",
]

ENSEMBL_PREFIX = "ensembl"
MECHANISM_PREFIX = "mech"
SOMATIC_SUFFIX = "somatic"
MECHANISM_DIRECTIONS = ("GoF", "LoF", "DN")

# PREFIX_digits, the OBO/purl spelling the OptimusKG backbone uses for disease, phenotype and GO ids.
_OBO_UNDERSCORE = re.compile(r"^(?P<prefix>[A-Za-z][A-Za-z0-9]*)_(?P<local>\d+)$")
# Bare Ensembl gene id, optionally versioned (ENSG00000000000 / ENSG00000000000.1).
_ENSEMBL_GENE = re.compile(r"^ENSG\d+(?:\.\d+)?$")
_MECHANISM_ID = re.compile(
    rf"^{MECHANISM_PREFIX}:(?P<gene>[^:\s]+):(?P<direction>{'|'.join(MECHANISM_DIRECTIONS)})"
    rf"(?::(?P<somatic>{SOMATIC_SUFFIX}))?$"
)


# --------------------------------------------------------------------------------------
# CURIEs
# --------------------------------------------------------------------------------------


def normalize_curie(raw: str, namespace_hint: str | None = None) -> str:
    """One spelling for an identifier (see the module docstring for the rules).

    Raises ``ValueError`` for an empty id or a bare id with no recognisable format and no
    ``namespace_hint``; ``TypeError`` when ``raw`` is not a string.
    """
    if not isinstance(raw, str):
        raise TypeError(f"identifier must be a str, got {type(raw).__name__}")
    value = raw.strip()
    if not value:
        raise ValueError("identifier is empty")
    if ":" in value:
        return value
    obo = _OBO_UNDERSCORE.match(value)
    if obo is not None:
        return f"{obo['prefix']}:{obo['local']}"
    if _ENSEMBL_GENE.match(value):
        return f"{ENSEMBL_PREFIX}:{value}"
    if namespace_hint is not None:
        hint = namespace_hint.strip().rstrip(":")
        if not hint:
            raise ValueError(f"namespace hint for {value!r} is empty")
        return f"{hint}:{value}"
    raise ValueError(f"cannot normalise bare id {value!r} without a namespace hint")


def split_curie(curie: str) -> tuple[str, str]:
    """``("MONDO", "0000000")`` for ``"MONDO:0000000"``; splits on the first colon only."""
    prefix, sep, local = curie.partition(":")
    if not sep or not prefix or not local:
        raise ValueError(f"not a CURIE (expected PREFIX:local): {curie!r}")
    return prefix, local


# --------------------------------------------------------------------------------------
# Mechanism ids
# --------------------------------------------------------------------------------------


def mechanism_id(gene_symbol: str, direction: str, somatic: bool = False) -> str:
    """``mech:<SYMBOL>:<GoF|LoF|DN>[:somatic]``. The symbol is kept exactly as given."""
    symbol = gene_symbol.strip() if isinstance(gene_symbol, str) else ""
    if not symbol or ":" in symbol or any(ch.isspace() for ch in symbol):
        raise ValueError(f"gene symbol must be a non-empty token without ':' or whitespace, got {gene_symbol!r}")
    if direction not in MECHANISM_DIRECTIONS:
        raise ValueError(f"direction must be one of {MECHANISM_DIRECTIONS}, got {direction!r}")
    suffix = f":{SOMATIC_SUFFIX}" if somatic else ""
    return f"{MECHANISM_PREFIX}:{symbol}:{direction}{suffix}"


def parse_mechanism_id(node_id: str) -> tuple[str, str, bool] | None:
    """``(gene_symbol, direction, somatic)`` for a ``mech:`` id, else ``None``."""
    if not isinstance(node_id, str):
        return None
    m = _MECHANISM_ID.match(node_id)
    if m is None:
        return None
    return m["gene"], m["direction"], m["somatic"] is not None
