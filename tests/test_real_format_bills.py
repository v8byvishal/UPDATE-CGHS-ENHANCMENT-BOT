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
