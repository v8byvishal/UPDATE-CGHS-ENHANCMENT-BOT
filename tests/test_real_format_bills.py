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
import hashlib
import io
import pathlib
import re
import sys

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
CLOSED FINDING - fixed, and confirmed against the real bill.

The finding as originally recorded (kept verbatim, indented):

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

The blocking condition - "requires the real bills" - no longer holds.  The
example-format bill 39538.pdf is now committed to Git (origin/main b5987f4,
blob 423a9ea6, sha256 1825f727...) and reading it CONFIRMED the diagnosis
independently.  The newline-spanning name class really did match across a
line boundary ('Service \\nBlood Bank Procedure(999311 )'), and it also could
not express a parenthesised department name at all, so
'Hospital services (others)(999311 )' produced no boundary and the preceding
Equipment section absorbed its subtotal (79,920.00 was read as 81,180.00).

_DEPT_HEADER_RE is now anchored to one line and accepts a parenthesised
qualifier inside the name.  The reproducer below is kept executable and is
now asserted to PASS;
test_real_39538_equipment_stops_at_a_parenthesised_department pins the same
repair against the real document.
"""


def test_FINDING_dept_subtotal_loses_value_first_departments():
    """Former open finding - now a positive regression test."""
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
# 39538.pdf is NOT in this repository, not on this filesystem and not in the
# connected Google Drive (all three were searched).  It must not be added.
# The STRUCTURE encoded below is the structure the operator read off the real
# PDF, and it is reconstructed in the department layout the parser is
# specified to read:
#
#   Mr. KAMTA PRASAD TIWARI / IP BPLIP39538 / Bill BPL-ICR-29191
#
#   page 1      service/amount table, NO "Service Summary" caption
#                 IP Pharmacy                        773,703.60
#                 OT Pharmacy                          1,790.80
#   to p73      IP Pharmacy(999311 )  Dept Sub Total  742,104.38
#                                     Dept Total      742,104.38
#   OT          OT Pharmacy(999311 )  five DATED blocks, one subtotal each:
#                 223.85 + 447.70 + 447.70 + 447.70 + 223.85 = 1,790.80
#   p91-94      IP Pharmacy(999311 )  Dept Sub Total   31,599.22
#                                     Dept Total       31,599.22
#                                     Patient Payable Total  31,599.00
#
#   742,104.38 + 31,599.22 = 773,703.60
#
# The second block is a SECOND IP PHARMACY SECTION - not Ward Consumables,
# not a different department.  It is excluded from DRUG100 for one reason
# only, the reason the project already locked:
#
#   cghs/parsing.py  "DRUG100 = IP Pharmacy subtotal + OT Pharmacy subtotal
#                     (Patient Payable excluded)"
#   cghs/rules.py    a department slice carrying Patient Payable is skipped
#   test_cghs_code_rules.py::test_patient_payable_is_not_an_enhancement_contribution
#
# That contract resolves the "which IP subtotals?" ambiguity without
# inventing a rule: ALL IP Pharmacy detailed subtotals count, MINUS any whose
# section is patient-payable.  The second section's Patient Payable Total of
# 31,599.00 covers its 31,599.22 subtotal, so it is excluded - visibly, with
# a recorded reason, never silently.
#
#   DRUG100 = 742,104.38 + (223.85+447.70+447.70+447.70+223.85) = 743,895.18
#
# What these tests prove: the extractor handles this structure and reports
# these figures.  What they do NOT prove: that a PyMuPDF text extraction of
# the real 39538.pdf produces this exact layout.  REAL_FORMAT_PDF_REGRESSION
# stays ENVIRONMENT_BLOCKED until the PDF is available.
# ===========================================================================

from cghs.parsing import CGHSParsingEngine

IP_PHARMACY_SUMMARY_39538 = 773703.60
IP_MAIN_SUBTOTAL_39538 = 742104.38
#: Second IP Pharmacy section - patient-payable, excluded from DRUG100.
IP_SECOND_SUBTOTAL_39538 = 31599.22
OT_DATED_SUBTOTALS_39538 = [223.85, 447.70, 447.70, 447.70, 223.85]
OT_PHARMACY_39538 = 1790.80
DRUG100_39538 = 743895.18


def _bill_39538_text(with_summary=True, with_second_ip_section=True,
                     ot_blocks=None):
    """39538 in the department layout the parser is specified to read."""
    blocks = OT_DATED_SUBTOTALS_39538 if ot_blocks is None else ot_blocks
    head = [
        "BILL OF SUPPLY                         BPL-ICR-29191",
        "Mr. KAMTA PRASAD TIWARI                IP BPLIP39538",
        "Service                                      Amount",
        "IP Pharmacy                              773,703.60",
        "OT Pharmacy                                1,790.80",
        "Grand Total                            1,234,567.89",
        "",
    ] if with_summary else []

    ot = ["OT Pharmacy(999311 )"]
    for day, amount in enumerate(blocks, start=1):
        ot += [f"0{day}/02/2025",
               f"1  CGHS INJ PROPOFOL  OTP01  1  223.85  {amount:,.2f}",
               f"Dept Sub Total : {amount:,.2f}"]
    ot += [f"Dept Total : {sum(blocks):,.2f}", ""]

    second_ip = [
        "IP Pharmacy(999311 )",
        "1  CGHS NON-FORMULARY DRUG  IPP99  1  31599.22  31,599.22",
        "Dept Sub Total : 31,599.22",
        "Dept Total : 31,599.22",
        "Patient Payable Total : 31,599.00",
    ] if with_second_ip_section else []

    return "\n".join(head + [
        "IP Pharmacy(999311 )",
        "1  CGHS TAB PARACETAMOL  IPP01  10  12.00  120.00",
        "2  CGHS INJ MEROPENEM    IPP02  40  1250.00  50,000.00",
        "Dept Sub Total : 742,104.38",
        "Dept Total : 742,104.38",
        "",
    ] + ot + second_ip)


def _parse_text(text):
    return CGHSParsingEngine().parse_document([[]], text, "<39538>")


def _drug100(text):
    final, _raw, _name, _rejected, _log = _parse_text(text)
    return [i for i in final if i["code"] == "DRUG100"]


IPP, OTP = r'IP\s*Pharmacy', r'OT\s*Pharmacy'


# --- 1/2. the two IP Pharmacy sections are both seen, and distinguished ----

def test_39538_main_ip_pharmacy_subtotal():
    entries = rules.collect_dept_subtotals(_bill_39538_text(), IPP)
    assert entries, "the IP Pharmacy sections must be found"
    assert entries[0]["amount"] == pytest.approx(IP_MAIN_SUBTOTAL_39538, abs=0.005)
    assert entries[0]["included"] is True


def test_39538_second_ip_pharmacy_subtotal_is_seen_not_discarded():
    entries = rules.collect_dept_subtotals(_bill_39538_text(), IPP)
    seconds = [e for e in entries
               if e["amount"] == pytest.approx(IP_SECOND_SUBTOTAL_39538, abs=0.005)]
    assert seconds, (
        "the second IP Pharmacy section must be SEEN - silently never "
        "noticing it is the failure mode this test exists to prevent")
    assert seconds[0]["included"] is False
    assert "Patient Payable" in seconds[0]["reason"]


def test_39538_second_ip_section_is_classified_as_ip_pharmacy():
    """It is a second IP Pharmacy(999311) block, NOT another department."""
    text = _bill_39538_text()
    assert text.count("IP Pharmacy(999311 )") == 2
    for other in (r'Ward\s*Consumables', r'OT\s*Consumables', r'\bConsumables\b'):
        assert not [e for e in rules.collect_dept_subtotals(text, other)
                    if e["amount"] == pytest.approx(IP_SECOND_SUBTOTAL_39538,
                                                    abs=0.005)], (
            "31,599.22 must not be relabelled as a consumables department")


def test_39538_two_ip_sections_sum_to_the_summary_figure():
    entries = rules.collect_dept_subtotals(_bill_39538_text(), IPP)
    assert round(sum(e["amount"] for e in entries), 2) == pytest.approx(
        IP_PHARMACY_SUMMARY_39538, abs=0.005), (
        "742,104.38 + 31,599.22 = 773,703.60 is why the summary is larger")


# --- 3. the first-page summary, which carries no caption ------------------

def test_39538_ip_pharmacy_summary_without_a_service_summary_caption():
    text = _bill_39538_text()
    assert "Service Summary" not in text, "this bill has no such caption"
    assert rules.extract_service_summary_amount(text, IPP) == pytest.approx(
        IP_PHARMACY_SUMMARY_39538, abs=0.005)
    assert rules.extract_service_summary_amount(text, OTP) == pytest.approx(
        OT_PHARMACY_39538, abs=0.005)


def test_summary_is_not_read_when_there_are_no_department_sections():
    """Structural span only means something if detailed sections exist."""
    assert rules.extract_service_summary_amount(
        "IP Pharmacy 773,703.60\n", IPP) is None


# --- 4/5. OT Pharmacy aggregates its dated blocks -------------------------

def test_39538_ot_pharmacy_dated_blocks_aggregate_to_1790_80():
    entries = rules.collect_dept_subtotals(_bill_39538_text(), OTP)
    assert [e["amount"] for e in entries] == pytest.approx(
        OT_DATED_SUBTOTALS_39538, abs=0.005)
    assert rules.extract_dept_subtotal(_bill_39538_text(), OTP) == pytest.approx(
        OT_PHARMACY_39538, abs=0.005)


def test_ot_pharmacy_is_not_first_match_only():
    """The defect: reading block 1 only returned 223.85 of 1,790.80."""
    got = rules.extract_dept_subtotal(_bill_39538_text(), OTP)
    assert got != pytest.approx(OT_DATED_SUBTOTALS_39538[0], abs=0.005), (
        "only the first dated block was read")
    assert got == pytest.approx(sum(OT_DATED_SUBTOTALS_39538), abs=0.005)


def test_ot_aggregate_matches_the_summary_statement():
    text = _bill_39538_text()
    assert rules.extract_dept_subtotal(text, OTP) == pytest.approx(
        rules.extract_service_summary_amount(text, OTP), abs=0.005)


@pytest.mark.parametrize("blocks", [[100.00], [100.00, 200.00],
                                    [10.00, 20.00, 30.00, 40.00]])
def test_any_number_of_dated_blocks_aggregates(blocks):
    text = _bill_39538_text(with_summary=False, ot_blocks=blocks)
    assert rules.extract_dept_subtotal(text, OTP) == pytest.approx(
        sum(blocks), abs=0.005)


# --- 6. no cross-department contamination ---------------------------------

def test_no_cross_department_subtotal_contamination():
    text = _bill_39538_text()
    ip = rules.extract_dept_subtotal(text, IPP)
    ot = rules.extract_dept_subtotal(text, OTP)
    assert ip == pytest.approx(IP_MAIN_SUBTOTAL_39538, abs=0.005), (
        "IP Pharmacy absorbed another department's subtotals")
    assert ot == pytest.approx(OT_PHARMACY_39538, abs=0.005), (
        "OT Pharmacy absorbed another department's subtotals")
    assert ip + ot != pytest.approx(
        IP_MAIN_SUBTOTAL_39538 + IP_SECOND_SUBTOTAL_39538 + OT_PHARMACY_39538,
        abs=0.005), "a global sum of every Dept Sub Total was taken"


def test_a_department_is_not_summed_twice_when_named_twice():
    """Overlapping slices must not double count the same subtotal label."""
    text = ("IP Pharmacy(999311 )\n"
            "1 IP Pharmacy dispensing fee  IPP01 1 1.00 1.00\n"
            "Dept Sub Total : 500.00\n")
    assert rules.extract_dept_subtotal(text, IPP) == pytest.approx(500.00, abs=0.005)


# --- 7. Patient Payable is never substituted for a department subtotal ----

def test_patient_payable_is_not_used_as_the_department_subtotal():
    entries = rules.collect_dept_subtotals(_bill_39538_text(), IPP)
    for entry in entries:
        assert entry["amount"] != pytest.approx(31599.00, abs=0.005), (
            "the Patient Payable Total was read as a department subtotal")
    assert rules.extract_dept_subtotal(
        "Patient Payable\nDept Sub Total : 999.00\n", r'Patient\s*Payable') == 0.0


def test_patient_payable_amount_is_excluded_from_drug100():
    drug = _drug100(_bill_39538_text())
    assert drug[0]["amount"] == pytest.approx(DRUG100_39538, abs=0.005)
    assert drug[0]["amount"] != pytest.approx(
        DRUG100_39538 + IP_SECOND_SUBTOTAL_39538, abs=0.005)


# --- 8. the 500000 ceiling stays gone -------------------------------------

@pytest.mark.parametrize("amount", [499999.99, 500000.00, 500000.01,
                                    742104.38, 773703.60, 1250000.00])
def test_dept_subtotal_has_no_arbitrary_upper_bound(amount):
    text = (f"IP Pharmacy(999311 )\n1 X A1 1 1.00 1.00\n"
            f"Dept Sub Total : {amount:,.2f}\n")
    assert rules.extract_dept_subtotal(text, IPP) == pytest.approx(
        amount, abs=0.005), f"{amount:,.2f} was dropped by a magnitude cutoff"


def test_consumables_total_has_no_arbitrary_upper_bound():
    text = ("OT Consumables(999311 )\n1 STENT C1 1 1.00 1.00\n"
            "Dept Sub Total : 600,000.00\n")
    assert rules.extract_consumables_total(text)[0] == pytest.approx(
        600000.00, abs=0.005)


def test_39538_ip_is_not_zeroed_and_drug100_is_not_ot_only():
    """The original live defect, still pinned."""
    assert rules.extract_dept_subtotal(_bill_39538_text(), IPP) != 0.0
    assert _drug100(_bill_39538_text())[0]["amount"] != pytest.approx(
        OT_PHARMACY_39538, abs=0.005)


# --- 9. DRUG100 arithmetic matches the locked rule ------------------------

def test_39538_drug100_is_exactly_743895_18():
    drug = _drug100(_bill_39538_text())
    assert drug, "DRUG100 must be produced for 39538"
    assert drug[0]["amount"] == pytest.approx(DRUG100_39538, abs=0.005)
    assert drug[0]["qty"] == 1, "an amount must never become a unit quantity"


def test_39538_drug100_equals_the_locked_rule_arithmetic():
    expected = IP_MAIN_SUBTOTAL_39538 + sum(OT_DATED_SUBTOTALS_39538)
    assert expected == pytest.approx(DRUG100_39538, abs=0.005)
    assert rules.reconcile_pharmacy(_bill_39538_text())["total"] == pytest.approx(
        expected, abs=0.005)


def test_39538_drug100_is_executable_with_no_review_state():
    final, _raw, _name, rejected, _log = _parse_text(_bill_39538_text())
    assert [i for i in final if i["code"] == "DRUG100"]
    assert not [r for r in rejected if r.get("code") == "DRUG100"], (
        "no unexplained REVIEW state")


def test_39538_drug100_is_never_the_summary_total():
    drug = _drug100(_bill_39538_text())
    for wrong in (IP_PHARMACY_SUMMARY_39538,
                  IP_PHARMACY_SUMMARY_39538 + OT_PHARMACY_39538,
                  1234567.89):
        assert drug[0]["amount"] != pytest.approx(wrong, abs=0.005)


# --- 10. provenance -------------------------------------------------------

def test_provenance_lists_every_contributing_subtotal():
    ev = rules.reconcile_pharmacy(_bill_39538_text())
    included = [p for p in ev["provenance"] if p.get("included")]
    amounts = sorted(p["amount"] for p in included)
    assert amounts == pytest.approx(
        sorted([IP_MAIN_SUBTOTAL_39538] + OT_DATED_SUBTOTALS_39538), abs=0.005), (
        "every included subtotal must appear individually")
    assert round(sum(amounts), 2) == pytest.approx(DRUG100_39538, abs=0.005)


def test_provenance_records_the_excluded_patient_payable_section():
    ev = rules.reconcile_pharmacy(_bill_39538_text())
    assert ev["excluded"], "an excluded amount must be reported, not dropped"
    entry = ev["excluded"][0]
    assert entry["label"] == "IP Pharmacy"
    assert entry["amount"] == pytest.approx(IP_SECOND_SUBTOTAL_39538, abs=0.005)
    assert "Patient Payable" in entry["reason"]


def test_provenance_carries_the_exact_arithmetic():
    ev = rules.reconcile_pharmacy(_bill_39538_text())
    assert ev["arithmetic"].endswith(f"= {DRUG100_39538:,.2f}")
    for part in ("742,104.38", "223.85", "447.70"):
        assert part in ev["arithmetic"]
    assert "31,599.22" in ev["reason"] and "excluded" in ev["reason"]


def test_every_provenance_row_names_its_source():
    ev = rules.reconcile_pharmacy(_bill_39538_text())
    for p in ev["provenance"]:
        assert p["source"] in {"detailed_dept_subtotal", "excluded_dept_subtotal",
                               "service_summary"}
        assert p["detail"]


# --- 12. earlier validated work is untouched ------------------------------

def test_c844823_row_recognition_and_duplicate_accounting_are_unchanged():
    from cghs.dom import PROBE_JS, ROW_CODE_PATTERN
    import cghs.controllers as controllers
    import cghs.orchestrator as orchestrator
    import inspect
    assert ROW_CODE_PATTERN in PROBE_JS
    for code in ("AG008", "BC002", "EP092", "MG001", "NS064", "NU110", "NU122"):
        assert controllers._ROW_CODE_RE.search(code)
    assert "DUPLICATE_PROVEN" in inspect.getsource(orchestrator.BatchRunner)


# ===========================================================================
# F. REAL PDF STRUCTURAL REGRESSION - 39538.pdf obtained from Git
#
# PROVENANCE (verified, not asserted from memory):
#   git path   39538.pdf on origin/main, commit b5987f4
#   blob       423a9ea630136c55d1e06ea07f1bdf39982d6ce3
#   sha256     1825f727981314ec5b6241f967ee9e26ee1bd76e4cbac96b5ceb4404da04993a
#   in-repo    tests/fixtures/39538.pdf (byte-identical copy)
#
# Unlike section E, these tests load the REAL document.  They are the pre-fix
# reproduction required before production code may be changed, and the
# old-vs-new evidence afterwards.  The amounts here are EVIDENCE values used to
# validate extraction; none of them is permitted into cghs/ (see
# test_case_specific_amounts_are_not_hardcoded_in_the_package).
# ===========================================================================

REAL_39538_SHA256 = (
    "1825f727981314ec5b6241f967ee9e26ee1bd76e4cbac96b5ceb4404da04993a")
REAL_39538_GIT_BLOB = "423a9ea630136c55d1e06ea07f1bdf39982d6ce3"

#: Page-1 Service Summary, read off the real document.
REAL_39538_SUMMARY = {
    "Blood Bank Procedure": 9450.00,
    "Consultation": 29750.00,
    "Equipment": 79920.00,
    "Hospital services (others)": 1260.00,
    "Investigations": 68984.00,
    "IP Pharmacy": 773703.60,
    "Non Invasive Procedure": 98341.75,
    "OT Consumables": 10022.00,
    "OT Pharmacy": 1790.80,
    "Physiotherapy": 19790.00,
    "Profile": 18167.00,
    "Room Rent": 156600.00,
    "Ward Consumables": 11874.44,
}
REAL_39538_PAYER_TOTAL = 1248055.00
REAL_39538_PATIENT_TOTAL = 31599.00
REAL_39538_GRAND_TOTAL = 1279654.00


def _real_39538_path():
    return _find_bill("39538")


def _real_pymupdf():
    """The REAL PyMuPDF.

    ``tests.support.legacy_loader`` installs a stub under the name ``fitz`` so
    the c3ccdf3 baseline can be imported without PyMuPDF.  ``pytest.importorskip
    ("fitz")`` would therefore hand back that stub and the real document would
    never be opened.  The genuine library is imported under its current name.
    """
    return pytest.importorskip("pymupdf")


@contextlib.contextmanager
def _real_fitz_installed():
    """Let production's lazy ``import fitz`` resolve to the real library."""
    real = _real_pymupdf()
    saved = sys.modules.get("fitz")
    sys.modules["fitz"] = real
    try:
        yield
    finally:
        if saved is None:
            del sys.modules["fitz"]
        else:
            sys.modules["fitz"] = saved


def _real_39538_text():
    path = _real_39538_path()
    if path is None:
        pytest.skip("ENVIRONMENT_BLOCKED: 39538.pdf is not in the repository")
    doc = _real_pymupdf().open(str(path))
    return "\n".join(page.get_text() for page in doc)


def _dept_pattern(name: str) -> str:
    """Department name -> the pattern callers use.  No fuzzy matching."""
    return re.escape(name).replace(r"\ ", r"\s*")


def test_real_39538_is_the_committed_git_bill():
    """The analysed file must be the one in Git, byte for byte."""
    path = _real_39538_path()
    if path is None:
        pytest.skip("ENVIRONMENT_BLOCKED: 39538.pdf is not in the repository")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert digest == REAL_39538_SHA256, (
        f"{path} is not the committed bill (sha256 {digest})")


def test_real_39538_has_the_expected_page_count():
    path = _real_39538_path()
    if path is None:
        pytest.skip("ENVIRONMENT_BLOCKED: 39538.pdf is not in the repository")
    assert _real_pymupdf().open(str(path)).page_count == 95


def test_real_39538_has_no_service_summary_caption():
    """Defect C premise: the real summary table carries no caption at all."""
    assert "Service Summary" not in _real_39538_text()


def test_real_39538_summary_span_covers_the_summary_table():
    """PRE-FIX: the span collapses to the patient header block."""
    text = _real_39538_text()
    span = rules._service_summary_span(text)
    assert span is not None
    block = text[span[0]:span[1]]
    for dept in REAL_39538_SUMMARY:
        assert dept in block, (
            f"the summary span does not contain the {dept!r} summary row")


def test_real_39538_service_summary_ip_pharmacy_is_read():
    """PRE-FIX: returns None - the amount sits on the line after the label."""
    assert rules.extract_service_summary_amount(
        _real_39538_text(), IPP) == pytest.approx(773703.60, abs=0.005)


def test_real_39538_service_summary_ot_pharmacy_is_read():
    assert rules.extract_service_summary_amount(
        _real_39538_text(), OTP) == pytest.approx(1790.80, abs=0.005)


def test_real_39538_every_summary_line_is_readable():
    text = _real_39538_text()
    for dept, amount in REAL_39538_SUMMARY.items():
        assert rules.extract_service_summary_amount(
            text, _dept_pattern(dept)) == pytest.approx(amount, abs=0.005), (
                f"summary line for {dept!r} was not read")


def test_real_39538_main_ip_pharmacy_subtotal_is_the_payer_section():
    entries = rules.collect_dept_subtotals(_real_39538_text(), IPP)
    included = [e for e in entries if e["included"]]
    assert len(included) == 1
    assert included[0]["amount"] == pytest.approx(742104.38, abs=0.005)


def test_real_39538_second_ip_section_is_seen_and_excluded_with_a_reason():
    entries = rules.collect_dept_subtotals(_real_39538_text(), IPP)
    excluded = [e for e in entries if not e["included"]]
    assert len(excluded) == 1, "the patient-payable IP section was discarded"
    assert excluded[0]["amount"] == pytest.approx(31599.22, abs=0.005)
    assert "payable" in excluded[0]["reason"].lower()


def test_real_39538_two_ip_sections_sum_to_the_summary_line():
    entries = rules.collect_dept_subtotals(_real_39538_text(), IPP)
    assert sum(e["amount"] for e in entries) == pytest.approx(
        REAL_39538_SUMMARY["IP Pharmacy"], abs=0.005)


def test_real_39538_ot_pharmacy_aggregates_its_five_dated_blocks():
    entries = rules.collect_dept_subtotals(_real_39538_text(), OTP)
    assert [e["amount"] for e in entries] == [
        pytest.approx(v, abs=0.005)
        for v in (223.85, 447.70, 447.70, 447.70, 223.85)]
    assert rules.extract_dept_subtotal(
        _real_39538_text(), OTP) == pytest.approx(1790.80, abs=0.005)


def test_real_39538_ot_is_not_first_match_only_and_not_a_global_sum():
    text = _real_39538_text()
    ot = rules.extract_dept_subtotal(text, OTP)
    assert ot != pytest.approx(223.85, abs=0.005), "first-match-only bug"
    assert ot == pytest.approx(REAL_39538_SUMMARY["OT Pharmacy"], abs=0.005)
    assert ot < sum(REAL_39538_SUMMARY.values()), "a global sum was taken"


def test_real_39538_consultation_is_not_built_from_a_row_description():
    """PRE-FIX: 'PHYSIOTHERAPY CONSULTATION' on p81 fabricates a section."""
    assert rules.extract_dept_subtotal(
        _real_39538_text(), _dept_pattern("Consultation")) == pytest.approx(
            REAL_39538_SUMMARY["Consultation"], abs=0.005)


def test_real_39538_equipment_stops_at_a_parenthesised_department():
    """PRE-FIX: 'Hospital services (others)(999311 )' is not recognised."""
    assert rules.extract_dept_subtotal(
        _real_39538_text(), _dept_pattern("Equipment")) == pytest.approx(
            REAL_39538_SUMMARY["Equipment"], abs=0.005)


def test_real_39538_ward_consumables_survives_the_payer_payable_close():
    """PRE-FIX: the payer region's CLOSING total zeroes the last department."""
    assert rules.extract_dept_subtotal(
        _real_39538_text(), _dept_pattern("Ward Consumables")) == pytest.approx(
            REAL_39538_SUMMARY["Ward Consumables"], abs=0.005)


def test_real_39538_every_payer_department_matches_its_summary_line():
    """No department may absorb, lose, or invent money."""
    text = _real_39538_text()
    wrong = {}
    for dept, amount in REAL_39538_SUMMARY.items():
        got = rules.extract_dept_subtotal(text, _dept_pattern(dept))
        expected = 742104.38 if dept == "IP Pharmacy" else amount
        if abs(got - expected) > 0.005:
            wrong[dept] = (expected, got)
    assert not wrong, f"departments misread: {wrong}"


def test_real_39538_payer_departments_reconcile_to_the_payer_payable_total():
    """Structural proof that the region model is right, to the rupee."""
    text = _real_39538_text()
    total = sum(rules.extract_dept_subtotal(text, _dept_pattern(d))
                for d in REAL_39538_SUMMARY)
    assert round(total) == pytest.approx(REAL_39538_PAYER_TOTAL, abs=1.0)
    assert REAL_39538_PAYER_TOTAL + REAL_39538_PATIENT_TOTAL == pytest.approx(
        REAL_39538_GRAND_TOTAL, abs=0.005)


def test_real_39538_drug100_follows_the_locked_rule():
    ev = rules.reconcile_pharmacy(_real_39538_text())
    assert ev["total"] == pytest.approx(743895.18, abs=0.005)
    assert ev["total"] == pytest.approx(742104.38 + 1790.80, abs=0.005)
    assert ev["total"] != pytest.approx(
        743895.18 + 31599.22, abs=0.005), "patient-payable money leaked in"


def test_real_39538_drug100_provenance_names_every_amount():
    ev = rules.reconcile_pharmacy(_real_39538_text())
    amounts = [p["amount"] for p in ev["provenance"]]
    for value in (742104.38, 223.85, 447.70, 31599.22):
        assert any(abs(a - value) < 0.005 for a in amounts), (
            f"{value} is missing from provenance")
    assert any(not p["included"] for p in ev["provenance"])
    for p in ev["provenance"]:
        assert p["detail"] and p["source"]


def test_real_39538_drug100_records_the_summary_cross_check():
    """PRE-FIX: both summary readings are None, so the check disappears."""
    ev = rules.reconcile_pharmacy(_real_39538_text())
    assert ev["ip_summary"] == pytest.approx(773703.60, abs=0.005)
    assert ev["ot_summary"] == pytest.approx(1790.80, abs=0.005)


def test_real_39538_drug100_is_executable_with_no_review_state():
    text = _real_39538_text()
    final, _raw, _name, _rejected, _log = CGHSParsingEngine().parse_document(
        [[]], text, "<39538>")
    drug = [i for i in final if i["code"] == "DRUG100"]
    assert len(drug) == 1
    blob = str(drug[0]).upper()
    assert "REVIEW" not in blob


def test_real_39538_production_parse_preserves_the_locked_invariants():
    """The full production path, not a helper."""
    path = _real_39538_path()
    if path is None:
        pytest.skip("ENVIRONMENT_BLOCKED: 39538.pdf is not in the repository")
    with _real_fitz_installed():
        final, _raw, _name, _rejected, log = CGHSParsingEngine().parse(str(path))
    qty = {i["code"]: i["qty"] for i in final}
    assert qty["CN002"] == 87
    assert qty["CC001"] == 29
    assert qty["CC002"] == 108
    assert qty["BC002"] == 3
    assert qty["PT004"] == 19
    assert qty["PT005"] == 53
    assert "CC003" not in qty, "C003 must stay REVIEW_REQUIRED"
    assert any("743895.18" in str(line) for line in log)
    assert any("21896.44" in str(line) for line in log)


def test_real_39538_resolved_codes_are_all_in_the_1998_registry():
    """No hallucinated code may be produced from the real document."""
    path = _real_39538_path()
    if path is None:
        pytest.skip("ENVIRONMENT_BLOCKED: 39538.pdf is not in the repository")
    from cghs.locators import CGHS_CODE_REGISTRY
    with _real_fitz_installed():
        final, _raw, _name, _rejected, _log = CGHSParsingEngine().parse(str(path))
    for item in final:
        code = item["code"]
        if code in ("DRUG100", "CNSU100", "CC001", "WC001", "CN002"):
            continue
        assert code in CGHS_CODE_REGISTRY, f"{code} is not a registry record"


def test_real_39538_compound_code_splits_into_registry_components():
    """CGHS-L B042+043+044-2025 wraps over FOUR lines in the real document."""
    occ = rules.resolve_cghs_codes(
        "CGHS-L B042+043+044-2025", service_name="VIRAL MARKER PROFILE")
    assert [o["final_code"] for o in occ] == ["LB042", "LB043", "LB044"]
    assert all(o["status"] == rules.EXECUTABLE for o in occ)


def test_real_39538_ventilator_rows_stay_review_required():
    """26 CGHS-C C003 rows must never be guessed into CC003."""
    occ = rules.resolve_cghs_codes(
        "CGHS-C C003-2025", service_name="VENTILATOR CHARGES FULL DAY")
    assert all(o["status"] == rules.REVIEW_REQUIRED for o in occ)
    assert all(o["final_code"] is None for o in occ)


# ---------------------------------------------------------------------------
# F2. GENERALISATION - same grammar, different identity/amounts/dates/pages
#
# The goal is not "39538 passes" but "a bill of this format parses".  These
# documents reuse the REAL grammar discovered above (three-line captionless
# summary rows, parenthesised department names, payer/patient region markers,
# dated OT blocks) with different patients, amounts, dates and section counts.
# ---------------------------------------------------------------------------

def _format_bill(departments, *, patient="Ms. A N OTHER", ip_no="BPLIP00001",
                 patient_payable=None, page_breaks=True):
    """Build a document in the grammar proven from the real PDF."""
    summary, detail = [], []
    for index, (name, amount, body) in enumerate(departments, start=1):
        summary += [f"{name}(999311 )", f" {amount:,.2f}", f" {index}"]
        detail += [f"{name}(999311 )"] + body
        if page_breaks:
            detail += [f"Page {index + 1} of 99"]
    head = [f"Name  :", patient, "IP Number:", f"*{ip_no}*", "Payer Payable",
            "Dis (%)"]
    tail = ["Payer Payable Total :", f" {sum(d[1] for d in departments):,.2f}"]
    if patient_payable:
        name, amount, body = patient_payable
        tail += ["Patient Payable", "Start ", "Date", f"{name}(999311 )"]
        tail += body + ["Patient Payable  Total :", f" {amount:,.2f}"]
    return "\n".join(head + summary + ["Page 1 of 99"] + detail + tail)


def _section(subtotals, total=None, dated=False):
    """A detailed section in the grammar the real PDF uses.

    Each block opens with a SUBSECTION label - a date for dated blocks, a
    discipline otherwise - exactly as pages 3-94 of the real document do.  The
    amount PRECEDES "Dept Sub Total :" and FOLLOWS "Dept Total :", which is the
    layout the real report emits.
    """
    out = []
    for n, amount in enumerate(subtotals, start=1):
        out.append(f"{n:02d}-Mar-2031" if dated else "Some Discipline")
        out += [" 1.00 ", "12345", f" {amount:,.2f}",
                f" {n}", " SOME SERVICE ITEM              ",
                f" {amount:,.2f}", "Dept Sub Total :"]
    out += ["Dept Total :", f" {total if total is not None else sum(subtotals):,.2f}"]
    return out


def test_generalised_bill_parses_with_different_identity_and_amounts():
    text = _format_bill([
        ("IP Pharmacy", 11111.11, _section([11111.11])),
        ("OT Pharmacy", 606.00, _section([101.00] * 6, dated=True)),
    ], patient="Mr. Q Z EXAMPLE", ip_no="BPLIP77777")
    assert rules.extract_dept_subtotal(text, IPP) == pytest.approx(11111.11)
    assert rules.extract_dept_subtotal(text, OTP) == pytest.approx(606.00)
    assert rules.reconcile_pharmacy(text)["total"] == pytest.approx(11717.11)


def test_generalised_captionless_three_line_summary_is_read():
    """PRE-FIX: the real summary shape is unreadable."""
    text = _format_bill([
        ("IP Pharmacy", 2500.00, _section([2500.00])),
        ("OT Pharmacy", 900.00, _section([300.00] * 3, dated=True)),
    ])
    assert rules.extract_service_summary_amount(text, IPP) == pytest.approx(2500.00)
    assert rules.extract_service_summary_amount(text, OTP) == pytest.approx(900.00)


def test_generalised_last_payer_department_may_be_a_pharmacy():
    """PRE-FIX: the payer close zeroes whichever department comes last.

    This is the future-bill corruption the Ward Consumables defect proves.
    """
    text = _format_bill([
        ("Room Rent", 5000.00, _section([5000.00])),
        ("IP Pharmacy", 98765.43, _section([98765.43])),
    ])
    assert rules.extract_dept_subtotal(text, IPP) == pytest.approx(98765.43), (
        "the last payer-side department was discarded")
    assert rules.reconcile_pharmacy(text)["total"] == pytest.approx(98765.43)


def test_generalised_row_description_naming_a_department_is_not_a_section():
    """PRE-FIX: a row that merely mentions a department fabricates one."""
    text = _format_bill([
        ("IP Pharmacy", 1000.00, _section([1000.00])),
        ("Physiotherapy", 4000.00,
         [" 1.00 ", "999", " 4,000.00", " 1",
          " IP PHARMACY LIAISON REVIEW              ", " 4,000.00",
          "Dept Sub Total :", "Dept Total :", " 4,000.00"]),
    ])
    assert rules.extract_dept_subtotal(text, IPP) == pytest.approx(1000.00), (
        "a Physiotherapy row naming IP Pharmacy was counted as pharmacy")


def test_generalised_parenthesised_department_bounds_the_previous_one():
    """PRE-FIX: a parenthesised name is invisible, so boundaries vanish."""
    text = _format_bill([
        ("Equipment", 700.00, _section([350.00, 350.00])),
        ("Hospital services (others)", 60.00, _section([60.00])),
        ("IP Pharmacy", 800.00, _section([800.00])),
    ])
    assert rules.extract_dept_subtotal(
        text, _dept_pattern("Equipment")) == pytest.approx(700.00)
    assert rules.extract_dept_subtotal(
        text, _dept_pattern("Hospital services (others)")) == pytest.approx(60.00)


def test_generalised_patient_payable_region_is_excluded_by_position():
    text = _format_bill(
        [("IP Pharmacy", 3000.00, _section([3000.00]))],
        patient_payable=("IP Pharmacy", 250.00, _section([250.00])))
    entries = rules.collect_dept_subtotals(text, IPP)
    assert len(entries) == 2, "both occurrences must stay distinguishable"
    assert [e["included"] for e in entries] == [True, False]
    assert rules.reconcile_pharmacy(text)["total"] == pytest.approx(3000.00)


@pytest.mark.parametrize("blocks", [1, 2, 5, 9, 14])
def test_generalised_any_number_of_dated_ot_blocks_aggregates(blocks):
    text = _format_bill([
        ("IP Pharmacy", 100.00, _section([100.00])),
        ("OT Pharmacy", 50.0 * blocks, _section([50.00] * blocks, dated=True)),
    ])
    assert rules.extract_dept_subtotal(text, OTP) == pytest.approx(50.0 * blocks)


def test_generalised_summary_lines_are_never_added_to_the_detail():
    text = _format_bill([("IP Pharmacy", 4321.00, _section([4321.00]))])
    assert rules.extract_dept_subtotal(text, IPP) == pytest.approx(4321.00), (
        "the summary line was added to the detailed subtotal")
