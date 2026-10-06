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


def extract_dept_subtotal(text: str, dept_pattern: str) -> float:
    total = 0.0
    try:
        dept_headers = list(re.finditer(r'[A-Za-z][A-Za-z\s]*\(\s*999311\s*\)', text, re.IGNORECASE))
        for m in re.finditer(dept_pattern, text, re.IGNORECASE):
            next_pos = len(text)
            for h in dept_headers:
                if h.start() > m.start():
                    next_pos = min(next_pos, h.start())
            slice_text = text[m.start(): next_pos]
            if re.search(r'Patient\s*Payable|Grand\s*Total|Payer\s*Payable', slice_text, re.IGNORECASE):
                continue
            val = None
            for regex in [r'Dept\s*Sub\s*Total\s*:?\s*([\d,]+\.\d{2})', r'([\d,]+\.\d{2})\s*Dept\s*Sub\s*Total']:
                mm = re.search(regex, slice_text, re.IGNORECASE|re.DOTALL)
                if mm:
                    try:
                        val = float(mm.group(1).replace(',',''))
                        break
                    except: continue
            if val is not None and 0 < val < 500000:
                total += val
    except Exception:
        pass
    return total

def extract_consumables_total(text: str):
    total = 0.0
    details = []
    for m in _SUBTOTAL_LABEL.finditer(text):
        amt_str, _layout = _subtotal_at(text, m)
        if amt_str is None:
            continue
        try:
            val = float(amt_str.replace(',',''))
        except: continue
        if not (0 < val < 500000):
            continue
        start = max(0, m.start()-5000)
        snippet = text[start:m.start()]
        last_header = None
        last_pos = -1
        for pat in [r'Cathlab\s*Consumables', r'OT\s*Consumables', r'Ward\s*Consumable', r'\bConsumables\b']:
            for mm in re.finditer(pat, snippet, re.IGNORECASE):
                if mm.start() > last_pos:
                    last_pos = mm.start()
                    last_header = pat
        dept_headers = list(re.finditer(r'[A-Za-z][A-Za-z\s]*\(\s*999311\s*\)', snippet, re.IGNORECASE))
        if dept_headers:
            last_dept = max(dept_headers, key=lambda x: x.start())
            last_dept_text = snippet[last_dept.start():last_dept.start()+80]
            if re.search(r'Consumable', last_dept_text, re.IGNORECASE):
                if not any(abs(m.start()-pos) < 10 for pos,_,_ in details):
                    details.append((m.start(), val, last_dept_text.strip()[:40]))
                    total += val
        elif last_header:
            if not any(abs(m.start()-pos) < 200 for pos,_ in details):
                details.append((m.start(), val, last_header))
                total += val
    uniq = []
    seen = set()
    for pos,val,pat in sorted(details):
        key = (int(pos/500), round(val,2))
        if key not in seen:
            seen.add(key)
            uniq.append((pat,val))
    total = sum(v for _,v in uniq)
    return total, uniq

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
