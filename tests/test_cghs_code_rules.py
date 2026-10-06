"""CGHS code identification, normalisation and aggregation (task sections 2-28).

The defect this file pins down: the baseline resolved a code by building
candidates as ``category + token`` and accepting the first one that happened
to exist in ``VALID_CODES``.  Because ``_build_valid_codes`` generates whole
families as wildcards (CC001..CC100, C001..C100, ...), *every* invented
``CCxxx`` validated.  That is a blanket ``Cxxx -> CCxxx`` rule with no
evidence behind it:

    "VENTILATOR CGHS-C C003"              -> CC003   (a code nobody defined)
    "UNRELATED CGHS-C C004"               -> CC004   (no NIV Machine on the row)
    "BLOOD BANK PACKED CELLS CGHS-C C002" -> CC002   (not the Oxygen code)
    "Room Rent( CGHS-RI ) ICU 1 4500.00"  -> RI145   (manufactured from rupees)

Each test asserts the resolved codes and, where the rule is about safety, the
review record that explains the refusal - never a return value alone.
"""

from __future__ import annotations

import pytest

from cghs import parsing, rules
from cghs.locators import (
    CGHS_CODE_FAMILIES,
    CGHS_CODE_REGISTRY,
    PORTAL_OPTION_MAP,
    VALID_CODES,
    cghs_record,
)
from cghs.rules import EXECUTABLE, REVIEW_REQUIRED, resolve_cghs_codes


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def codes(row_text):
    """The EXECUTABLE final codes for a row, in order."""
    return [code for code, _ in rules.normalize_cghs_code(row_text)]


def review(row_text):
    """The raw tokens the resolver refused to execute."""
    return [o["original_token"] for o in resolve_cghs_codes(row_text)
            if o["status"] == REVIEW_REQUIRED]


def reasons(row_text):
    return " | ".join(o["normalization_reason"]
                      for o in resolve_cghs_codes(row_text))


def _blocks(lines):
    """PyMuPDF-shaped ``get_text("blocks")`` tuples for one page."""
    return [(10.0, 20.0 * i, 500.0, 20.0 * i + 15.0, text, i, 0)
            for i, text in enumerate(lines)]


def _parse(lines, full_text=None):
    pages = [_blocks(lines)]
    text = full_text if full_text is not None else "\n".join(lines)
    return parsing.CGHSParsingEngine().parse_document(pages, text, "<test>")


# ---------------------------------------------------------------------------
# section 3: locked category composition
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("row,expected", [
    ("LIPID PROFILE CGHS-LB012", "LB012"),
    ("CHEST XRAY CGHS-RI034", "RI034"),
    ("ECHO CGHS-CI005", "CI005"),
    ("PROCEDURE CGHS-GP009", "GP009"),
    ("PHYSIOTHERAPY CGHS-PT004", "PT004"),
])
def test_direct_codes_resolve_to_themselves(row, expected):
    assert codes(row) == [expected]


@pytest.mark.parametrize("row,expected", [
    ("LIPID PROFILE CGHS-L B012", "LB012"),     # CGHS-L  + Bxxx -> LBxxx
    ("CHEST XRAY CGHS-RI 034", "RI034"),        # CGHS-RI + num  -> RIxxx
    ("ECHO CGHS-CI 005", "CI005"),              # CGHS-CI + num  -> CIxxx
    ("PROCEDURE CGHS-G P009", "GP009"),         # CGHS-G  + Pxxx -> GPxxx
    ("PHYSIOTHERAPY CGHS-P T004", "PT004"),     # CGHS-P  + Txxx -> PTxxx
    ("CONSULTATION CGHS-C N002", "CN002"),      # CGHS-C  + Nxxx -> CNxxx
])
def test_locked_category_composition(row, expected):
    assert codes(row) == [expected]


def test_category_composition_is_a_table_not_concatenation():
    """C + C is deliberately absent: that pair is the blanket rule."""
    assert ("C", "C") not in rules.LOCKED_CATEGORY_COMPOSITION
    assert rules.LOCKED_CATEGORY_COMPOSITION[("C", "N")] == "CN"


def test_composition_still_validates_against_the_registry():
    """A well-formed composition that is not a real code is not executable."""
    assert "LB999" not in VALID_CODES
    assert codes("SOMETHING CGHS-L B999") == []
    assert review("SOMETHING CGHS-L B999") == ["B999"]


# ---------------------------------------------------------------------------
# section 4: context-sensitive locked mappings
# ---------------------------------------------------------------------------

LOCKED_CONTEXT = [
    ("C004", "CC004", "NIV MACHINE PER DAY"),
    ("C008", "CC008", "BLOOD TRANSFUSION CHARGE"),
    ("C010", "CC010", "ENDOTRACHEAL INTUBATION"),
    ("C011", "CC011", "CENTRAL LINE"),
    ("C012", "CC012", "NEBULIZER THERAPY"),
    ("C014", "CC014", "RYLES TUBE INSERTION CHARGE"),
]


@pytest.mark.parametrize("alias,final,context", LOCKED_CONTEXT)
def test_locked_mapping_fires_with_same_row_evidence(alias, final, context):
    assert codes(f"{context} CGHS-C {alias} 1 100.00 100.00") == [final]


@pytest.mark.parametrize("alias,final,context", LOCKED_CONTEXT)
def test_locked_mapping_refuses_without_same_row_evidence(alias, final, context):
    """The baseline resolved these identically with or without the service."""
    row = f"UNRELATED SERVICE CGHS-C {alias} 1 100.00 100.00"
    assert codes(row) == [], f"{alias} resolved with no {context} evidence"
    assert review(row) == [alias]
    assert "UNRESOLVED_MAPPING" in reasons(row)


@pytest.mark.parametrize("alias,final,context", LOCKED_CONTEXT)
def test_locked_mapping_names_the_evidence_it_wanted(alias, final, context):
    row = f"UNRELATED SERVICE CGHS-C {alias}"
    assert final in reasons(row), "the refusal must say what it would become"


def test_no_blanket_c_to_cc_expansion_exists():
    """Every Cxxx without a locked rule stays unresolved - all 100 of them."""
    locked = set(rules.LOCKED_CONTEXT_MAPPINGS) | {"C002"}
    escaped = []
    for n in range(1, 101):
        alias = f"C{n:03d}"
        if alias in locked:
            continue
        produced = codes(f"GENERIC SERVICE CGHS-C {alias}")
        if produced:
            escaped.append((alias, produced))
    assert escaped == [], f"blanket expansion still reachable: {escaped}"


# ---------------------------------------------------------------------------
# section 5: C002 is Oxygen, and only Oxygen
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("row,qty", [
    ("OXYGEN FULL DAY CGHS-C C002", 24),
    ("OXYGEN HALF DAY CGHS-C C002", 12),
    ("OXYGEN CGHS-C C002", 1),
])
def test_c002_oxygen_quantity_is_locked(row, qty):
    occurrences = [o for o in resolve_cghs_codes(row) if o["status"] == EXECUTABLE]
    assert [o["final_code"] for o in occurrences] == ["CC002"]
    assert occurrences[0]["quantity"] == qty


@pytest.mark.parametrize("row", [
    "BLOOD BANK PACKED CELLS CGHS-C C002",
    "PACKED CELLS 2 UNITS CGHS-C C002",
    "BLOOD BANK CHARGES CGHS-C C002",
])
def test_c002_in_blood_bank_context_is_not_cc002(row):
    assert codes(row) == []
    assert review(row) == ["C002"]
    assert "Blood Bank" in reasons(row)


def test_c002_without_oxygen_evidence_is_not_cc002():
    row = "SOME OTHER SERVICE CGHS-C C002"
    assert codes(row) == []
    assert review(row) == ["C002"]


def test_oxygen_quantity_survives_into_the_parsed_document():
    final, raw, _name, _rejected, _log = _parse([
        "Equipment(999311)",
        "OXYGEN FULL DAY CGHS-C C002 1 150.00 150.00",
    ])
    cc002 = [i for i in final if i["code"] == "CC002"]
    assert cc002 and cc002[0]["qty"] == 24, final


# ---------------------------------------------------------------------------
# section 6: C003 is never invented
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("context", ["VENTILATOR", "FRESH FROZEN PLASMA"])
def test_c003_is_always_review_required(context):
    row = f"{context} CGHS-C C003 1 500.00 500.00"
    assert codes(row) == []
    assert review(row) == ["C003"]
    assert "UNRESOLVED_MAPPING" in reasons(row)


@pytest.mark.parametrize("context", ["VENTILATOR", "FRESH FROZEN PLASMA", "ANY ROW"])
def test_cc003_is_never_produced(context):
    assert "CC003" not in codes(f"{context} CGHS-C C003")


def test_c003_review_reaches_the_operator_not_the_portal():
    final, _raw, _name, rejected, _log = _parse([
        "ICU(999311)",
        "VENTILATOR CGHS-C C003 1 500.00 500.00",
    ])
    assert [i["code"] for i in final] == []
    assert any(r.get("status") == REVIEW_REQUIRED and r.get("code") == "C003"
               for r in rejected), rejected


# ---------------------------------------------------------------------------
# section 7: derived codes keep their locked derivation
# ---------------------------------------------------------------------------

ROOM_RENT = [
    "Room Rent(999311)",
    "Room Rent( CGHS-RI ) ICU 1 4500.00 4500.00",
    "Room Rent( CGHS-RI ) ICU 1 4500.00 4500.00",
    "Room Rent( CGHS-RI ) ICU 1 4500.00 4500.00",
    "Room Rent( CGHS-RI ) AC MULTIBEDS 1 2000.00 2000.00",
    "Room Rent( CGHS-RI ) SINGLE 1 2500.00 2500.00",
    "Sub Total 18000.00",
]


def test_cc001_wc001_cn002_come_from_room_rent_row_counts():
    final, _raw, _name, _rejected, _log = _parse(ROOM_RENT)
    got = {i["code"]: i["qty"] for i in final}
    assert got.get("CC001") == 3, "CC001 = ICU row COUNT"
    assert got.get("WC001") == 2, "WC001 = qualifying Ward row COUNT"
    assert got.get("CN002") == 3 * 3 + 2 * 2, "CN002 = ICU*3 + Ward*2"


def test_a_room_rent_amount_never_becomes_a_code():
    """'1 4500.00' used to be welded into '14500.00' and yield RI145."""
    row = "Room Rent( CGHS-RI ) ICU 1 4500.00 4500.00"
    assert codes(row) == []
    assert "RI145" not in codes(row)

    final, _raw, _name, _rejected, _log = _parse(ROOM_RENT)
    assert not [i for i in final if i["code"].startswith("RI")], final


def test_raw_consultation_cn002_cannot_override_the_derivation():
    final, _raw, _name, rejected, _log = _parse(ROOM_RENT + [
        "Consultation(999311)",
        "CONSULTATION CGHS-C CN002 6 300.00 1800.00",
    ])
    got = {i["code"]: i["qty"] for i in final}
    assert got["CN002"] == 13, "the Room Rent derivation must win"
    assert any("CN002" in str(r.get("reason", "")).upper() for r in rejected)


def test_raw_n002_in_a_service_row_is_not_authoritative():
    final, _raw, _name, _rejected, _log = _parse(ROOM_RENT + [
        "Consultation(999311)",
        "CONSULTATION CGHS-C N002 6 300.00 1800.00",
    ])
    assert {i["code"]: i["qty"] for i in final}["CN002"] == 13


def test_cc001_from_a_cghs_row_is_rejected_outside_room_rent():
    final, _raw, _name, rejected, _log = _parse([
        "ICU(999311)",
        "ICU CHARGES CGHS-CI CC001 3 1000.00 3000.00",
    ])
    assert [i["code"] for i in final] == []
    assert any(r.get("code") == "CC001" for r in rejected)


# ---------------------------------------------------------------------------
# section 8: Patient Payable stays out of the enhancement aggregation
# ---------------------------------------------------------------------------

def test_patient_payable_is_not_an_enhancement_contribution():
    text = "\n".join([
        "IP Pharmacy(999311)",
        "1  CGHS TAB PARACETAMOL  IPP01  10  12.00  120.00",
        "Dept Sub Total : 1,250.00",
        "",
        "Patient Payable(999311)",
        "1  NON CGHS ITEM  XX001  1  999.00  999.00",
        "Dept Sub Total : 999.00",
    ])
    final, raw, _name, _rejected, _log = _parse(
        ["Pharmacy(999311)"], full_text=text)
    assert all(i["code"] != "PP999" for i in final)
    drug = [i for i in final if i["code"] == "DRUG100"]
    assert drug and drug[0]["amount"] == pytest.approx(1250.00), (
        "Patient Payable must not be added to DRUG100")


def test_patient_payable_subtotal_is_never_read_as_a_department():
    assert rules.extract_dept_subtotal(
        "Patient Payable\nDept Sub Total : 999.00\n", r'Patient\s*Payable') == 0.0


# ---------------------------------------------------------------------------
# sections 9-10: amounts are not quantities
# ---------------------------------------------------------------------------

def test_amount_based_codes_keep_amount_semantics():
    text = "\n".join([
        "IP Pharmacy(999311)",
        "1  CGHS TAB PARACETAMOL  IPP01  10  12.00  120.00",
        "Dept Sub Total : 1,250.00",
        "",
        "OT Pharmacy(999311)",
        "1  CGHS INJ PROPOFOL  OTP01  2  223.85  447.70",
        "Dept Sub Total : 447.70",
    ])
    final, _raw, _name, _rejected, _log = _parse(
        ["Pharmacy(999311)"], full_text=text)
    drug = [i for i in final if i["code"] == "DRUG100"]
    assert drug, final
    assert drug[0]["qty"] == 1, "an amount must never become a unit quantity"
    assert drug[0]["amount"] == pytest.approx(1250.00 + 447.70)


def test_explicit_row_quantity_is_preserved():
    final, _raw, _name, _rejected, _log = _parse([
        "Laboratory(999311)",
        # real bill column shape: qty  ref  amount
        "LIPID PROFILE CGHS-LB012 4.00 5433 2000.00",
    ])
    lb = [i for i in final if i["code"] == "LB012"]
    assert lb and lb[0]["qty"] == 4, final


# ---------------------------------------------------------------------------
# sections 13-16, 26: compound '+' expressions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("row,expected", [
    ("CGHS-LB269+LB270-2025", ["LB269", "LB270"]),
    ("CGHS-LB068+LB075+LB126-2025", ["LB068", "LB075", "LB126"]),
])
def test_compound_expressions_split_into_independent_codes(row, expected):
    assert codes(f"LAB PANEL {row} 1 100.00 100.00") == expected


def test_the_plus_is_never_retained_inside_a_final_code():
    for code in codes("LAB PANEL CGHS-LB269+LB270-2025 1 100.00 100.00"):
        assert "+" not in code


@pytest.mark.parametrize("row", [
    "CGHS-LB269+LB270-2025", "CGHS-LB012-2025", "CGHS-RI034-2024",
])
def test_the_year_suffix_is_never_retained_inside_a_final_code(row):
    produced = codes(f"SERVICE {row} 1 100.00 100.00")
    assert produced, row
    for code in produced:
        assert "-" not in code and "20" not in code[2:]


def test_a_compound_component_is_a_real_registry_code():
    for code in codes("LAB PANEL CGHS-LB068+LB075+LB126-2025"):
        assert code in VALID_CODES


# ---------------------------------------------------------------------------
# section 17: prefix carry across '+' is ambiguous
# ---------------------------------------------------------------------------

def test_prefix_carry_is_not_assumed():
    row = "LAB PANEL CGHS-LB269+270-2025"
    assert codes(row) == ["LB269"], "LB270 must not be invented"
    assert review(row) == ["270"]
    assert "prefix carry" in reasons(row)


def test_an_ambiguous_component_does_not_remove_the_unambiguous_one():
    """Safety over recall, but never at the cost of a code that IS explicit."""
    assert codes("LAB PANEL CGHS-LB269+270") == ["LB269"]


# ---------------------------------------------------------------------------
# section 27: a compound must not contaminate across components
# ---------------------------------------------------------------------------

def test_components_resolve_independently():
    row = "OXYGEN FULL DAY CGHS-C C002+C003"
    assert codes(row) == ["CC002"], "the resolvable component stays executable"
    assert review(row) == ["C003"], "the unresolvable one is reported"


def test_an_unresolved_component_is_not_silently_dropped():
    occurrences = resolve_cghs_codes("OXYGEN CGHS-C C002+C003")
    assert len(occurrences) == 2
    assert {o["status"] for o in occurrences} == {EXECUTABLE, REVIEW_REQUIRED}


# ---------------------------------------------------------------------------
# sections 19, 20, 28: aggregate only AFTER resolution
# ---------------------------------------------------------------------------

def test_identical_final_codes_aggregate_across_rows():
    final, _raw, _name, _rejected, _log = _parse([
        "Laboratory(999311)",
        "PANEL CGHS-LB269+LB270-2025 1 100.00 100.00",
        "REPEAT CGHS-LB269-2025 1 100.00 100.00",
    ])
    got = {i["code"]: i["qty"] for i in final}
    assert got.get("LB269") == 2, "two independent occurrences aggregate"
    assert got.get("LB270") == 1, "the other component stays separate"
    assert "LB269+LB270" not in got


def test_the_same_alias_in_two_contexts_is_never_merged_on_the_raw_token():
    """C002 means Oxygen in one row and something else in a Blood Bank row."""
    final, _raw, _name, rejected, _log = _parse([
        "Equipment(999311)",
        "OXYGEN FULL DAY CGHS-C C002 1 150.00 150.00",
        "Blood Bank(999311)",
        "BLOOD BANK PACKED CELLS CGHS-C C002 2 1000.00 2000.00",
    ])
    got = {i["code"]: i["qty"] for i in final}
    assert got.get("CC002") == 24, "only the Oxygen row contributes"
    assert any(r.get("code") == "C002" and r.get("status") == REVIEW_REQUIRED
               for r in rejected), rejected


# ---------------------------------------------------------------------------
# sections 21-22: provenance and raw alias safety
# ---------------------------------------------------------------------------

PROVENANCE_FIELDS = ["raw_expression", "original_token", "final_code",
                     "normalization_reason", "quantity", "page", "section",
                     "service_name", "source_row_text"]


@pytest.mark.parametrize("field", PROVENANCE_FIELDS)
def test_every_occurrence_carries_its_provenance(field):
    occurrences = resolve_cghs_codes(
        "LAB PANEL CGHS-LB269+LB270-2025 1 100.00 100.00",
        page=7, section="Laboratory", service_name="LAB PANEL")
    assert occurrences
    for occ in occurrences:
        assert field in occ, f"{field} missing from {occ}"


def test_split_components_record_their_parent_expression():
    occurrences = resolve_cghs_codes("LAB PANEL CGHS-LB068+LB075+LB126-2025")
    assert len(occurrences) == 3
    for index, occ in enumerate(occurrences):
        assert occ["parent_expression"] == "LB068+LB075+LB126"
        assert occ["component_index"] == index
        assert occ["component_count"] == 3


def test_a_single_code_is_not_labelled_as_a_component():
    occ = resolve_cghs_codes("LIPID PROFILE CGHS-LB012")[0]
    assert "component_index" not in occ
    assert occ["raw_expression"] == "LB012"


@pytest.mark.parametrize("alias", ["C002", "C003", "C008", "C010", "C011",
                                   "C012", "C014", "N002"])
def test_raw_aliases_are_never_executable_as_themselves(alias):
    produced = codes(f"GENERIC ROW CGHS-C {alias} 1 100.00 100.00")
    assert alias not in produced, f"{alias} escaped as an executable code"


# ---------------------------------------------------------------------------
# sections 11-12, 23: registry discipline
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("prefix", ["WC", "ST", "OT"])
def test_unproven_families_are_not_wildcarded_into_the_registry(prefix):
    """Section 23: a prefix seen once is not a licence to invent a family.

    WC/ST/OT were each a full 100-code family in the synthetic registry.  The
    CGHS master list defines not one of them, so the registry must hold none.
    """
    present = [c for c in VALID_CODES if c.startswith(prefix)]
    assert present == [], f"{prefix} family added without evidence: {present[:5]}"


@pytest.mark.parametrize("prefix,expected", [
    ("BC", ["BC001", "BC002", "BC003", "BC004", "BC005", "BC006"]),
    ("CT", ["CT001", "CT002", "CT003", "CT004", "CT005", "CT006"]),
    ("HC", ["HC001", "HC002"]),
])
def test_a_proven_family_holds_exactly_the_master_codes(prefix, expected):
    """BC/CT/HC are real families - but only for the codes the master lists.

    The synthetic registry held neither; the master list proves BC001-006,
    CT001-006 and HC001-002 and nothing beyond them.  A family being real is
    not a licence to extend it, so the contents are pinned exactly.
    """
    assert sorted(c for c in VALID_CODES if c.startswith(prefix)) == expected


@pytest.mark.parametrize("token", ["BC002", "CT001"])
def test_an_unregistered_token_is_preserved_for_review_not_manufactured(token):
    row = f"SERVICE CGHS-{token} 1 100.00 100.00"
    assert codes(row) == [], f"{token} was manufactured"
    assert review(row), f"{token} was silently dropped instead of reported"


@pytest.mark.parametrize("description", ["BLOOD", "LABORATORY", "RADIOLOGY",
                                         "CARDIOLOGY", "CHEMOTHERAPY"])
def test_a_description_alone_never_invents_a_code(description):
    assert codes(f"{description} SERVICE CGHS- 1 100.00 100.00") == []


def test_a_row_with_no_cghs_marker_produces_nothing():
    assert resolve_cghs_codes("LIPID PROFILE 1 500.00 500.00") == []


# ---------------------------------------------------------------------------
# section 31: the resolver stays inside the existing single pass
# ---------------------------------------------------------------------------

def test_resolution_does_not_rescan_the_document():
    """One call per row, no document-wide rescan hidden inside the resolver."""
    calls = {"n": 0}
    original = rules.resolve_cghs_codes

    def counting(row_text, **kwargs):
        calls["n"] += 1
        return original(row_text, **kwargs)

    parsing.resolve_cghs_codes = counting
    try:
        rows = ["Laboratory(999311)"] + [
            f"TEST CGHS-LB{i:03d} 1 100.00 100.00" for i in range(1, 21)]
        _parse(rows)
    finally:
        parsing.resolve_cghs_codes = original

    assert calls["n"] <= len(rows), (
        f"{calls['n']} resolver calls for {len(rows)} rows - the parser is "
        f"rescanning")


def test_resolver_is_pure_and_repeatable():
    row = "LAB PANEL CGHS-LB269+LB270-2025 1 100.00 100.00"
    assert resolve_cghs_codes(row) == resolve_cghs_codes(row)


# ---------------------------------------------------------------------------
# CGHS MASTER REGISTRY - the 1998-record list is the only source of code identity
#
# The registry used to be generated: ``for i in range(1, 400): LB{i:03d}`` and
# friends produced 1802 codes, most of which no CGHS document ever defined,
# while real codes were missing entirely.  It is now the operator-supplied
# master list committed to this repository as
# ``CGHS_1998_CODE_TREATMENT_NABH_SPECIALITY (1).txt``
# (164739 bytes, sha256 b3ad063160bb8e9776769e528266518600939cae3f5eec236cedc62f796d0f80).
# ---------------------------------------------------------------------------

def test_the_registry_holds_exactly_the_1998_master_records():
    """Section 20: the whole master list was consumed, nothing added."""
    assert len(CGHS_CODE_REGISTRY) == 1998
    assert len(VALID_CODES) == 1998


def test_valid_codes_is_derived_from_the_registry_not_a_second_list():
    """There is ONE registry.  VALID_CODES is its key set, not a copy."""
    assert VALID_CODES == frozenset(CGHS_CODE_REGISTRY)
    assert isinstance(VALID_CODES, frozenset)


def test_every_record_satisfies_the_section_20_assertions():
    """Section 20: the acceptance gate, asserted against the shipped data.

    record_count / sr_no range / missing / duplicate / blank / numeric-rate.
    A failure here means the integration shipped a repaired or lossy record.
    """
    records = list(CGHS_CODE_REGISTRY.values())
    assert len(records) == 1998

    sr_numbers = sorted(r.sr_no for r in records)
    assert sr_numbers[0] == 1
    assert sr_numbers[-1] == 1998
    assert sr_numbers == list(range(1, 1999)), "missing or duplicate Sr No"

    assert len({r.code for r in records}) == 1998, "duplicate CGHS code"

    for r in records:
        assert r.code.strip(), f"blank code at Sr {r.sr_no}"
        assert r.description.strip(), f"blank description at Sr {r.sr_no}"
        assert r.nabh_rate.strip(), f"blank NABH rate at Sr {r.sr_no}"
        assert r.speciality.strip(), f"blank speciality at Sr {r.sr_no}"
        assert r.nabh_rate.replace(".", "", 1).isdigit(), (
            f"non-numeric NABH rate {r.nabh_rate!r} at Sr {r.sr_no}")


def test_sr_no_is_metadata_and_never_the_code():
    """Section 5: Sr 1998 is HC002 - the serial number is not an identity."""
    assert cghs_record("HC002").sr_no == 1998
    assert "1998" not in VALID_CODES
    assert "1" not in VALID_CODES
    # The old registry carried a bare numeric token; nothing numeric survives.
    assert not [c for c in VALID_CODES if c.isdigit()]


def test_hc002_the_last_master_record_is_valid():
    """It was INVALID before: no range() ever produced an HC family."""
    assert "HC002" in VALID_CODES
    record = cghs_record("HC002")
    assert record.code == "HC002"
    assert record.nabh_rate == "2200"
    assert record.speciality == "Annual Health Check-up"


@pytest.mark.parametrize("code", ["CC099", "LB399", "RI200", "CI100", "C001",
                                  "C002", "WC001", "PT100", "MG100", "OT001"])
def test_codes_the_generator_invented_are_no_longer_valid(code):
    """Section 4: every one of these was VALID purely because of range()."""
    assert code not in VALID_CODES
    assert cghs_record(code) is None


def test_no_family_is_a_contiguous_synthetic_range():
    """Section 4: no family may look like range(1, N) output.

    The generator emitted unbroken 1..100 / 1..200 / 1..399 runs.  A real
    family from the master list is far smaller than its highest number is
    large only by coincidence, so the tell-tale is an exact 1..N run of a
    size the generator used.
    """
    synthetic_sizes = {100, 200, 399}
    for family in CGHS_CODE_FAMILIES:
        members = sorted(c for c in VALID_CODES if c[:2] == family)
        if len(members) not in synthetic_sizes:
            continue
        numbers = [int(c[2:]) for c in members]
        assert numbers != list(range(1, len(members) + 1)), (
            f"{family} is still a contiguous synthetic range of "
            f"{len(members)} codes")


def test_metadata_travels_with_the_code(
):
    """Sections 6/16/17: description, NABH rate and speciality stay attached."""
    record = cghs_record("CC002")
    assert record.description == "Compressed Air / Piped Oxygen per hour"
    assert record.nabh_rate == "90"          # preserved exactly, never computed
    assert record.speciality == "Critical Care"   # authoritative, never guessed


def test_the_nabh_rate_is_the_source_string_not_a_number():
    """Section 17: the rate is preserved exactly - not rounded or re-typed."""
    assert cghs_record("BC002").nabh_rate == "1550"
    assert cghs_record("CI001").nabh_rate == "158"
    assert isinstance(cghs_record("CI001").nabh_rate, str)


def test_speciality_is_read_from_the_master_not_inferred_from_the_prefix():
    """Section 16: two codes sharing no prefix logic still carry their own."""
    assert cghs_record("BC002").speciality == "Blood Component Charges"
    assert cghs_record("GP001").speciality == "General Procedure"
    assert cghs_record("PT004").speciality == "Physiotherapy"


def test_registry_lookup_is_exact_and_never_fuzzy():
    """No closest match, no description search, no prefix widening."""
    assert cghs_record("cc002") is not None      # input case is normalised
    assert cghs_record(" CC002 ") is not None    # ...and surrounding space
    assert cghs_record("CC0020") is None
    assert cghs_record("CC02") is None
    assert cghs_record("Compressed Air") is None
    assert cghs_record("") is None
    assert cghs_record(None) is None


@pytest.mark.parametrize("row,expected", [
    ("SERVICE CGHS-L B248-2025 1 100.00 100.00", "LB248"),
    ("SERVICE CGHS-RI 062-2025 1 100.00 100.00", "RI062"),
    ("SERVICE CGHS-CI 003-2025 1 100.00 100.00", "CI003"),
    ("PACKED RED CELL CGHS-B C002-2025 1 100.00 100.00", "BC002"),
    ("SERVICE CGHS-A G008-2025 1 100.00 100.00", "AG008"),
    ("SERVICE CGHS-E P092-2025 1 100.00 100.00", "EP092"),
    ("SERVICE CGHS-M G001-2025 1 100.00 100.00", "MG001"),
    ("SERVICE CGHS-N S064-2025 1 100.00 100.00", "NS064"),
    ("SERVICE CGHS-N U110-2025 1 100.00 100.00", "NU110"),
    ("SERVICE CGHS-N U122-2025 1 100.00 100.00", "NU122"),
    ("SERVICE CGHS-P T004-2025 1 100.00 100.00", "PT004"),
    ("SERVICE CGHS-P T005-2025 1 100.00 100.00", "PT005"),
    ("SERVICE CGHS-G P009-2025 1 100.00 100.00", "GP009"),
])
def test_every_real_hospital_bill_format_resolves(row, expected):
    """Sections 7/14/19: each one, and each result, is in the master list."""
    assert codes(row) == [expected]
    assert expected in VALID_CODES


@pytest.mark.parametrize("row,expected", [
    ("SERVICE CGHS-RI 062-202 5 1 100.00 100.00", "RI062"),   # 062-202 + 5
    ("SERVICE CGHS-RI 062-20 25 1 100.00 100.00", "RI062"),
    ("SERVICE CGHS-L B-103-2 025 1 100.00 100.00", "LB103"),  # B-103-2 + 025
    ("SERVICE CGHS-L B103-2 025 1 100.00 100.00", "LB103"),
])
def test_a_pdf_line_wrapped_code_is_reconstructed_then_validated(row, expected):
    """Sections 8/19: reconstruct the token, then still prove registry membership."""
    assert codes(row) == [expected]


def test_the_hyphen_repair_never_welds_a_year_onto_a_family_letter():
    """Section 8: reconstruct, but never invent.  B-2025 is not B202."""
    assert "B202" not in codes("SERVICE CGHS-L B-2025 1 100.00 100.00")
    assert "LB202" not in codes("SERVICE CGHS-L B-2025 1 100.00 100.00")
    assert "RI202" not in codes("SERVICE CGHS-RI -2025 1 100.00 100.00")


def test_the_year_suffix_never_reaches_the_executable_code():
    """Section 9: -2025 is metadata."""
    for code in codes("SERVICE CGHS-RI 062-2025 1 100.00 100.00"):
        assert code == "RI062"
        assert "2025" not in code and "-" not in code


def test_a_proven_compound_becomes_three_independent_codes():
    """Sections 10/11/22: CGHS-L B042+043+044 is three codes, not one."""
    row = "LAB PANEL CGHS-L B042+043+044-2025 1 100.00 100.00"
    assert codes(row) == ["LB042", "LB043", "LB044"]
    for code in codes(row):
        assert code in VALID_CODES
        assert "-" not in code and "+" not in code

    occurrences = resolve_cghs_codes(row)
    assert len(occurrences) == 3
    assert {o["component_index"] for o in occurrences} == {0, 1, 2}
    assert all(o["component_count"] == 3 for o in occurrences)
    # Section 26: provenance survives the split.
    assert all(o["raw_expression"] == "B042+043+044" for o in occurrences)


def test_prefix_carry_needs_a_registry_hit_for_every_component():
    """Section 11: carry is refused the moment a component is not a real code.

    LB042 exists; LB999 does not.  The proven component still resolves, the
    unprovable one is preserved for review rather than invented.
    """
    row = "LAB PANEL CGHS-L B042+999-2025 1 100.00 100.00"
    assert codes(row) == ["LB042"]
    assert review(row) == ["999"]


@pytest.mark.parametrize("row", [
    "SERVICE CGHS-GP021 1 100.00 100.00",      # range() invented GP014..GP100
    "SERVICE CGHS-CC099 1 100.00 100.00",      # range() invented CC015..CC100
    "SERVICE CGHS-LB999 1 100.00 100.00",
    "SERVICE CGHS-RI999 1 100.00 100.00",
    "INVOICE 4471 CGHS-L B9999 1 100.00 100.00",
])
def test_a_pattern_shaped_code_absent_from_the_master_is_never_executable(row):
    """Sections 3/21: shape is not membership.  Fail closed, and say so."""
    assert codes(row) == []
    assert review(row), "the occurrence was silently dropped instead of reported"


@pytest.mark.parametrize("identifier", ["HSN 998514", "PMIS 12345",
                                        "SERVICE CODE 4471", "ITEM 00123"])
def test_a_foreign_identifier_is_never_read_as_a_cghs_code(identifier):
    """Section 18: invoice / HSN / PMIS / item numbers are not CGHS codes."""
    assert codes(f"{identifier} 1 100.00 100.00") == []


def test_the_c_family_safety_rules_are_unchanged_by_the_registry_swap():
    """Section 12/13: the locked context rules still govern raw C aliases."""
    # C003 has no approved target and stays unresolved - CC003 now EXISTS in
    # the master list, which makes this the sharpest possible version of the
    # rule: availability of a plausible target is still not authorisation.
    assert "CC003" in VALID_CODES
    assert codes("VENTILATOR CGHS-C C003 1 100.00 100.00") == []
    assert review("VENTILATOR CGHS-C C003 1 100.00 100.00") == ["C003"]

    # C002 is Oxygen only, and only with the evidence on the same row.
    assert codes("OXYGEN FULL DAY CGHS-C C002") == ["CC002"]
    assert codes("BLOOD BANK PACKED CELLS CGHS-C C002") == []
    assert codes("SOME OTHER SERVICE CGHS-C C002") == []

    # The remaining context mappings still need their same-row evidence.
    assert codes("BLOOD TRANSFUSION CGHS-C C008") == ["CC008"]
    assert codes("UNRELATED SERVICE CGHS-C C008") == []


def test_portal_pseudo_codes_are_not_registry_members():
    """Registry membership is not a portal target, and vice versa."""
    for pseudo in PORTAL_OPTION_MAP:
        assert pseudo not in VALID_CODES
        assert cghs_record(pseudo) is None
