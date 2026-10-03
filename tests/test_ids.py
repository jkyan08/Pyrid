"""pipeline/ids.py: CURIE normalisation and mechanism ids. Pure functions, fully offline.

The identifiers below are FORMAT exemplars (zero-filled local parts, ``mock`` bodies), not
resolved records: nothing here is a real MONDO / HP / Ensembl / ChEMBL id (README hard rule 2).
Gene symbols are HGNC symbols, which seeds and tests may name.
"""

from __future__ import annotations

import pytest

from pipeline.ids import (
    ENSEMBL_PREFIX,
    MECHANISM_DIRECTIONS,
    mechanism_id,
    normalize_curie,
    parse_mechanism_id,
    split_curie,
)
from tests.conftest import mechanism_id as conftest_mechanism_id
from tests.conftest import parse_mechanism_id as conftest_parse_mechanism_id

# --------------------------------------------------------------------------------------
# normalize_curie
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("MONDO_0000000", "MONDO:0000000"),
        ("EFO_0000000", "EFO:0000000"),
        ("Orphanet_0", "Orphanet:0"),
        ("HP_0000000", "HP:0000000"),
        ("GO_0000000", "GO:0000000"),
        ("DOID_0", "DOID:0"),
        ("OBA_0000000", "OBA:0000000"),
        ("NCIT_C0", None),  # local part is not digits: not the OBO pattern, needs a hint
    ],
)
def test_obo_underscore_becomes_colon(raw: str, expected: str | None) -> None:
    if expected is None:
        with pytest.raises(ValueError):
            normalize_curie(raw)
        assert normalize_curie(raw, "mock") == f"mock:{raw}"
    else:
        assert normalize_curie(raw) == expected


def test_prefix_case_and_body_are_never_changed() -> None:
    assert normalize_curie("Orphanet_0") == "Orphanet:0"
    assert normalize_curie("mondo_0") == "mondo:0"  # not our job to fix a lower-case prefix
    assert normalize_curie("Mock-Body", "CHEMBL") == "CHEMBL:Mock-Body"


def test_bare_ensembl_gene_gets_the_ensembl_prefix() -> None:
    assert normalize_curie("ENSG00000000000") == f"{ENSEMBL_PREFIX}:ENSG00000000000"
    assert normalize_curie("ENSG00000000000.1") == f"{ENSEMBL_PREFIX}:ENSG00000000000.1"
    # The Ensembl rule wins over any hint.
    assert normalize_curie("ENSG00000000000", "CHEMBL") == f"{ENSEMBL_PREFIX}:ENSG00000000000"


@pytest.mark.parametrize(
    "curie",
    [
        "REACT:R-HSA-0",
        "NCBIGene:0",
        "HP:0000000",
        "MONDO:0000000",
        "mock:gene:1",
        "mech:STAT1:GoF",
        f"{ENSEMBL_PREFIX}:ENSG00000000000",
        "GO_REF:0000000",
    ],
)
def test_ids_with_a_colon_are_kept(curie: str) -> None:
    assert normalize_curie(curie) == curie
    assert normalize_curie(curie, "CHEMBL") == curie


def test_bare_id_takes_the_namespace_hint() -> None:
    assert normalize_curie("CHEMBL0", "CHEMBL") == "CHEMBL:CHEMBL0"
    assert normalize_curie("CHEMBL0", "CHEMBL:") == "CHEMBL:CHEMBL0"
    assert normalize_curie("CHEMBL0", " CHEMBL ") == "CHEMBL:CHEMBL0"
    assert normalize_curie("DB0", "drugbank") == "drugbank:DB0"


def test_bare_id_without_a_hint_is_an_error_not_a_guess() -> None:
    with pytest.raises(ValueError, match="namespace hint"):
        normalize_curie("CHEMBL0")
    with pytest.raises(ValueError):
        normalize_curie("CHEMBL0", "")
    with pytest.raises(ValueError):
        normalize_curie("CHEMBL0", ":")


def test_whitespace_is_stripped_and_blank_rejected() -> None:
    assert normalize_curie("  MONDO_0000000\n") == "MONDO:0000000"
    assert normalize_curie(" REACT:R-HSA-0 ") == "REACT:R-HSA-0"
    for blank in ("", "   ", "\t"):
        with pytest.raises(ValueError, match="empty"):
            normalize_curie(blank)


def test_non_string_is_a_type_error() -> None:
    with pytest.raises(TypeError):
        normalize_curie(123)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        normalize_curie(None)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("raw", "hint"),
    [("MONDO_0000000", None), ("ENSG00000000000", None), ("CHEMBL0", "CHEMBL"), ("REACT:R-HSA-0", None)],
)
def test_normalisation_is_idempotent(raw: str, hint: str | None) -> None:
    once = normalize_curie(raw, hint)
    assert normalize_curie(once, hint) == once
    assert normalize_curie(once) == once


# --------------------------------------------------------------------------------------
# split_curie
# --------------------------------------------------------------------------------------


def test_split_curie() -> None:
    assert split_curie("MONDO:0000000") == ("MONDO", "0000000")
    assert split_curie("REACT:R-HSA-0") == ("REACT", "R-HSA-0")
    assert split_curie("mech:STAT1:GoF") == ("mech", "STAT1:GoF")  # first colon only
    for bad in ("MONDO_0000000", ":0", "MONDO:", ""):
        with pytest.raises(ValueError):
            split_curie(bad)


# --------------------------------------------------------------------------------------
# mechanism ids
# --------------------------------------------------------------------------------------


def test_mechanism_directions_match_the_contract() -> None:
    assert MECHANISM_DIRECTIONS == ("GoF", "LoF", "DN")


@pytest.mark.parametrize("direction", MECHANISM_DIRECTIONS)
@pytest.mark.parametrize("somatic", [False, True])
def test_mechanism_id_format_and_roundtrip(direction: str, somatic: bool) -> None:
    mid = mechanism_id("STAT1", direction, somatic)
    assert mid == f"mech:STAT1:{direction}" + (":somatic" if somatic else "")
    assert parse_mechanism_id(mid) == ("STAT1", direction, somatic)
    # Ids are already in normalised form (they contain a colon).
    assert normalize_curie(mid) == mid


def test_mechanism_id_agrees_with_the_test_helpers_convention() -> None:
    """tests/conftest.py spells the same convention; the two must never drift apart."""
    for direction in MECHANISM_DIRECTIONS:
        for somatic in (False, True):
            ours = mechanism_id("JAK2", direction, somatic)
            assert ours == conftest_mechanism_id("JAK2", direction, somatic)
            assert parse_mechanism_id(ours) == conftest_parse_mechanism_id(ours)


def test_mechanism_id_keeps_symbol_case_and_strips_whitespace() -> None:
    assert mechanism_id("C1orf0", "LoF") == "mech:C1orf0:LoF"
    assert mechanism_id(" TYK2 ", "LoF") == "mech:TYK2:LoF"


@pytest.mark.parametrize("bad_symbol", ["", "   ", "STAT 1", "STAT:1", "ST\tAT1"])
def test_mechanism_id_rejects_bad_symbols(bad_symbol: str) -> None:
    with pytest.raises(ValueError, match="gene symbol"):
        mechanism_id(bad_symbol, "GoF")


@pytest.mark.parametrize("bad_direction", ["gof", "GOF", "gain", "LOF", "dn", "", "somatic"])
def test_mechanism_id_rejects_bad_directions(bad_direction: str) -> None:
    with pytest.raises(ValueError, match="direction"):
        mechanism_id("STAT1", bad_direction)


@pytest.mark.parametrize(
    "node_id",
    [
        "mock:gene:1",
        "MONDO:0000000",
        "mech:STAT1",
        "mech:STAT1:gof",
        "mech:STAT1:GoF:germline",
        "mech:STAT1:GoF:Somatic",
        "mech:STAT1:GoF:somatic:extra",
        "mech::GoF",
        "mech:STAT 1:GoF",
        "xmech:STAT1:GoF",
        "",
    ],
)
def test_parse_mechanism_id_returns_none_for_non_mechanism_ids(node_id: str) -> None:
    assert parse_mechanism_id(node_id) is None


def test_parse_mechanism_id_tolerates_non_strings() -> None:
    assert parse_mechanism_id(None) is None  # type: ignore[arg-type]
    assert parse_mechanism_id(42) is None  # type: ignore[arg-type]
