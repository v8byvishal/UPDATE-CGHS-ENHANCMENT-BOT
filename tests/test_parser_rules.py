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


#: Samples whose FINAL CODES intentionally differ from the baseline, and why.
#: Everything not listed here must still agree code-for-code.
CODE_DEVIATIONS = {
    "WARD CHARGES CGHS-G WC001 2 500.00 1000.00": (
        ["WC001"], [],
        "WC001 is not a CGHS code: the 1998-record master list defines no WC "
        "family at all (it was a synthetic range() family). WC001 is an "
        "internal Room Rent tally, derived at parsing.py from counted ward "
        "rows, and parsing.py already REJECTS any WC001 appearing on a CGHS "
        "row ('must be from Room Rent only'). Resolving it from bill text "
        "would therefore manufacture a code the registry does not contain."),
    "Room Rent( CGHS-RI ) ICU 1 4500.00 4500.00": (
        ["RI145"], [],
        "RI145 was manufactured out of the rupee column: the baseline searched "
        "100 characters past the CGHS marker, digit-repair welded the quantity "
        "onto the amount ('1 4500.00' -> '14500.00') and the first three digits "
        "of that number became a code. No such service is on the row."),
}


@pytest.mark.parametrize("value", CODE_SAMPLES)
def test_normalize_cghs_code_matches_baseline(value):
    """The FINAL CODES must match the baseline, except where declared.

    Only the codes are compared: the reason string is explanatory text, not a
    business rule, and it now names the locked rule that fired.  Every code
    difference has to be declared in CODE_DEVIATIONS with its evidence.
    """
    mine = [code for code, _ in rules.normalize_cghs_code(value)]
    theirs = [code for code, _ in BASELINE.normalize_cghs_code(value)]

    if value in CODE_DEVIATIONS:
        was, now, _ = CODE_DEVIATIONS[value]
        assert theirs == was, "baseline no longer produces the declared codes"
        assert mine == now
    else:
        assert mine == theirs


def test_every_declared_code_deviation_is_real_and_explained():
    for value, (was, now, reason) in CODE_DEVIATIONS.items():
        assert value in CODE_SAMPLES, f"{value!r} is not a differential sample"
        assert was != now, f"{value!r} is declared but does not actually differ"
        assert len(reason) > 120, f"{value!r} needs real evidence, not a note"


def test_normalize_cghs_code_reasons_are_still_populated():
    """A changed reason string is fine; an empty one is not."""
    for value in CODE_SAMPLES:
        for code, reason in rules.normalize_cghs_code(value):
            assert code and reason, f"{value!r} produced an unexplained code"


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

    modified = []
    for name, body in original_bodies.items():
        assert name in current_bodies, f"rule {name} disappeared"
        if current_bodies[name] != body:
            modified.append(name)

    # Any change to a business rule must be DECLARED, with a reason, and backed
    # by its own differential test.  Undeclared drift still fails hard.
    # Compared as sorted sequences: ``modified`` follows baseline SOURCE order,
    # the declaration is alphabetical.  Equality of content is the assertion -
    # every change declared, every declaration real - not incidental ordering.
    assert sorted(modified) == sorted(DECLARED_RULE_DEVIATIONS), (
        f"undeclared rule change(s): {sorted(set(modified) - set(DECLARED_RULE_DEVIATIONS))}; "
        f"declared but unchanged: {sorted(set(DECLARED_RULE_DEVIATIONS) - set(modified))}")


#: The ONLY business-rule function allowed to differ from the baseline, and why.
#: See test_consumables_subtotal_is_layout_independent for the proof.
DECLARED_RULE_DEVIATIONS = {
    "normalize_cghs_code": (
        "The baseline built candidate codes as 'category + token' and accepted "
        "the first candidate that existed in VALID_CODES. Because "
        "_build_valid_codes generates the entire CC001..CC100 family as "
        "wildcards, EVERY invented CCxxx validated, so the function implemented "
        "a blanket Cxxx -> CCxxx expansion with no evidence: 'VENTILATOR CGHS-C "
        "C003' produced CC003, a code that does not exist, and the six "
        "context-sensitive aliases (C004/C008/C010/C011/C012/C014) resolved "
        "identically whether or not the row carried their service. It also "
        "searched 100 characters past the CGHS marker, so digit-repair welded "
        "the quantity onto the amount and manufactured RI145 out of a rupee "
        "value. Resolution is now an explicit ladder - explicit family token, "
        "locked category composition, locked same-row context rule - and an "
        "alias that no locked rule resolves is returned as REVIEW_REQUIRED "
        "instead of being guessed or silently dropped. Compound '+' "
        "expressions are split and resolved per component, and prefix carry "
        "across a '+' is no longer assumed."),
    "extract_dept_subtotal": (
        "Baseline accepted a department subtotal only when '0 < val < 500000'. "
        "That magnitude cutoff is not a business rule - no CGHS or hospital "
        "rule caps a department at five lakh - and on real bill BPLIP39538 "
        "(Mr. KAMTA PRASAD TIWARI, BPL-ICR-29191) it silently destroyed the "
        "pharmacy figure: IP Pharmacy's real Dept Sub Total of 742,104.38 was "
        "rejected and contributed 0.00, while OT Pharmacy's 1,790.80 passed, so "
        "the caller summed 1,790.80 and - being under the same ceiling - "
        "emitted DRUG100 = 1,790.80 against a true pharmacy spend of "
        "743,895.18. The extraction 'succeeded' syntactically and under-reported "
        "by 742,104.38 with no warning. The cutoff is removed; the guards that "
        "actually belong here are structural and are kept: the department slice "
        "and the requirement that the amount sit against a 'Dept Sub Total' "
        "label. The payable exclusion is now decided by REGION rather than by a "
        "word anywhere in the slice: a bill is partitioned into a payer-payable "
        "and a patient-payable region, each opening with a column header of "
        "that name and closing with a total of that name, and a section is "
        "excluded when its own department is Patient Payable, when it closes "
        "with a Patient Payable total, or when it opens after the payer region "
        "closed. The previous blanket 'Patient Payable|Grand Total|Payer "
        "Payable' substring test was wrong in a way the real bill proves: the "
        "payer region CLOSES with the words 'Payer Payable Total', so the LAST "
        "payer-side department of every bill in this format was silently zeroed "
        "and told the operator it had been excluded as patient money. "
        "Additionally the first-page summary span is now skipped explicitly, so "
        "a summary line restating the same department can never be added to its "
        "detailed subtotal and double count it; that span is identified "
        "structurally - everything before the first DETAILED department header, "
        "a detailed header being one whose section actually closes with a 'Dept "
        "Sub Total' - because real bills print the summary as a plain "
        "service/amount table with no caption to key off AND build that table "
        "out of department header lines, so 'before the first header' collapsed "
        "the span onto the summary's own first row. Sections are also anchored "
        "on a header line rather than on the department NAME, since a row "
        "description that merely mentions a department otherwise fabricated a "
        "section that absorbed the next real department's subtotal. Finally "
        "the reader is no longer first-match-only: "
        "a department may state SEVERAL subtotals - an OT Pharmacy section is "
        "commonly a run of dated blocks each closing with its own 'Dept Sub "
        "Total' - and reading just the first under-reported DRUG100 by every "
        "block after the first. All labels inside the department's own slice "
        "are now summed, section-scoped so no department can absorb another's "
        "money, and position-deduplicated so overlapping slices cannot count "
        "one subtotal twice. See "
        "test_dept_subtotal_differs_from_baseline_only_above_the_old_ceiling "
        "and the 39538 section of test_real_format_bills.py."),
    "extract_consumables_total": (
        "Also carried the identical '0 < val < 500000' magnitude cutoff removed "
        "from extract_dept_subtotal, which silently dropped real six-figure "
        "consumable subtotals; see that entry for the full evidence. Separately: "
        "Baseline scanned one alternating regex with finditer, which matches at "
        "the earliest position. When a line-item amount sat immediately above a "
        "'Dept Sub Total :' label, the value-first branch matched that ROW amount "
        "and consumed the label, so the real subtotal was never read. The result "
        "depended on PDF text layout: the SAME bill gave 3,586.50 in label-first "
        "layout and 8,993.90 in value-first layout, and extract_dept_subtotal "
        "(DRUG100) disagreed with extract_consumables_total (CNSU100) on identical "
        "text. The label is now anchored first and the amount resolved around it, "
        "label-first then value-first - the same precedence extract_dept_subtotal "
        "already applied. Only the previously-ambiguous layout changes."),
}


def test_declared_deviations_each_carry_a_reason():
    for name, reason in DECLARED_RULE_DEVIATIONS.items():
        assert hasattr(rules, name), f"{name} is declared but does not exist"
        assert len(reason) > 120, f"{name} needs a real explanation, not a note"


def test_consumables_subtotal_is_layout_independent():
    """The same bill must total the same whichever way the PDF extracts it.

    This is the oracle for the one declared deviation: the BASELINE itself
    produces the correct figure via its value-first path, so the value-first
    result is the intended semantics, and label-first must now agree with it.
    """
    rows = ("OT Consumables(999311)\n"
            "1 CGHS DISPOSABLE KIT OTC01 2 1500.00 3000.00\n"
            "2 CGHS SURGICAL MESH OTC02 1 3486.50 3486.50\n")
    ward = ("Ward Consumables(999311)\n"
            "1 CGHS GLOVES WC01 1 100.00 100.00\n")

    label_first = rows + "Dept Sub Total : 6,486.50\n" + ward + "Dept Sub Total : 2,507.40\n"
    value_first = rows + "6,486.50 Dept Sub Total\n" + ward + "2,507.40 Dept Sub Total\n"

    expected = 6486.50 + 2507.40

    assert rules.extract_consumables_total(label_first)[0] == pytest.approx(expected)
    assert rules.extract_consumables_total(value_first)[0] == pytest.approx(expected)

    # the value-first path is unchanged from the baseline - it was always right
    assert (rules.extract_consumables_total(value_first)[0]
            == pytest.approx(BASELINE.extract_consumables_total(value_first)[0]))

    # and the baseline really did disagree with itself across the two layouts
    assert (BASELINE.extract_consumables_total(label_first)[0]
            != pytest.approx(BASELINE.extract_consumables_total(value_first)[0]))


@pytest.mark.parametrize("layout,text,expected", [
    ("value_first", "OT Consumables(999311)\nX 1 1.00 1.00\n6,486.50 Dept Sub Total\n", 6486.50),
    ("label_first_no_rows", "OT Consumables(999311)\nDept Sub Total : 6,486.50\n", 6486.50),
    ("no_amount", "OT Consumables(999311)\nDept Sub Total :\n", 0.0),
    ("non_consumable_dept", "IP Pharmacy(999311)\nDept Sub Total : 1,250.00\n", 0.0),
])
def test_consumables_layouts_unchanged_from_baseline(layout, text, expected):
    """Every layout EXCEPT the ambiguous one must match the baseline exactly."""
    assert rules.extract_consumables_total(text)[0] == pytest.approx(expected)
    assert (rules.extract_consumables_total(text)[0]
            == pytest.approx(BASELINE.extract_consumables_total(text)[0]))


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
    assert CGHS_CATEGORY_MAP == BASELINE.CGHS_CATEGORY_MAP
    # VALID_CODES deliberately DIVERGES from the baseline: the synthetic
    # range()-generated universe (1802 codes, most of them invented) was
    # replaced by the operator-supplied 1998-record CGHS master list.  The
    # portal mappings either side of it are unchanged, which is the point of
    # this test - the registry swap did not disturb them.
    assert VALID_CODES != BASELINE.VALID_CODES
    assert len(VALID_CODES) == 1998


def test_portal_input_value_uses_the_mapping_then_the_code():
    assert portal_input_value("DRUG100") == "drugs(DRGU100-None)"
    assert portal_input_value("CNSU100") == "consumables(CNSU100-None)"
    assert portal_input_value("LB012") == "LB012"


def test_amount_based_codes_are_exactly_the_two_documented_ones():
    """The two amount-based codes are portal targets, not CGHS master codes.

    DRUG100/CNSU100 are department-subtotal pseudo-codes with proven portal
    mappings.  They are deliberately absent from the CGHS master list, and
    being a portal target is not registry membership - so the registry must
    NOT contain them, and the portal map must contain exactly these two.
    """
    assert set(PORTAL_OPTION_MAP) == {"DRUG100", "CNSU100"}
    assert not (set(PORTAL_OPTION_MAP) & set(VALID_CODES)), (
        "a portal pseudo-code leaked into the CGHS registry")


def test_dept_subtotal_differs_from_baseline_only_above_the_old_ceiling():
    """Oracle for the extract_dept_subtotal deviation.

    Below 500,000 the new implementation must agree with the baseline exactly.
    At and above it the baseline returns 0.00 - it does not reject the bill, it
    silently deletes the department - and the new implementation returns the
    real figure.
    """
    def one(amount):
        return (f"IP Pharmacy(999311)\n1 CGHS DRUG A IPP01 1 1.00 1.00\n"
                f"Dept Sub Total : {amount:,.2f}\n")

    for amount in (100.00, 12345.67, 250000.00, 499999.99):
        assert rules.extract_dept_subtotal(one(amount), r'IP\s*Pharmacy') == \
            pytest.approx(BASELINE.extract_dept_subtotal(one(amount), r'IP\s*Pharmacy')), \
            f"{amount:,.2f} is below the old ceiling and must be unchanged"

    for amount in (500000.00, 742104.38, 1250000.00):
        assert BASELINE.extract_dept_subtotal(one(amount), r'IP\s*Pharmacy') == 0.0, \
            "the baseline really did silently zero this department"
        assert rules.extract_dept_subtotal(one(amount), r'IP\s*Pharmacy') == \
            pytest.approx(amount, abs=0.005), "the real figure must survive now"


def test_drug100_under_the_old_ceiling_is_byte_for_byte_unchanged():
    """Bills the baseline handled correctly must produce the identical amount."""
    text = ("IP Pharmacy(999311)\n1 CGHS DRUG A IPP01 1 1.00 1.00\n"
            "Dept Sub Total : 12,345.67\n\n"
            "OT Pharmacy(999311)\n1 CGHS DRUG B OTP01 1 1.00 1.00\n"
            "Dept Sub Total : 2,345.00\n")
    ip = rules.extract_dept_subtotal(text, r'IP\s*Pharmacy')
    ot = rules.extract_dept_subtotal(text, r'OT\s*Pharmacy')
    assert ip == pytest.approx(BASELINE.extract_dept_subtotal(text, r'IP\s*Pharmacy'))
    assert ot == pytest.approx(BASELINE.extract_dept_subtotal(text, r'OT\s*Pharmacy'))
    assert round(ip + ot, 2) == pytest.approx(14690.67, abs=0.005)
