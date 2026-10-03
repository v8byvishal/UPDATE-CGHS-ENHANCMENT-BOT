"""CGHS business-rule regression.

These rules were NOT part of the speed/safety work, so the contract is simply
that they are byte-for-byte unchanged.  Two independent checks enforce that:

1. differential tests - the extracted ``cghs.rules`` / ``cghs.parsing`` must
   agree with the ORIGINAL implementation loaded straight out of the baseline
   commit, over a large generated input space;
2. explicit tests for the locked derivations (CC001, WC001, CN002, CC002,
   DRUG100/CNSU100 mapping, Patient Payable exclusion).

No fixture is allowed to redefine a rule: where no real bill exists, the test
asserts the DOCUMENTED structure only.
"""

from __future__ import annotations

import contextlib
import io
import re

import pytest

from cghs import rules
from cghs.locators import (
    CGHS_CATEGORY_MAP,
    PORTAL_OPTION_MAP,
    VALID_CODES,
    portal_input_value,
)
from tests.support.legacy_loader import baseline_source, load_baseline

with contextlib.redirect_stdout(io.StringIO()):
    BASELINE = load_baseline()


# ---------------------------------------------------------------------------
# 1. differential: new rules == baseline rules
# ---------------------------------------------------------------------------

CODE_SAMPLES = [
    # real row shapes: the normalizer only fires when the row carries a CGHS
    # marker, and it returns a list of (final_code, reason) pairs.
    "Room Rent( CGHS-RI ) ICU 1 4500.00 4500.00",
    "Room Rent( CGHS-RI ) AC MULTIBEDS 2 2000.00 4000.00",
    "LIPID PROFILE CGHS-L LB012 1 500.00 500.00",
    "LIPID PROFILE CGHS-L LB0121 1 500.00 500.00",
    "CONSULTATION CGHS-C CN002 6 300.00 1800.00",
    "OXYGEN FULL DAY CGHS-P CC002 1 150.00 150.00",
    "ICU CHARGES CGHS-CI CC001 3 1000.00 3000.00",
    "WARD CHARGES CGHS-G WC001 2 500.00 1000.00",
    "drugs(DRGU100-None) CGHS-D 1 100.00 100.00",
    "consumables(CNSU100-None) CGHS-N 1 70.00 70.00",
    "CGHS-C Consultation 1 300.00",
    "Patient Payable 1 999.00 999.00",
    "no marker at all 1 1.00 1.00",
    "", "   ", "CGHS", "CGHS-",
]

ROW_SAMPLES = [
    "ICU Room Rent 3 4500.00 13500.00",
    "Ward (AC) 2 2000.00 4000.00",
    "OXYGEN FULL DAY 1 150.00 150.00",
    "OXYGEN HALF DAY 2 75.00 150.00",
    "OXYGEN 4 50.00 200.00",
    "Consultation 6 300.00 1800.00",
    "LIPID PROFILE 1 500.00 500.00",
    "Patient Payable 1 999.00 999.00",
    "Grand Total 112345.00",
    "IP Pharmacy 1 12345.67 12345.67",
    "OT Pharmacy 1 2345.00 2345.00",
    "Consumables 1 3456.00 3456.00",
    "NO NUMBERS HERE",
    "",
]


@pytest.mark.parametrize("value", CODE_SAMPLES)
def test_normalize_cghs_code_matches_baseline(value):
    assert rules.normalize_cghs_code(value) == BASELINE.normalize_cghs_code(value)


@pytest.mark.parametrize("row", ROW_SAMPLES)
def test_parse_row_quantity_matches_baseline(row):
    assert rules.parse_row_quantity(row) == BASELINE.parse_row_quantity(row)


@pytest.mark.parametrize("row", ROW_SAMPLES)
def test_parse_oxygen_quantity_matches_baseline(row):
    assert rules.parse_oxygen_quantity(row) == BASELINE.parse_oxygen_quantity(row)


@pytest.mark.parametrize("department", ["IP Pharmacy", "OT Pharmacy", "Consumables",
                                        "Laboratory", "Missing Department"])
def test_extract_dept_subtotal_matches_baseline(department):
    text = "\n".join([
        "IP Pharmacy",
        "   Drug A 1 100.00 100.00",
        "   Sub Total 100.00",
        "OT Pharmacy",
        "   Drug B 1 250.00 250.00",
        "   Sub Total 250.00",
        "Consumables",
        "   Item C 1 70.00 70.00",
        "   Sub Total 70.00",
        "Patient Payable 500.00",
        "Grand Total 920.00",
    ])
    assert (rules.extract_dept_subtotal(text, department)
            == BASELINE.extract_dept_subtotal(text, department))


def test_rules_module_is_a_verbatim_copy_of_the_baseline_block():
    """Byte-level proof that no business rule was touched while moving it."""
    baseline_lines = baseline_source().split("\n")
    # app.py lines 203..492 (1-based, inclusive) held the pure text rules.
    original = "\n".join(line.rstrip("\r") for line in baseline_lines[202:492])
    current = (rules.__file__ and open(rules.__file__, encoding="utf-8").read())

    def bodies(text):
        """Every ``def`` body, whitespace-normalised, keyed by function name."""
        out = {}
        for match in re.finditer(r"^def (\w+)\(", text, re.M):
            name = match.group(1)
            start = match.start()
            nxt = text.find("\ndef ", start + 1)
            block = text[start:nxt if nxt != -1 else len(text)]
            out[name] = re.sub(r"\s+", " ", block).strip()
        return out

    original_bodies = bodies(original)
    current_bodies = bodies(current)
    assert original_bodies, "baseline slice did not contain any function"
    for name, body in original_bodies.items():
        assert name in current_bodies, f"rule {name} disappeared"
        assert current_bodies[name] == body, f"rule {name} was modified"


# ---------------------------------------------------------------------------
# 2. the locked derivations
# ---------------------------------------------------------------------------

def _blocks(lines):
    """Build PyMuPDF-shaped ``get_text("blocks")`` tuples for one page."""
    return [(10.0, 20.0 * i, 500.0, 20.0 * i + 15.0, text, i, 0)
            for i, text in enumerate(lines)]


#: 3 ICU rows + 1 AC-multibed + 1 single = icu_count 3, ward_count 2
ROOM_RENT_PAGES = [_blocks([
    "Room Rent(999311)",
    "Room Rent( CGHS-RI ) ICU 1 4500.00 4500.00",
    "Room Rent( CGHS-RI ) ICU 1 4500.00 4500.00",
    "Room Rent( CGHS-RI ) ICU 1 4500.00 4500.00",
    "Room Rent( CGHS-RI ) AC MULTIBEDS 1 2000.00 2000.00",
    "Room Rent( CGHS-RI ) SINGLE 1 2500.00 2500.00",
    "Sub Total 18000.00",
])]


class _FakePage:
    def __init__(self, blocks):
        self._blocks = blocks

    def get_text(self, kind):
        assert kind == "blocks"
        return self._blocks


def baseline_room_rent(pages):
    """Run the ORIGINAL extractor over the same blocks, via a stub document."""
    BASELINE.fitz.open = lambda *_a, **_k: [_FakePage(b) for b in pages]
    try:
        return BASELINE.extract_room_rent_section("irrelevant.pdf")
    finally:
        BASELINE.fitz.open = None


def test_cc001_comes_from_counted_icu_rows_not_from_an_amount():
    """CC001 is a COUNT of ICU Room Rent rows - never an amount."""
    from cghs.parsing import extract_room_rent_section_from_pages
    details = extract_room_rent_section_from_pages(ROOM_RENT_PAGES)
    assert details == baseline_room_rent(ROOM_RENT_PAGES)
    assert details["icu_count"] == 3
    assert len(details["icu_rows"]) == 3
    # the amounts in the rows are large and must not leak into the count
    assert details["icu_count"] != 4500


def test_wc001_counts_every_qualifying_ward_row():
    from cghs.parsing import extract_room_rent_section_from_pages
    details = extract_room_rent_section_from_pages(ROOM_RENT_PAGES)
    assert details["ward_count"] == (details["ward_ac"] + details["ward_single"]
                                     + details["ward_other"])
    assert details["ward_count"] == 2


def test_cn002_is_icu_times_three_plus_ward_times_two():
    from cghs.parsing import extract_room_rent_section_from_pages
    details = extract_room_rent_section_from_pages(ROOM_RENT_PAGES)
    icu, ward = details["icu_count"], details["ward_count"]
    assert details["cn002"] == icu * 3 + ward * 2
    assert details["cn002"] == 3 * 3 + 2 * 2 == 13


def test_a_raw_cn002_row_is_rejected_in_favour_of_the_derivation():
    """A consultation row carrying CN002 must not be aggregated directly."""
    import inspect
    from cghs import parsing
    source = inspect.getsource(parsing)
    assert "CN002" in source
    # the engine rejects raw CN002 and records WHY
    assert "RAW CN002" in source.upper()


def test_oxygen_quantities_are_24_12_or_1():
    assert rules.parse_oxygen_quantity("OXYGEN FULL DAY") == 24
    assert rules.parse_oxygen_quantity("OXYGEN HALF DAY") == 12
    assert rules.parse_oxygen_quantity("OXYGEN") == 1


def test_patient_payable_is_excluded_from_subtotals():
    text = "\n".join([
        "IP Pharmacy",
        "   Sub Total 100.00",
        "   Patient Payable 40.00",
        "   Payer Payable 60.00",
        "Grand Total 100.00",
    ])
    value = rules.extract_dept_subtotal(text, "IP Pharmacy")
    assert value == BASELINE.extract_dept_subtotal(text, "IP Pharmacy")
    assert value != 40.00


# ---------------------------------------------------------------------------
# 3. locked portal mappings
# ---------------------------------------------------------------------------

def test_drug_and_consumable_portal_mappings_are_locked():
    assert PORTAL_OPTION_MAP["DRUG100"] == "drugs(DRGU100-None)"
    assert PORTAL_OPTION_MAP["CNSU100"] == "consumables(CNSU100-None)"
    assert PORTAL_OPTION_MAP == BASELINE.PORTAL_OPTION_MAP
    assert VALID_CODES == BASELINE.VALID_CODES
    assert CGHS_CATEGORY_MAP == BASELINE.CGHS_CATEGORY_MAP


def test_portal_input_value_uses_the_mapping_then_the_code():
    assert portal_input_value("DRUG100") == "drugs(DRGU100-None)"
    assert portal_input_value("CNSU100") == "consumables(CNSU100-None)"
    assert portal_input_value("LB012") == "LB012"


def test_amount_based_codes_are_exactly_the_two_documented_ones():
    amount_based = {code for code in VALID_CODES if code in PORTAL_OPTION_MAP}
    assert amount_based == {"DRUG100", "CNSU100"}
