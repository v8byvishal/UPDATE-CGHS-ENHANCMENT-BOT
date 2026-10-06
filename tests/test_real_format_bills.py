"""D. REAL-FORMAT BILL REGRESSION.

PROVENANCE - read this before trusting any number in this file.

The real PDFs (40343, 39078, 40337 golden, D1-D5) are **not present** in this
repository and a Google Drive search returned none, so true PDF-level
regression is ``ENVIRONMENT_BLOCKED``.  Two things are done instead:

1. **If the PDFs ever appear**, :func:`_find_bill` picks them up automatically
   and the real parser runs against them.  Those tests skip, loudly, until then.

2. **Evidence-derived fixtures.**  The task brief states observed facts about
   40343 and 39078 (patient, IP number, bill number, department subtotals,
   Room Rent composition).  Those documented values are reconstructed here in
   the department layout the parser is specified to read, and the parser is
   asserted to extract exactly them.

What (2) proves: the parser handles the documented structure and returns the
documented subtotals.  What it does NOT prove: that a real PyMuPDF text
extraction of those PDFs produces this exact layout.  That distinction is why
the status stays ENVIRONMENT_BLOCKED rather than PASS.

Hard rules honoured here:

* no case-specific amount is promoted to a global rule - every assertion is
  about *extraction*, never about what a code "should" cost;
* no final billing result is invented for 40343: the brief explicitly says no
  proven final summary exists, and :func:`test_no_final_result_is_invented_for_40343`
  guards that;
* the aggregation rules (CC001/WC001/CN002/DRUG100/CNSU100) are the ones
  already locked in ``cghs.rules`` - this file only feeds them text.
"""

from __future__ import annotations

import contextlib
import io
import pathlib

import pytest

from cghs import rules
from cghs.parsing import extract_room_rent_section_from_pages

REPO = pathlib.Path(__file__).resolve().parents[1]

with contextlib.redirect_stdout(io.StringIO()):
    from tests.support.legacy_loader import load_baseline
    BASELINE = load_baseline()


def _find_bill(stem: str):
    """Locate a real bill PDF if the operator ever drops one in."""
    for folder in (REPO, REPO / "fixtures", REPO / "tests" / "fixtures",
                   REPO / "docs" / "evidence", REPO / "Library"):
        for candidate in (folder / f"{stem}.pdf", folder / f"{stem}.PDF"):
            if candidate.exists():
                return candidate
    return None


def _blocks(lines):
    """PyMuPDF-shaped ``get_text("blocks")`` tuples for one page."""
    return [(10.0, 20.0 * i, 500.0, 20.0 * i + 15.0, text, i, 0)
            for i, text in enumerate(lines)]


# ---------------------------------------------------------------------------
# evidence from the task brief, section 10
# ---------------------------------------------------------------------------

EVIDENCE = {
    "40343": {
        "pages": 16,
        "patient": "Mrs. LAXMI DEVI SHRIVASTAVA",
        "ip_no": "BPLIP40343",
        "bill_no": "BPL-ICR-28519",
        "ot_consumables": 6486.50,
        "ot_pharmacy": 447.70,
        "has_ip_pharmacy": True,
        "room_rent_has_icu": True,
        "final_result_known": False,      # explicitly NOT available
    },
    "39078": {
        "pages": 48,
        "patient": "Mr. DES RAJ BHAGAT",
        "ip_no": "BPLIP39078",
        "bill_no": "BPL-ICR-28535",
        "ot_consumables": 2507.40,
        "has_ip_pharmacy": True,
        "room_rent_has_icu": True,
        "room_rent_has_single": True,
        "final_result_known": False,
    },
}


def _bill_text(stem: str, ip_pharmacy: float, value_first: bool = False) -> str:
    """Reconstruct the documented department structure for one bill."""
    ev = EVIDENCE[stem]

    def subtotal(amount: float) -> str:
        return (f"{amount:,.2f} Dept Sub Total" if value_first
                else f"Dept Sub Total : {amount:,.2f}")

    parts = [
        "Hospital Bill Summary",
        f"Patient Name : {ev['patient']}",
        f"IP No : {ev['ip_no']}        Bill No : {ev['bill_no']}",
        "",
        "OT Consumables(999311)",
        "1  CGHS DISPOSABLE KIT  OTC01  2  1500.00  3000.00",
        "2  CGHS SURGICAL MESH   OTC02  1  3486.50  3486.50",
        subtotal(ev["ot_consumables"]),
        "",
    ]
    if "ot_pharmacy" in ev:
        parts += [
            "OT Pharmacy(999311)",
            "1  CGHS INJ PROPOFOL  OTP01  2  223.85  447.70",
            subtotal(ev["ot_pharmacy"]),
            "",
        ]
    parts += [
        "IP Pharmacy(999311)",
        "1  CGHS TAB PARACETAMOL  IPP01  10  12.00  120.00",
        subtotal(ip_pharmacy),
        "",
        # must never be aggregated
        "Patient Payable(999311)",
        "1  NON CGHS ITEM  XX001  1  999.00  999.00",
        subtotal(999.00),
    ]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# department subtotal extraction against the documented figures
# ---------------------------------------------------------------------------

def test_40343_department_subtotals_match_the_documented_evidence():
    text = _bill_text("40343", ip_pharmacy=1250.00, value_first=False)

    assert rules.extract_dept_subtotal(text, r'OT\s*Consumables') == pytest.approx(6486.50)
    assert rules.extract_dept_subtotal(text, r'OT\s*Pharmacy') == pytest.approx(447.70)
    assert rules.extract_dept_subtotal(text, r'IP\s*Pharmacy') == pytest.approx(1250.00)

    # DRUG100 = IP Pharmacy + OT Pharmacy, Patient Payable excluded
    drug100 = (rules.extract_dept_subtotal(text, r'IP\s*Pharmacy')
               + rules.extract_dept_subtotal(text, r'OT\s*Pharmacy'))
    assert drug100 == pytest.approx(1250.00 + 447.70)


def test_39078_department_subtotals_match_the_documented_evidence():
    text = _bill_text("39078", ip_pharmacy=3100.00)
    assert rules.extract_dept_subtotal(text, r'OT\s*Consumables') == pytest.approx(2507.40)
    assert rules.extract_dept_subtotal(text, r'IP\s*Pharmacy') == pytest.approx(3100.00)


@pytest.mark.parametrize("stem,expected", [("40343", 6486.50), ("39078", 2507.40)])
@pytest.mark.parametrize("value_first", [False, True], ids=["label_first", "value_first"])
def test_cnsu100_is_layout_independent_on_real_format_bills(stem, expected, value_first):
    """CNSU100 must not depend on how the PDF happened to extract."""
    text = _bill_text(stem, ip_pharmacy=1250.00, value_first=value_first)
    total, _detail = rules.extract_consumables_total(text)
    assert total == pytest.approx(expected)


@pytest.mark.parametrize("stem", sorted(EVIDENCE))
def test_patient_payable_is_never_aggregated(stem):
    text = _bill_text(stem, ip_pharmacy=1250.00)
    assert rules.extract_dept_subtotal(text, r'Patient\s*Payable') == 0.0
    total, _ = rules.extract_consumables_total(text)
    assert 999.00 not in (round(total, 2),)
    assert total == pytest.approx(EVIDENCE[stem]["ot_consumables"])


# ---------------------------------------------------------------------------
# Room Rent composition -> the locked CC001 / WC001 / CN002 derivations
# ---------------------------------------------------------------------------

def test_40343_room_rent_icu_rows_drive_cc001_and_cn002():
    """40343's Room Rent contains ICU rows (brief, section 10)."""
    pages = [_blocks([
        "Room Rent(999311)",
        "Room Rent( CGHS-RI ) ICU 1 4500.00 4500.00",
        "Room Rent( CGHS-RI ) ICU 1 4500.00 4500.00",
        "Sub Total 9000.00",
    ])]
    section = extract_room_rent_section_from_pages(pages)

    assert section["icu_count"] == 2
    assert section["ward_count"] == 0
    # locked derivation, not a bill amount
    assert section["cn002"] == 2 * 3 + 0 * 2 == 6
    # the ROW COUNT drives CC001, never the rupee amount
    assert section["icu_count"] != 4500


def test_39078_room_rent_mixes_single_and_icu_rows():
    """39078's Room Rent includes Single and ICU rows (brief, section 10)."""
    pages = [_blocks([
        "Room Rent(999311)",
        "Room Rent( CGHS-RI ) ICU 1 4500.00 4500.00",
        "Room Rent( CGHS-RI ) ICU 1 4500.00 4500.00",
        "Room Rent( CGHS-RI ) ICU 1 4500.00 4500.00",
        "Room Rent( CGHS-RI ) SINGLE 1 2500.00 2500.00",
        "Room Rent( CGHS-RI ) SINGLE 1 2500.00 2500.00",
        "Sub Total 18500.00",
    ])]
    section = extract_room_rent_section_from_pages(pages)

    assert section["icu_count"] == 3
    assert section["ward_single"] == 2
    assert section["ward_count"] == (section["ward_ac"] + section["ward_single"]
                                     + section["ward_other"])
    assert section["cn002"] == 3 * 3 + 2 * 2 == 13


@pytest.mark.parametrize("stem", sorted(EVIDENCE))
def test_room_rent_derivations_agree_with_the_baseline(stem):
    """Differential: the moved parser must derive exactly what the original did."""
    pages = [_blocks([
        "Room Rent(999311)",
        "Room Rent( CGHS-RI ) ICU 1 4500.00 4500.00",
        "Room Rent( CGHS-RI ) SINGLE 1 2500.00 2500.00",
        "Room Rent( CGHS-RI ) AC MULTIBEDS 1 2000.00 2000.00",
        "Sub Total 9000.00",
    ])]
    mine = extract_room_rent_section_from_pages(pages)

    class _FakePage:
        def __init__(self, blocks):
            self._blocks = blocks

        def get_text(self, kind="blocks"):
            return self._blocks

    original_open = BASELINE.fitz.open
    try:
        BASELINE.fitz.open = lambda *_a, **_k: [_FakePage(pages[0])]
        theirs = BASELINE.extract_room_rent_section("irrelevant.pdf")
    finally:
        BASELINE.fitz.open = original_open

    assert mine == theirs


# ---------------------------------------------------------------------------
# the honesty guards
# ---------------------------------------------------------------------------

FINDING_DEPT_SUBTOTAL_VALUE_FIRST = """
OPEN FINDING - NOT_YET_VERIFIED, deliberately NOT fixed.

extract_dept_subtotal() silently returns 0.00 for a department whose subtotal
is extracted in value-first layout ("6,486.50 Dept Sub Total") when another
department follows it.  Cause: the department-header regex

    [A-Za-z][A-Za-z\\s]*\\(\\s*999311\\s*\\)

lets [A-Za-z\\s]* span newlines, so the NEXT header is matched starting at
"Dept Sub Total\\n\\nOT Pharmacy(999311)" instead of at "OT Pharmacy".  The
department slice is then cut before its own subtotal and the amount is lost.

Impact: DRUG100 (IP Pharmacy + OT Pharmacy) could under-report on a bill whose
PDF extracts value-first.

Why it is NOT fixed here: unlike the extract_consumables_total defect, there is
no internal oracle proving the intended value - the baseline yields 0.00 on
every path, so a "fix" would be me choosing a new billing number with no
evidence.  The real PDFs are not available to confirm which layout 40343 and
39078 actually produce.  Fixing this requires the real bills, and the locked
mapping must not change on a fixture's suggestion.
"""


@pytest.mark.xfail(strict=True, reason="open finding: see FINDING_DEPT_SUBTOTAL_VALUE_FIRST")
def test_FINDING_dept_subtotal_loses_value_first_departments():
    """Reproducer kept executable so the day it is fixed, this test tells us."""
    text = _bill_text("40343", ip_pharmacy=1250.00, value_first=True)
    assert rules.extract_dept_subtotal(text, r'OT\s*Consumables') == pytest.approx(6486.50)


def test_no_final_result_is_invented_for_40343():
    """The brief states no proven final summary exists for 40343.

    Nothing in the codebase may hardcode one.
    """
    assert EVIDENCE["40343"]["final_result_known"] is False

    # no module in the package may carry a bill-specific total
    for module in sorted((REPO / "cghs").glob("*.py")):
        text = module.read_text(encoding="utf-8")
        for ident in ("BPLIP40343", "BPL-ICR-28519", "BPLIP39078", "BPL-ICR-28535"):
            assert ident not in text, f"{module.name} hardcodes case identity {ident}"


def test_case_specific_amounts_are_not_hardcoded_in_the_package():
    """6486.50 / 447.70 / 2507.40 are evidence, not rules."""
    for module in sorted((REPO / "cghs").glob("*.py")):
        text = module.read_text(encoding="utf-8")
        for amount in ("6486.50", "6,486.50", "447.70", "2507.40", "2,507.40"):
            assert amount not in text, f"{module.name} hardcodes case amount {amount}"


# ---------------------------------------------------------------------------
# real PDFs - activate automatically once supplied
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stem", ["40343", "39078", "40337"])
def test_real_pdf_parses_when_supplied(stem):
    path = _find_bill(stem)
    if path is None:
        pytest.skip(f"ENVIRONMENT_BLOCKED: {stem}.pdf is not in the repository")

    pytest.importorskip("fitz")
    from cghs.parsing import CGHSParsingEngine

    plan = CGHSParsingEngine().parse(str(path))
    assert plan is not None

    if stem in EVIDENCE:
        ev = EVIDENCE[stem]
        blob = str(plan)
        assert ev["ip_no"] in blob or ev["bill_no"] in blob, (
            f"parsed {stem}.pdf but found neither {ev['ip_no']} nor {ev['bill_no']}")


@pytest.mark.parametrize("stem", ["D1", "D2", "D3", "D4", "D5"])
def test_d_series_fixtures_when_present(stem):
    path = _find_bill(stem)
    if path is None:
        pytest.skip(f"ENVIRONMENT_BLOCKED: {stem}.pdf is not in the repository")

    pytest.importorskip("fitz")
    from cghs.parsing import CGHSParsingEngine

    assert CGHSParsingEngine().parse(str(path)) is not None


# ===========================================================================
# E. BILL 39538 - PHARMACY AMOUNT CORRECTNESS
#
# PROVENANCE - read before trusting any number below.
#
# 39538.pdf is NOT in this repository and must not be added to it.  The
# figures asserted here were read off the real bill by the operator:
#
#     Mr. KAMTA PRASAD TIWARI / IP BPLIP39538 / Bill BPL-ICR-29191
#
#     Service Summary        IP Pharmacy              773,703.60
#                            OT Pharmacy                1,790.80
#     detailed IP Pharmacy   Dept Sub Total           742,104.38
#                            Dept Total               742,104.38
#     detailed OT Pharmacy   Dept Sub Total             1,790.80
#     a SEPARATE department  Dept Sub Total            31,599.22
#                            Dept Total                31,599.22
#                            Patient Payable Total     31,599.00
#
#     742,104.38 + 31,599.22 = 773,703.60
#
# That arithmetic is the whole point of this section.  The Service Summary's
# "IP Pharmacy" line AGGREGATES the detailed IP Pharmacy department and a
# separate department; it is not a second statement of the same department.
# So summary != detail is ordinary bill structure, not a contradiction, and
# must never gate DRUG100.  An earlier revision withheld 39538's DRUG100 as
# REVIEW_REQUIRED on exactly that false inference; these tests pin the
# corrected behaviour.
#
# The locked rule (cghs.parsing module docstring) is unchanged:
#     DRUG100 = detailed IP Pharmacy subtotal + detailed OT Pharmacy subtotal
# giving 742,104.38 + 1,790.80 = 743,895.18 for this bill.
#
# What these tests prove: the extractor handles a real bill's magnitudes and
# section structure and reports the documented figures.  What they do NOT
# prove: that a PyMuPDF text extraction of the real 39538.pdf produces this
# exact layout.
# ===========================================================================

from cghs.parsing import CGHSParsingEngine

IP_PHARMACY_SUMMARY_39538 = 773703.60
IP_PHARMACY_DETAILED_39538 = 742104.38
OT_PHARMACY_39538 = 1790.80
#: A SEPARATE department's subtotal - not part of IP Pharmacy, not in DRUG100.
SEPARATE_SECTION_39538 = 31599.22
#: The locked rule: detailed IP subtotal + detailed OT subtotal.
DRUG100_39538 = 743895.18


def _bill_39538_text(with_summary=True, with_separate_section=True):
    """39538 in the department layout the parser is specified to read."""
    head = [
        "Service Summary",
        "IP Pharmacy                               773,703.60",
        "OT Pharmacy                                 1,790.80",
        "Deposit Received                           50,000.00",
        "Patient Payable                            12,345.00",
        "Grand Total                             1,234,567.89",
        "",
    ] if with_summary else []
    tail = [
        "Ward Consumables(999311)",
        "1  CGHS CONSUMABLE KIT  WCC01  1  31599.22  31,599.22",
        "Dept Sub Total : 31,599.22",
        "Dept Total : 31,599.22",
        "Patient Payable Total : 31,599.00",
        "",
    ] if with_separate_section else []
    return "\n".join(head + [
        "IP Pharmacy(999311)",
        "1  CGHS TAB PARACETAMOL  IPP01  10  12.00  120.00",
        "2  CGHS INJ MEROPENEM    IPP02  40  1250.00  50,000.00",
        "Dept Sub Total : 742,104.38",
        "Dept Total : 742,104.38",
        "",
        "OT Pharmacy(999311)",
        "1  CGHS INJ PROPOFOL  OTP01  8  223.85  1,790.80",
        "Dept Sub Total : 1,790.80",
        "Dept Total : 1,790.80",
        "",
    ] + tail + [
        "Patient Payable(999311)",
        "1  NON CGHS ITEM  XX001  1  12345.00  12,345.00",
        "Dept Sub Total : 12,345.00",
    ])


def _parse_text(text):
    return CGHSParsingEngine().parse_document([[]], text, "<39538>")


def _drug100(text):
    final, _raw, _name, _rejected, _log = _parse_text(text)
    return [i for i in final if i["code"] == "DRUG100"]


# --- 1. the required 39538 result ------------------------------------------

def test_39538_drug100_is_exactly_743895_18():
    """THE acceptance figure: detailed IP subtotal + detailed OT subtotal."""
    drug = _drug100(_bill_39538_text())
    assert drug, "DRUG100 must be produced for 39538, not withheld"
    assert drug[0]["amount"] == pytest.approx(DRUG100_39538, abs=0.005)
    assert drug[0]["qty"] == 1, "an amount must never become a unit quantity"


def test_39538_drug100_is_executable_not_review_required():
    final, _raw, _name, rejected, _log = _parse_text(_bill_39538_text())
    assert [i for i in final if i["code"] == "DRUG100"], (
        "39538 pharmacy is fully explained by the bill structure and must be "
        "executable")
    assert not [r for r in rejected if r.get("code") == "DRUG100"], (
        "a Service Summary that aggregates other departments is not a "
        "pharmacy contradiction and must not raise REVIEW_REQUIRED")


def test_39538_summary_total_is_never_used_as_the_drug100_amount():
    """773,703.60 + 1,790.80 would double count the separate department."""
    drug = _drug100(_bill_39538_text())
    assert drug[0]["amount"] != pytest.approx(
        IP_PHARMACY_SUMMARY_39538 + OT_PHARMACY_39538, abs=0.005)
    assert drug[0]["amount"] != pytest.approx(IP_PHARMACY_SUMMARY_39538, abs=0.005)


# --- 2. the 31,599.22 is a separate section, not IP Pharmacy detail --------

def test_39538_arithmetic_of_the_summary_line():
    """742,104.38 + 31,599.22 = 773,703.60 - the summary aggregates both."""
    assert IP_PHARMACY_DETAILED_39538 + SEPARATE_SECTION_39538 == pytest.approx(
        IP_PHARMACY_SUMMARY_39538, abs=0.005)


def test_31599_is_not_classified_as_ip_pharmacy_detail():
    ip = rules.extract_dept_subtotal(_bill_39538_text(), r'IP\s*Pharmacy')
    assert ip == pytest.approx(IP_PHARMACY_DETAILED_39538, abs=0.005)
    assert ip != pytest.approx(
        IP_PHARMACY_DETAILED_39538 + SEPARATE_SECTION_39538, abs=0.005), (
        "the separate department was absorbed into IP Pharmacy")


def test_31599_is_not_included_in_drug100():
    drug = _drug100(_bill_39538_text())
    assert drug[0]["amount"] == pytest.approx(DRUG100_39538, abs=0.005)
    assert drug[0]["amount"] != pytest.approx(
        DRUG100_39538 + SEPARATE_SECTION_39538, abs=0.005)


def test_31599_section_is_distinct_from_the_pharmacy_departments():
    """It lives under its own department header, not inside either pharmacy."""
    text = _bill_39538_text()
    ip_body = text[text.index("IP Pharmacy(999311)"):text.index("OT Pharmacy(999311)")]
    ot_body = text[text.index("OT Pharmacy(999311)"):text.index("Ward Consumables(999311)")]
    assert "31,599.22" not in ip_body and "31,599.22" not in ot_body
    assert "31,599.22" in text, "the separate section must still be in the bill"


def test_drug100_is_identical_with_and_without_the_separate_section():
    """DRUG100 depends only on the two pharmacy departments."""
    with_it = _drug100(_bill_39538_text(with_separate_section=True))[0]["amount"]
    without = _drug100(_bill_39538_text(with_separate_section=False))[0]["amount"]
    assert with_it == pytest.approx(without, abs=0.005) == pytest.approx(
        DRUG100_39538, abs=0.005)


# --- 3. the two 39538 pharmacy sources, extracted independently ------------

def test_39538_ip_pharmacy_detailed_subtotal():
    assert rules.extract_dept_subtotal(
        _bill_39538_text(), r'IP\s*Pharmacy') == pytest.approx(
            IP_PHARMACY_DETAILED_39538, abs=0.005)


def test_39538_ot_pharmacy_is_extracted_correctly():
    assert rules.extract_dept_subtotal(
        _bill_39538_text(), r'OT\s*Pharmacy') == pytest.approx(
            OT_PHARMACY_39538, abs=0.005)


def test_39538_service_summary_is_read_separately_from_the_detail():
    """Reachable as provenance, and never mistaken for the detail."""
    assert rules.extract_service_summary_amount(
        _bill_39538_text(), r'IP\s*Pharmacy') == pytest.approx(
            IP_PHARMACY_SUMMARY_39538, abs=0.005)
    assert rules.extract_service_summary_amount(
        _bill_39538_text(), r'OT\s*Pharmacy') == pytest.approx(
            OT_PHARMACY_39538, abs=0.005)


# --- 4. the original large-amount defect stays fixed -----------------------

def test_ip_pharmacy_above_five_lakh_is_not_silently_dropped():
    """The original live defect: the ceiling zeroed IP and emitted OT alone."""
    got = rules.extract_dept_subtotal(_bill_39538_text(), r'IP\s*Pharmacy')
    assert got == pytest.approx(IP_PHARMACY_DETAILED_39538, abs=0.005), (
        f"IP Pharmacy extracted as {got:,.2f}; an arbitrary < 500000 ceiling "
        f"silently discards real six-figure pharmacy subtotals")


@pytest.mark.parametrize("amount", [499999.99, 500000.00, 500000.01,
                                    742104.38, 773703.60, 1250000.00])
def test_dept_subtotal_has_no_arbitrary_upper_bound(amount):
    text = (f"IP Pharmacy(999311)\n1 X A1 1 1.00 1.00\n"
            f"Dept Sub Total : {amount:,.2f}\n")
    assert rules.extract_dept_subtotal(text, r'IP\s*Pharmacy') == pytest.approx(
        amount, abs=0.005), f"{amount:,.2f} was dropped by a magnitude cutoff"


def test_consumables_total_has_no_arbitrary_upper_bound():
    text = ("OT Consumables(999311)\n1 STENT C1 1 1.00 1.00\n"
            "Dept Sub Total : 600,000.00\n")
    total, _details = rules.extract_consumables_total(text)
    assert total == pytest.approx(600000.00, abs=0.005), (
        "CNSU100 carried the identical ceiling defect")


def test_39538_never_reports_the_ot_only_amount_as_drug100():
    """The exact original defect: IP zeroed, OT emitted, nobody told."""
    for item in _drug100(_bill_39538_text()):
        assert item["amount"] != pytest.approx(OT_PHARMACY_39538, abs=0.005), (
            "DRUG100 reported the OT component alone - the IP subtotal was "
            "silently discarded")


# --- 5. no summary/detail double counting ----------------------------------

def test_summary_and_detail_are_never_added_together():
    got = rules.extract_dept_subtotal(_bill_39538_text(), r'IP\s*Pharmacy')
    assert got == pytest.approx(IP_PHARMACY_DETAILED_39538, abs=0.005)
    assert got != pytest.approx(
        IP_PHARMACY_SUMMARY_39538 + IP_PHARMACY_DETAILED_39538, abs=0.005)


def test_a_summary_line_spelling_dept_sub_total_is_still_not_detail():
    """Hardening: a summary that happens to carry the detail label."""
    text = "\n".join([
        "Service Summary",
        "IP Pharmacy Dept Sub Total : 773,703.60",
        "",
        "IP Pharmacy(999311)",
        "1 X A1 1 1.00 1.00",
        "Dept Sub Total : 742,104.38",
    ])
    assert rules.extract_dept_subtotal(text, r'IP\s*Pharmacy') == pytest.approx(
        IP_PHARMACY_DETAILED_39538, abs=0.005)


def test_pharmacy_amount_is_never_the_grand_total_or_patient_payable():
    ev = rules.reconcile_pharmacy(_bill_39538_text())
    for forbidden in (1234567.89, 12345.00, 50000.00):
        assert ev["total"] != pytest.approx(forbidden, abs=0.005)


# --- 6. provenance and precision -------------------------------------------

def test_decimal_precision_is_preserved():
    ev = rules.reconcile_pharmacy(_bill_39538_text())
    assert round(ev["total"], 2) == pytest.approx(DRUG100_39538, abs=0.005)


def test_pharmacy_amount_carries_provenance():
    ev = rules.reconcile_pharmacy(_bill_39538_text())
    assert ev["provenance"], "every pharmacy figure must say where it came from"
    labels = {p["label"] for p in ev["provenance"]}
    assert {"IP Pharmacy", "OT Pharmacy"} <= labels
    for p in ev["provenance"]:
        assert p["source"] in {"detailed_dept_subtotal", "service_summary"}
    detailed = {p["label"]: p["amount"] for p in ev["provenance"]
                if p["source"] == "detailed_dept_subtotal"}
    assert detailed["IP Pharmacy"] == pytest.approx(IP_PHARMACY_DETAILED_39538, abs=0.005)


def test_the_aggregating_summary_is_recorded_not_treated_as_a_contradiction():
    ev = rules.reconcile_pharmacy(_bill_39538_text())
    assert ev["ip_summary"] == pytest.approx(IP_PHARMACY_SUMMARY_39538, abs=0.005)
    assert ev["difference"] == pytest.approx(SEPARATE_SECTION_39538, abs=0.01)
    assert ev["total"] == pytest.approx(DRUG100_39538, abs=0.005), (
        "the recorded difference must not change the amount")
    assert "REVIEW" not in ev["reason"].upper()


def test_bill_without_a_service_summary_behaves_identically():
    ev = rules.reconcile_pharmacy(_bill_39538_text(with_summary=False))
    assert ev["ip_summary"] is None
    assert ev["total"] == pytest.approx(DRUG100_39538, abs=0.005)
    assert _drug100(_bill_39538_text(with_summary=False))[0]["amount"] == \
        pytest.approx(DRUG100_39538, abs=0.005)
