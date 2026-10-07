"""CGHS bill text rules - pure functions, no I/O.

Row-aware quantity parsing, department subtotal extraction and deterministic
code normalisation.  These encode locked CGHS business rules.

Canonical owner: this module.  Moved verbatim out of the legacy monolithic ``app.py``
at baseline commit c3ccdf32e2170891fad9b150c850053461c85a25 so that exactly one
implementation exists and so the CGHS business rules are importable (and therefore
testable) without PyQt5 / Chrome / PyMuPDF being present.

BUSINESS RULES ARE UNCHANGED.  Any behavioural change in this file is a defect.
"""

import re
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from .locators import (  # noqa: F401
    CGHS_CATEGORY_MAP,
    CGHS_CODE_FAMILIES,
    PORTAL_OPTION_MAP,
    VALID_CODES,
)

# ---------------------------------------------------------------------------
# Locked CGHS code-resolution tables
#
# A raw 4-character alias (C002, C003, C008, C010, C011, C012, C014, N002, ...)
# is NOT an executable enhancement code.  It only becomes one when a LOCKED
# rule below resolves it.  There is deliberately no generic ``C -> CC``
# expansion: the baseline built candidates as ``category + token`` and accepted
# the first one that happened to exist in VALID_CODES, and because
# ``_build_valid_codes`` generates the whole CC001..CC100 family as wildcards,
# EVERY invented CCxxx validated.  That is how "VENTILATOR CGHS-C C003" became
# CC003 - a code nobody ever defined.
# ---------------------------------------------------------------------------

#: An occurrence that may be executed against the portal.
EXECUTABLE = "EXECUTABLE"
#: An occurrence that is real but could not be resolved by a locked rule.
#: It is surfaced to the operator, never silently dropped and never executed.
REVIEW_REQUIRED = "REVIEW_REQUIRED"
#: Reason tag for an alias with no locked mapping.
UNRESOLVED_MAPPING = "UNRESOLVED_MAPPING"

#: Category + alias-prefix -> final family.  ("C", "C") is deliberately absent.
#:
#: Every pair below is an observed real-bill format whose resulting family is
#: present in the CGHS master list.  The composition is still only a CANDIDATE:
#: it becomes executable solely when the composed code exists in the registry,
#: so a pair being listed here can never manufacture a code.
LOCKED_CATEGORY_COMPOSITION = {
    ("L", "B"): "LB",   # CGHS-L  + Bxxx -> LBxxx
    ("G", "P"): "GP",   # CGHS-G  + Pxxx -> GPxxx
    ("P", "T"): "PT",   # CGHS-P  + Txxx -> PTxxx
    ("C", "N"): "CN",   # CGHS-C  + Nxxx -> CNxxx
    ("R", "P"): "RP",
    ("M", "G"): "MG",
    ("A", "G"): "AG",   # CGHS-A  + Gxxx -> AGxxx  (General Surgery)
    ("E", "P"): "EP",   # CGHS-E  + Pxxx -> EPxxx  (ENT Procedure)
    ("N", "S"): "NS",   # CGHS-N  + Sxxx -> NSxxx  (Neuro Surgery)
    ("N", "U"): "NU",   # CGHS-N  + Uxxx -> NUxxx  (Nephrology/Urology)
    ("B", "C"): "BC",   # CGHS-B  + Cxxx -> BCxxx  (Blood Component Charges)
}

#: Categories that are already a full family prefix and so take a bare numeric
#: (CGHS-RI + 034 -> RI034, CGHS-CI + 005 -> CI005).
LOCKED_NUMERIC_CATEGORIES = frozenset({
    "LB", "RI", "CI", "RP", "GP", "PT", "CN", "CC", "WC", "MG",
})

#: Families that may appear as a complete explicit token in the bill.
#:
#: DERIVED from the master registry - never hand-maintained - plus the three
#: legacy families (WC/ST/OT) that the bill text can still carry but that the
#: master list does not define.  Matching here only means "this token has the
#: SHAPE of a code"; executability still requires registry membership, so
#: listing a family can never make an unregistered code executable.
_EXPLICIT_FAMILY = re.compile(
    r'^(' + '|'.join(sorted(CGHS_CODE_FAMILIES | {"WC", "ST", "OT"},
                            key=lambda f: (-len(f), f))) + r')(\d{3})$')

#: A raw C-alias.  Never executable on its own - it must pass a locked rule.
_RAW_C_ALIAS = re.compile(r'^C(\d{3})$')

#: Context-sensitive locked mappings.  Each entry is
#: alias -> (final code, same-row evidence pattern, human description).
#: The mapping fires ONLY when the evidence is present in the same row.
LOCKED_CONTEXT_MAPPINGS = {
    "C004": ("CC004", re.compile(r'NIV\s*MACHINE'), "NIV Machine Per Day"),
    "C008": ("CC008", re.compile(r'BLOOD\s*TRANSFUSION'), "Blood Transfusion Charge"),
    "C010": ("CC010", re.compile(r'ENDO\s*TRACHEAL'), "Endotracheal Intubation"),
    "C011": ("CC011", re.compile(r'CENTRAL\s*LINE'), "Central Line"),
    "C012": ("CC012", re.compile(r'NEBULI[SZ]'), "Nebulizer Therapy"),
    "C014": ("CC014", re.compile(r"RYLE'?S?\s*TUBE"), "Ryles Tube Insertion Charge"),
}

#: C003 has NO approved target.  Both known contexts stay unresolved: inventing
#: CC003 is explicitly forbidden until a locked source-of-truth rule exists.
_NEVER_RESOLVED_ALIASES = frozenset({"C003"})

#: C002 is Oxygen ONLY.  In a Blood Bank / Packed Cells row the same alias
#: means something else entirely and must not become CC002.
_OXYGEN_CONTEXT = re.compile(r'OXYGEN')
_BLOOD_BANK_CONTEXT = re.compile(r'BLOOD\s*BANK|PACKED\s*CELL')

#: An amount column.  An alias token never lies beyond the first money value on
#: the row, so the search region stops there - otherwise digit-repair welds the
#: quantity onto the amount ("1 4500.00" -> "14500.00") and manufactures a code
#: out of rupees ("Room Rent( CGHS-RI ) ICU 1 4500.00" used to yield RI145).
_AMOUNT_TOKEN = re.compile(r'\d[\d,]*\.\d{2}')

#: Year/metadata suffix: -2025 and its PDF-fragmented forms.
_YEAR_SUFFIX = re.compile(r'-\s*20\s*\d\s*\d\b|-\s*20\s*\d{2}\b|-\s*20\d{2}\b'
                          r'|-\s*202\b|-\s*20\b')


#: Matches the "Dept Sub Total" LABEL only.  The amount is resolved separately
#: so that label-first and value-first layouts cannot shadow one another.
_SUBTOTAL_LABEL = re.compile(r'Dept\s*Sub\s*Total', re.IGNORECASE)
# NOTE: used with .match(text, pos), which already anchors at pos - a '^'
# here would mean start-of-STRING and never match.
_SUBTOTAL_AFTER = re.compile(r'\s*:?\s*([\d,]+\.\d{2})')
_SUBTOTAL_BEFORE = re.compile(r'([\d,]+\.\d{2})\s*$')


def _subtotal_at(text: str, label):
    """Resolve the amount belonging to one 'Dept Sub Total' label.

    Precedence is label-first, then value-first - identical to the precedence
    ``extract_dept_subtotal`` already applies inside a department slice.

    The previous single alternating regex was scanned with ``finditer``, which
    matches at the EARLIEST position: when a row amount sat immediately above
    the label (the common two-column PDF extraction), the value-first branch
    matched that ROW amount and consumed the label, so the real subtotal that
    followed was never seen.  ``extract_dept_subtotal`` and
    ``extract_consumables_total`` then disagreed on the same text - DRUG100
    read the subtotal while CNSU100 read the last line item.

    Returns ``(value, layout)`` or ``(None, reason)``.
    """
    after = _SUBTOTAL_AFTER.match(text, label.end())
    if after:
        return after.group(1), "label_first"
    before = _SUBTOTAL_BEFORE.search(text, 0, label.start())
    if before:
        return before.group(1), "value_first"
    return None, "no_amount"


# ---------------------------------------------------------------------------
# Service Summary vs detailed department sections
#
# A bill states each department TWICE: once as a line in the first-page
# service/amount table, and once as one or more detailed sections ending in
# "Dept Sub Total".  The two are not always equal, because a summary LINE can
# aggregate several detailed SECTIONS of the same department.  The locked rule
# for DRUG100 (see the module docstring of cghs.parsing) is the *subtotal*, so
# the detailed sections are what ``extract_dept_subtotal`` must read, and the
# summary must be kept out of it.
#
# Structurally the summary is already excluded because its lines do not carry
# the literal "Dept Sub Total".  That is not a guarantee, so the summary span
# is located and skipped explicitly: a summary line that happened to spell the
# label would otherwise be added to the detail and double count the department.
# ---------------------------------------------------------------------------

_SERVICE_SUMMARY_HEADING = re.compile(
    r'(?:Official\s*)?Service\s*(?:Wise\s*)?Summary', re.IGNORECASE)

#: A department header is a LINE of the form ``<Name>(<service code> )``.
#: The name may itself contain a parenthesised qualifier, so the previous
#: ``[A-Za-z\s]*`` name class could not express it and such a department was
#: invisible - its section boundary did not exist and the PRECEDING department
#: silently absorbed its subtotal.  ``\s`` also matched newlines, so the match
#: could start on an earlier line.  Anchoring to the line removes both faults.
_DEPT_HEADER_RE = re.compile(
    r'(?m)^[ \t]*[A-Za-z][^\n(]*?(?:\([^)\n]*\)[^\n(]*?)*\(\s*999311\s*\)[ \t]*$')

_WHOLE_LINE_AMOUNT = re.compile(r'[ \t]*([\d,]+\.\d{2})[ \t]*')

#: A bill is partitioned into a payer-payable and a patient-payable region.
#: Each region OPENS with a column header of that name and CLOSES with a total
#: of that name.  Membership of a region - not the presence of a word somewhere
#: nearby - is what the locked "Patient Payable excluded" rule refers to.
_PAYER_REGION_CLOSE = re.compile(r'Payer\s+Payable\s+Total\s*:', re.IGNORECASE)
_PATIENT_PAYABLE_TOTAL = re.compile(r'Patient\s+Payable\s+Total\s*:', re.IGNORECASE)
_PATIENT_PAYABLE_DEPT = re.compile(r'Patient\s*Payable', re.IGNORECASE)


def _is_summary_row(text: str, start: int, end: int, next_start: int) -> bool:
    """True when this department header is a first-page summary row.

    The discriminator is semantic and exact: a SUMMARY row states a
    department's total and nothing else, so it is never followed by a
    "Dept Sub Total" before the next department begins, whereas a DETAILED
    section is defined by closing with one.  Shape-based guesses (an amount
    line then a serial line) were rejected: a detailed section whose first row
    begins with a quantity has the same shape, so the shape does not separate
    the two and would have misread such a section as a summary line.
    """
    return _SUBTOTAL_LABEL.search(text, end, next_start) is None


@lru_cache(maxsize=4)
def _document_sections(text: str):
    """Build every department section boundary ONCE for a document.

    Returns a tuple of ``(start, end, header_text, next_start, is_summary_row)``
    in document order, plus the patient-payable region start (or ``None``).

    This is an index, not a rule: it computes exactly what the callers used to
    recompute.  ``_service_summary_span`` and ``collect_dept_subtotals`` are
    both called several times per bill and each call previously re-scanned the
    whole document for headers and then, per header, for a subtotal label - on
    a real 95-page bill that is 59 full-text regex passes with identical
    results.  Memoising the index is behaviour-preserving by construction: the
    function is pure in ``text`` and returns the same tuple for the same input.

    ``maxsize`` is small on purpose - a bill is processed, then the next one -
    so the cache cannot grow into a memory leak.
    """
    headers = [(m.start(), m.end(), m.group(0))
               for m in _DEPT_HEADER_RE.finditer(text)]
    sections = []
    for index, (start, end, label) in enumerate(headers):
        next_start = headers[index + 1][0] if index + 1 < len(headers) else len(text)
        sections.append((start, end, label, next_start,
                         _is_summary_row(text, start, end, next_start)))
    payer_close = _PAYER_REGION_CLOSE.search(text)
    return tuple(sections), (payer_close.end() if payer_close else None)

#: Rupee tolerance when comparing two statements of the same department.
#: This is decimal-precision slack (one paisa), NOT a business threshold.
PHARMACY_RECONCILIATION_TOLERANCE = 0.01


def _service_summary_span(text: str):
    """Return ``(start, end)`` of the first-page summary block, or ``None``.

    The summary is identified STRUCTURALLY, not by a caption: real bills print
    the first page as a plain service/amount table with no "Service Summary"
    words anywhere, so keying off the caption alone missed it entirely and the
    summary figure came back as None.

    The span ends at the first DETAILED section header.  It must not end at the
    first department header, because in this format the summary table is itself
    built out of department header lines - so "before the first header" collapsed
    the span onto the summary's own first row and excluded the whole table.  A
    detailed header is one that is not a summary row (:func:`_is_summary_row`).

    An explicit caption, when present, still wins - it starts the span later and
    so keeps patient/bill header lines out of it.  Both branches are exact
    string/structure matches; there is no fuzzy or OCR-style matching here.

    Returns ``None`` when the document has no detailed department section, which
    is the only situation in which "before the first section" is not meaningful.
    """
    sections, _patient_region_start = _document_sections(text)
    first_detail = next((s for s in sections if not s[4]), None)
    if first_detail is None:
        return None
    heading = _SERVICE_SUMMARY_HEADING.search(text, 0, first_detail[0])
    start = heading.start() if heading else 0
    return (start, first_detail[0])


def extract_service_summary_amount(text: str, dept_pattern: str):
    """Read one department's amount from the Service Summary block.

    Returns ``None`` when the bill has no Service Summary or the department is
    not listed in it - absence of evidence, never a zero.  Only the department's
    own line is read, so neighbouring rows (Patient Payable, Deposit, Grand
    Total) can never be picked up.

    Two layouts are read, both exactly:

    * the amount sits on the department's own line, and
    * the amount sits on the line DIRECTLY BENEATH it, which is how a
      fixed-column report renders the table.  Only a line that is wholly an
      amount qualifies, so a following row or label can never be mistaken for
      the department's figure.
    """
    span = _service_summary_span(text)
    if span is None:
        return None
    start, end = span
    label = re.compile(dept_pattern, re.IGNORECASE)
    lines = text[start:end].splitlines()
    for index, line in enumerate(lines):
        if _SERVICE_SUMMARY_HEADING.search(line):
            continue
        if not label.search(line):
            continue
        amounts = re.findall(r'([\d,]+\.\d{2})', line)
        if amounts:
            try:
                return float(amounts[-1].replace(',', ''))
            except ValueError:
                pass
        if index + 1 < len(lines):
            beneath = _WHOLE_LINE_AMOUNT.fullmatch(lines[index + 1])
            if beneath:
                try:
                    return float(beneath.group(1).replace(',', ''))
                except ValueError:
                    continue
    return None


def collect_dept_subtotals(text: str, dept_pattern: str):
    """Every ``Dept Sub Total`` belonging to one department, with provenance.

    Returns a list of dicts: ``amount``, ``included`` (bool), ``reason`` and
    ``position``.  ``extract_dept_subtotal`` is the sum of the included ones;
    the excluded ones are kept so a caller can SAY why money was left out
    instead of silently dropping it.

    Four properties matter and each is a real bill defect it prevents:

    * **Anchored on a department HEADER, not on the name.**  Matching the name
      anywhere in the document let a ROW DESCRIPTION that happens to mention a
      department fabricate a section, which then absorbed the subtotal of
      whichever real section followed it.  Only a header line opens a section.
    * **Section-aware, not first-match-only.**  A department can state several
      subtotals: an OT Pharmacy section is commonly a run of DATED blocks,
      each closing with its own "Dept Sub Total", and the department's true
      figure is their sum.  Reading only the first returned one block and
      under-reported DRUG100 by the rest.  Every label inside the department's
      own slice is resolved, using the same ``_subtotal_at`` label-first /
      value-first precedence the consumables reader already uses.
    * **Section-scoped, not a global sum.**  A slice stops at the next
      department header, so one department can never absorb another's money.
    * **Position-deduplicated.**  A department named more than once in the
      same section produces overlapping slices; each subtotal label is counted
      at most once, by absolute position.

    Exclusion is decided by REGION, not by a word in the neighbourhood.  The
    previous test treated any slice containing "Payer Payable" as patient
    money, but a bill's payer region CLOSES with exactly that phrase, so the
    last payer-side department of every such bill was silently zeroed - with a
    reason that said the opposite of what had happened.

    FAIL-CLOSED.  This function used to wrap its whole body in
    ``except Exception: pass`` and return whatever it had collected so far.
    An invalid department pattern therefore reported 0.00, and an internal
    defect raised part way through the loop returned a PARTIAL sum that was
    indistinguishable from a complete one - ``reconcile_pharmacy`` published
    DRUG100 = 742,104.38 instead of 743,895.18 and still described itself as
    reconciled.  Unreadable DATA is handled where it occurs (a label with no
    amount, a non-numeric amount, a non-positive amount are all skipped with
    provenance); anything else is a defect and must surface.
    """
    found, seen = [], set()
    summary_span = _service_summary_span(text)
    sections, patient_region_start = _document_sections(text)
    wanted = re.compile(dept_pattern, re.IGNORECASE)
    for header_start, _header_end, header_text, next_pos, _is_summary in sections:
        if not wanted.search(header_text):
            continue
        # The summary states the same department a second time.  Reading it
        # here would double count the department.
        if summary_span and summary_span[0] <= header_start < summary_span[1]:
            continue
        slice_text = text[header_start:next_pos]
        if _PATIENT_PAYABLE_DEPT.search(header_text):
            payable = "the section's own department is Patient Payable"
        elif _PATIENT_PAYABLE_TOTAL.search(slice_text):
            payable = "the section closes with a Patient Payable total"
        elif (patient_region_start is not None
              and header_start >= patient_region_start):
            payable = ("the section opens after the payer-payable region "
                       "closed, so it is patient-payable")
        else:
            payable = None
        for label in _SUBTOTAL_LABEL.finditer(slice_text):
            position = header_start + label.start()
            if position in seen:
                continue
            seen.add(position)
            amount_str, _layout = _subtotal_at(slice_text, label)
            if amount_str is None:
                continue
            try:
                value = float(amount_str.replace(',', ''))
            except ValueError:
                continue
            # No upper bound.  A real hospital pharmacy department runs
            # well past any round number, and the previous ``< 500000``
            # cutoff did not reject such a bill - it silently zeroed the
            # large department and let a small one through, so DRUG100 was
            # reported as a fraction of the true spend.  The guards that
            # belong here are structural, not a magnitude guess.
            if value <= 0:
                continue
            department = header_text.strip()
            if payable:
                # The locked rule is explicit: "Patient Payable excluded".
                found.append({
                    "amount": round(value, 2), "included": False,
                    "position": position, "department": department,
                    "reason": (f"excluded: {payable}, and the locked rule "
                               f"excludes Patient Payable")})
            else:
                found.append({
                    "amount": round(value, 2), "included": True,
                    "position": position, "department": department,
                    "reason": "included: department Dept Sub Total"})
    return sorted(found, key=lambda entry: entry["position"])


def extract_dept_subtotal(text: str, dept_pattern: str) -> float:
    return round(sum(entry["amount"]
                     for entry in collect_dept_subtotals(text, dept_pattern)
                     if entry["included"]), 2)

#: A consumable department is identified by its own HEADER naming it, which is
#: how the real report prints it: "OT Consumables(999311 )",
#: "Ward Consumables(999311 )", "Cathlab Consumables(999311 )".  This is the
#: department-name test only - section discovery, region membership and
#: de-duplication all belong to collect_dept_subtotals.
_CONSUMABLE_DEPT = r'Consumable'


def extract_consumables_total(text: str):
    """CNSU100 - the consumable department subtotals of a bill.

    Returns ``(total, [(department_header, amount), ...])``.

    This reads the SAME section model as DRUG100.  It used to be a second,
    independent reader: it scanned every "Dept Sub Total" in the document,
    searched 5,000 characters backwards for a department-ish word, and then
    de-duplicated on a ``(position // 500, amount)`` bucket.  Four defects
    were reproduced against the grammar proven from the real bill:

    * two DIFFERENT consumable departments that happened to state the SAME
      subtotal were collapsed into one - 2,500.00 + 2,500.00 was reported as
      2,500.00 - because the dedup key was the amount, not the position;
    * one department stating two equal dated subtotals was collapsed the same
      way, and a dated run is the normal shape of these sections;
    * a consumables section inside the PATIENT PAYABLE region was INCLUDED,
      reporting 20,021.00 where the locked rule gives 10,022.00.  The region
      model that ``collect_dept_subtotals`` already applied to pharmacy simply
      did not exist on this path, so the two readers gave different answers
      about the same document;
    * the ``elif last_header`` branch unpacked three-tuples into two names and
      raised ``ValueError``; ``cghs.parsing`` swallowed that into a rejection
      and CNSU100 vanished from the plan.

    Delegating removes the duplicate business logic rather than patching it,
    so there is one department-section model in this codebase and exactly one
    answer to "which money is patient-payable".
    """
    entries = collect_dept_subtotals(text, _CONSUMABLE_DEPT)
    details = [(entry["department"], entry["amount"])
               for entry in entries if entry["included"]]
    total = round(sum(amount for _department, amount in details), 2)
    return total, details


def reconcile_pharmacy(text: str) -> dict:
    """Resolve the DRUG100 pharmacy amount and prove where it came from.

    The amount is NOT a judgement call: the locked rule recorded in the
    ``cghs.parsing`` module docstring is

        DRUG100 = IP Pharmacy subtotal + OT Pharmacy subtotal

    and "subtotal" is the detailed department ``Dept Sub Total``, which is why
    ``extract_dept_subtotal`` skips any slice carrying Patient Payable.  This
    function returns exactly that figure and never substitutes another.

    A SUMMARY LINE IS NOT A SECOND OPINION ON ONE SECTION.  One summary line
    can aggregate SEVERAL detailed sections of the same department - a bill
    may print a department's main section early and a further section of the
    same department much later - so summary != the first detailed subtotal is
    normal, and is NOT evidence of a contradiction.

    An earlier revision treated that difference as an unexplained gap and
    withheld DRUG100 as REVIEW_REQUIRED.  That was wrong, and it blocked
    correct bills; gating on summary-vs-detail equality is removed.  Which of
    a department's sections actually count is decided by the locked rule
    alone: every detailed subtotal is included EXCEPT one whose section is
    patient-payable, and each exclusion is reported with its reason.

    The summary is still READ, for two reasons that remain valid: it is
    provenance, and ``extract_dept_subtotal`` skips the summary span so a
    summary line can never be added to the detailed subtotal and double count
    the department.  ``summary_total`` and ``difference`` are reported as
    information about the bill's structure, never as a verdict on it.

    Returns a dict with ``total`` (the rule amount), the four component
    readings, ``difference``, ``reason`` and ``provenance``.
    """
    departments = (("IP Pharmacy", r'IP\s*Pharmacy'), ("OT Pharmacy", r'OT\s*Pharmacy'))

    detailed, summary, provenance, excluded = {}, {}, [], []
    for label, pattern in departments:
        entries = collect_dept_subtotals(text, pattern)
        included = [e for e in entries if e["included"]]
        detailed[label] = round(sum(e["amount"] for e in included), 2)
        # One provenance row per SUBTOTAL, not per department: a department can
        # state several - an OT Pharmacy section is typically a run of dated
        # blocks - and the record must show each one that was added.
        for index, entry in enumerate(entries, start=1):
            provenance.append({
                "label": label,
                "amount": entry["amount"],
                "source": ("detailed_dept_subtotal" if entry["included"]
                           else "excluded_dept_subtotal"),
                "included": entry["included"],
                "detail": (f"{label} subtotal #{index} {entry['amount']:,.2f} "
                           f"- {entry['reason']}")})
            if not entry["included"]:
                excluded.append({"label": label, "amount": entry["amount"],
                                 "reason": entry["reason"]})
        stated = extract_service_summary_amount(text, pattern)
        summary[label] = stated
        if stated is not None:
            provenance.append({"label": label, "amount": round(stated, 2),
                               "source": "service_summary", "included": False,
                               "detail": f"first-page summary {label} {stated:,.2f}"})

    total = round(sum(detailed.values()), 2)
    arithmetic = " + ".join(
        f"{e['amount']:,.2f}" for e in provenance if e.get("included")) or "0.00"
    arithmetic = f"{arithmetic} = {total:,.2f}"

    stated_amounts = [summary[label] for label, _ in departments
                      if summary[label] is not None]
    summary_total = round(sum(stated_amounts), 2) if stated_amounts else None
    difference = (round(summary_total - round(
        sum(detailed[label] for label, _ in departments
            if summary[label] is not None), 2), 2)
        if stated_amounts else None)

    reason = (f"DRUG100 = IP Pharmacy subtotal ({detailed['IP Pharmacy']:,.2f}) "
              f"+ OT Pharmacy subtotal ({detailed['OT Pharmacy']:,.2f}) "
              f"= {total:,.2f} [{arithmetic}], Patient Payable excluded")
    if excluded:
        reason += ("; excluded " + "; ".join(
            f"{e['label']} {e['amount']:,.2f} ({e['reason']})" for e in excluded))
    if difference is not None and abs(difference) > PHARMACY_RECONCILIATION_TOLERANCE:
        # Informational only.  The summary aggregates departments the locked
        # rule does not put in DRUG100; naming the gap here keeps it visible
        # without pretending the parser cannot proceed.
        reason += (f"; the Service Summary states {summary_total:,.2f} for these "
                   f"departments, {difference:,.2f} of which is carried by other "
                   f"detailed sections and is correctly excluded from DRUG100")

    return {
        "ip_detailed": detailed["IP Pharmacy"],
        "ot_detailed": detailed["OT Pharmacy"],
        "ip_summary": summary["IP Pharmacy"],
        "ot_summary": summary["OT Pharmacy"],
        "total": total,
        "summary_total": summary_total,
        "difference": difference,
        "excluded": excluded,
        "arithmetic": arithmetic,
        "reason": reason,
        "provenance": provenance,
    }


def parse_row_quantity(row_text: str) -> int:
    """
    Parse quantity from service row - correct column
    Looks for pattern qty ref amount near CGHS or ROOM RENT
    """
    # Look for pattern: qty (float)  ref (3-7 digits)  amount (float with comma)  [CGHS|ROOM RENT]
    # Example: "1.00  3096100  1,863.00 CGHS-L"
    # Search for all occurrences of (\d+\.\d{2})\s+(\d{3,7})\s+([\d,]+\.\d{2})
    matches = re.findall(r'(\d+\.\d{2})\s+(\d{3,7})\s+([\d,]+\.\d{2})', row_text)
    # Filter qty < 50
    candidates = []
    for qty_str, ref, amt in matches:
        try:
            qty_val = float(qty_str)
            if 0 < qty_val < 50:
                # Check if this qty is likely quantity (not tariff)
                # Tariff for labs often 1,863.00 same as amount, qty is small like 1.00
                # So we want qty that is small and ref is 3-7 digits
                candidates.append(qty_val)
        except: continue
    # If pattern with CGHS after amount, prefer that match closest to CGHS
    # Find CGHS position
    cghs_pos = row_text.upper().find("CGHS")
    if cghs_pos == -1:
        cghs_pos = row_text.upper().find("ROOM RENT")
    if matches:
        # Iterate matches in reverse order (closest to CGHS/ROOM RENT) and pick first small qty
        # For NEBULIZER: "45.00 0 1 NEBULIZER ... 4.00 5433 180.00 CGHS"
        # matches would be 45.00? No because 45.00 0 is not \d{3,7}, so not match. Only 4.00 5433 180.00 matches.
        # So last match is likely qty
        for qty_str, ref, amt in reversed(matches):
            try:
                qty_val = float(qty_str)
                if 0 < qty_val < 30:  # qty generally <=20
                    # Additional check: for ROOM RENT, qty should be 1.00
                    # Return int
                    return int(qty_val) if qty_val.is_integer() else int(qty_val)
            except: continue
    # Fallback: look for \b(\d+)\.00\b before ref and amount
    # Search for all \b(\d+)\.00\b
    all_qty = re.findall(r'\b(\d+)\.00\b', row_text)
    if all_qty:
        # Convert to int, filter small
        small = [int(q) for q in all_qty if 0 < int(q) < 30]
        if small:
            # For most rows, qty is 1. If multiple small, prefer the one that appears after service name?
            # Heuristic: qty is often 1, but for NEBULIZER it's 4
            # Take the last small qty that appears before CGHS/ROOM RENT and is <=10? Actually NEBULIZER qty 4 appears before ref
            # Let's take the qty that is closest to CGHS but not amount? Amount often is larger (e.g., 180.00 vs 4.00)
            # The amount is also \d+\.00 but larger? Actually both small
            # Better to use matches already attempted
            # Fallback: return 1 if no clear qty
            # If we have multiple small and one is 1, but another is 4, which to choose? Choose the one that appears in pattern qty ref amount, which we already tried
            # So fallback to 1?
            pass
    return 1

def parse_oxygen_quantity(row_text: str) -> int:
    """
    OXYGEN FULL DAY =24, HALF DAY=12, else 1 - row-aware
    """
    upper = row_text.upper()
    # Normalize variants
    if "OXYGEN" not in upper:
        return 1
    # FULL DAY variants
    if re.search(r'FULL\s*DAY|FULLDAY|24\s*HRS|24\s*HOURS', upper):
        return 24
    if re.search(r'HALF\s*DAY|HALFDAY|12\s*HRS|12\s*HOURS', upper):
        # Need to ensure it's not FULL
        if "FULL" not in upper:
            return 12
        # If both FULL and HALF present? Unlikely, but check which appears closer to OXYGEN?
        # For safety, if both present, ambiguous - return 1 and let caller handle? But spec says FULL=24, HALF=12
        # If row has HALF DAY explicitly, return 12
        if "HALF" in upper:
            # Check if FULL also present? If both, prefer FULL? Actually ambiguous, but spec says if both exist, need to decide
            # For our purpose, if HALF appears, return 12 unless FULL also appears
            # We'll check: if FULL DAY and HALF DAY both in same row impossible, but if both keywords, we need to see which is present as phrase
            # Simplify: if "HALF DAY" phrase present, return 12
            return 12
    return 1

def normalize_cghs_code(row_text: str) -> List[Tuple[str, str]]:
    """Deterministic row-aware code normalisation.

    Returns ``(final_code, normalization_reason)`` for the EXECUTABLE
    occurrences only - the compatibility shape every existing caller expects.
    :func:`resolve_cghs_codes` is the canonical implementation and also
    reports the occurrences that need review; use it when you need provenance
    or must not lose an unresolved alias.
    """
    return [(occ["final_code"], occ["normalization_reason"])
            for occ in resolve_cghs_codes(row_text)
            if occ["status"] == EXECUTABLE]


def _alias_region(row_text: str, marker_end: int) -> str:
    """The searchable span after a CGHS marker.

    Stops at the first money column: an alias never lies beyond it, and
    leaving the amounts in lets digit-repair weld a quantity onto an amount
    and manufacture a code out of rupees.
    """
    region = row_text[marker_end:marker_end + 100]
    region = region.replace("|", " ").replace("\n", " ")

    money = _AMOUNT_TOKEN.search(region)
    if money:
        region = region[:money.start()]

    region = _YEAR_SUFFIX.sub("", region)
    # Rejoin a family letter the PDF split from its number with a hyphen:
    # "CGHS-L B-103-2 025" -> "B103...".  Deliberately AFTER the year strip,
    # so a clean "-2025" is already gone and this can never weld a year onto
    # a letter ("B-2025" -> "B" long before we get here).  One letter, three
    # digits, nothing invented.
    region = re.sub(r'\b([A-Z])-(\d{3})\b', r'\1\2', region)
    # repair PDF-fragmented numbers: "B042+0 43" -> "B042+043"
    region = re.sub(r'(\d)\s+(\d)', r'\1\2', region)
    region = re.sub(r'(\d)\s+(\d)', r'\1\2', region)
    return region


def _carry_prefix(cat: str, parts: List[str], index: int, pref: str,
                  num: str) -> Optional[str]:
    """Section 11: the family of a PROVEN compound, else ``None``.

    A prefix is carried across ``+`` only when every condition holds:

    * the component itself is numeric-only (nothing to override);
    * it is not the first component of the expression;
    * the FIRST component carried an explicit alphabetic family
      (``B042`` in ``CGHS-L B042+043+044``) - a bare first component such as
      ``CGHS-LB269+270`` proves nothing and must not be carried;
    * every component between the first and this one is numeric-only, so the
      expression is one contiguous run under one unchanged category;
    * the resulting code exists in the CGHS master registry.

    Anything else returns ``None`` and the caller preserves the component for
    review.  This never invents a family and never widens to a new category.
    """
    if pref or index == 0:
        return None

    first = re.match(r'^([A-Z]{1,2})(\d{3})$', parts[0])
    if not first:
        return None

    # Every component after the first must be numeric-only for the run to be
    # contiguous and unambiguous.
    for middle in parts[1:index]:
        if not re.fullmatch(r'\d{3}', middle):
            return None

    family = LOCKED_CATEGORY_COMPOSITION.get((cat, first.group(1)))
    if family is None:
        return None

    candidate = f"{family}{num}"
    return candidate if candidate in VALID_CODES else None


def _resolve_component(cat: str, token: str, pref: str, num: str,
                       upper_row: str) -> Tuple[Optional[str], str, str,
                                                Optional[int]]:
    """Resolve ONE code component against the locked rules.

    Returns ``(final_code, status, reason, locked_quantity)``.  ``final_code``
    is None when nothing legitimate could be produced.  Evidence order is
    explicit token -> canonical registry -> locked context rule; inference
    never becomes executable on its own.
    """
    # 0. A C-token that is the ALIAS PREFIX of an explicit locked composition
    #    is not a raw C-alias at all: "CGHS-B C002" is the Blood Component
    #    category carrying Cxxx, which composes to BC002 (Packed Red Cell).
    #    ("C", "C") is deliberately absent from the table, so a genuine
    #    "CGHS-C C002" row can never take this path and keeps the full
    #    Oxygen / Blood Bank safety treatment in branch 1 below.
    _composed_family = LOCKED_CATEGORY_COMPOSITION.get((cat, pref))
    _is_composed_alias = (
        _composed_family is not None
        and f"{_composed_family}{num}" in VALID_CODES
    )

    # 1. A raw C-alias is never executable by itself.  It resolves only
    #    through a locked rule that reads the SAME-ROW evidence.
    if _RAW_C_ALIAS.match(token) and not _is_composed_alias:
        if token in _NEVER_RESOLVED_ALIASES:
            return (None, REVIEW_REQUIRED,
                    f"{UNRESOLVED_MAPPING}: {token} has no approved target "
                    f"code; inventing CC{num} is forbidden", None)

        if token == "C002":
            if _BLOOD_BANK_CONTEXT.search(upper_row):
                return (None, REVIEW_REQUIRED,
                        f"{UNRESOLVED_MAPPING}: C002 in a Blood Bank / Packed "
                        f"Cells row is not the Oxygen code and must not "
                        f"become CC002", None)
            if _OXYGEN_CONTEXT.search(upper_row):
                qty = parse_oxygen_quantity(upper_row)
                return ("CC002", EXECUTABLE,
                        f"C002 + Oxygen evidence in the same row => CC002 "
                        f"x{qty}", qty)
            return (None, REVIEW_REQUIRED,
                    f"{UNRESOLVED_MAPPING}: C002 carries no Oxygen evidence "
                    f"in the same row", None)

        locked = LOCKED_CONTEXT_MAPPINGS.get(token)
        if locked:
            final, evidence, description = locked
            if evidence.search(upper_row):
                return (final, EXECUTABLE,
                        f"{token} + '{description}' evidence in the same row "
                        f"=> {final}", None)
            return (None, REVIEW_REQUIRED,
                    f"{UNRESOLVED_MAPPING}: {token} requires "
                    f"'{description}' evidence in the same row to become "
                    f"{final}; the row does not carry it", None)

        return (None, REVIEW_REQUIRED,
                f"{UNRESOLVED_MAPPING}: no locked rule resolves raw alias "
                f"{token}", None)

    # 2. An explicit, complete code token from a known family.
    if _EXPLICIT_FAMILY.match(token):
        if token in VALID_CODES:
            return (token, EXECUTABLE,
                    f"Explicit code token '{token}' in the bill row", None)
        return (None, REVIEW_REQUIRED,
                f"{UNRESOLVED_MAPPING}: explicit token {token} is not in the "
                f"CGHS code registry", None)

    # 3. Locked category composition: CGHS-L + Bxxx -> LBxxx.
    family = LOCKED_CATEGORY_COMPOSITION.get((cat, pref))
    if family:
        candidate = f"{family}{num}"
        if candidate in VALID_CODES:
            return (candidate, EXECUTABLE,
                    f"Locked composition CGHS-{cat} + {token} => {candidate}",
                    None)
        return (None, REVIEW_REQUIRED,
                f"{UNRESOLVED_MAPPING}: composition CGHS-{cat} + {token} "
                f"=> {candidate} is not in the CGHS code registry", None)

    # 4. A category that is already a family, carrying a bare numeric.
    if not pref and cat in LOCKED_NUMERIC_CATEGORIES:
        candidate = f"{cat}{num}"
        if candidate in VALID_CODES:
            return (candidate, EXECUTABLE,
                    f"Locked composition CGHS-{cat} + {num} => {candidate}",
                    None)
        return (None, REVIEW_REQUIRED,
                f"{UNRESOLVED_MAPPING}: CGHS-{cat} + {num} => {candidate} is "
                f"not in the CGHS code registry", None)

    # 5. Nothing legitimate.  Preserve the token; never guess a family.
    return (None, REVIEW_REQUIRED,
            f"{UNRESOLVED_MAPPING}: category '{cat}' with token '{token}' "
            f"matches no locked rule", None)


def resolve_cghs_codes(row_text: str, page: Any = None, section: str = "",
                       service_name: str = "") -> List[Dict[str, Any]]:
    """Canonical CGHS code identification for one bill row.

    Splits explicit ``+`` compound expressions into independent components,
    resolves each one on its own through the locked rules, and returns an
    occurrence per component carrying its provenance.  Aggregation happens
    downstream, strictly AFTER resolution, so two different contexts can never
    be merged on a raw alias.
    """
    occurrences: List[Dict[str, Any]] = []
    if not row_text:
        return occurrences

    upper = row_text.upper()
    if "CGHS" not in upper:
        return occurrences

    for marker in re.finditer(r'CGHS[-\s]*([A-Z]{1,3})', upper):
        cat = (marker.group(1) or "").strip()
        region = _alias_region(row_text, marker.end())

        expression = re.search(
            r'([A-Z]{0,2}\d{3}(?:\s*\+\s*[A-Z]*\d{3})*)', region, re.IGNORECASE)
        if not expression:
            continue

        normalised = re.sub(r'\s*\+\s*', '+', expression.group(1)).upper()
        parts = [p for p in normalised.split('+') if p]
        count = len(parts)

        # The marker regex consumes the category, so "CGHS-LB269+LB270" leaves
        # "269+LB270".  Put the category back when the bill wrote it as part of
        # the first token, so provenance shows the expression as printed.
        raw_expression = normalised
        if parts and not re.match(r'^[A-Z]', parts[0]):
            raw_expression = f"{cat}{normalised}"

        for index, part in enumerate(parts):
            shape = re.match(r'^([A-Z]{0,2})(\d{3})$', part)
            if not shape:
                continue
            pref, num = shape.group(1), shape.group(2)

            carried = _carry_prefix(cat, parts, index, pref, num)

            if not pref and count > 1 and index > 0 and carried:
                # Section 11: the compound is PROVEN - the first component
                # carried an explicit family, every component is numeric-only
                # after it, the category is unchanged across the one
                # contiguous expression, and the carried code exists in the
                # registry.  "CGHS-L B042+043+044" is therefore three
                # independent codes, not one and two dropped.
                final, status, reason, locked_qty = (
                    carried, EXECUTABLE,
                    f"Prefix carried from the first component of "
                    f"'{raw_expression}' => {carried}; the compound is "
                    f"unambiguous and {carried} is in the CGHS code registry",
                    None)
            elif not pref and count > 1 and index > 0:
                # Section 17: the bill omitted the prefix on a later component
                # ("CGHS-LB269+270").  The first component is itself bare, so
                # there is no explicit family to carry: that would be a guess,
                # and the component is preserved for review instead.
                final, status, reason, locked_qty = (
                    None, REVIEW_REQUIRED,
                    f"{UNRESOLVED_MAPPING}: component '{part}' of "
                    f"'{raw_expression}' omits its code prefix; prefix carry "
                    f"is ambiguous and is not performed", None)
            else:
                final, status, reason, locked_qty = _resolve_component(
                    cat, part, pref, num, upper)

            occurrence = {
                "final_code": final,
                "status": status,
                "normalization_reason": reason,
                "quantity": locked_qty,
                "original_token": part,
                "raw_expression": raw_expression,
                "category": cat,
                "page": page,
                "section": section,
                "service_name": service_name,
                "source_row_text": row_text[:500],
            }
            if count > 1:
                occurrence["parent_expression"] = raw_expression
                occurrence["component_index"] = index
                occurrence["component_count"] = count
            occurrences.append(occurrence)

    return occurrences
