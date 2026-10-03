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
from typing import List, Tuple

from .locators import VALID_CODES, CGHS_CATEGORY_MAP, PORTAL_OPTION_MAP  # noqa: F401

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
    for m in re.finditer(r'Dept\s*Sub\s*Total\s*:?\s*([\d,]+\.\d{2})|([\d,]+\.\d{2})\s*Dept\s*Sub\s*Total', text, re.IGNORECASE|re.DOTALL):
        amt_str = m.group(1) or m.group(2)
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
    """
    Deterministic row-aware code normalization
    Returns list of (final_code, normalization_reason)
    """
    results = []
    upper = row_text.upper()
    if "CGHS" not in upper:
        return results

    # Find CGHS marker positions
    # Pattern: CGHS-?([A-Z]{1,3})? - capture category
    # Example: CGHS-L, CGHS-C, CGHS-RI, CGHS-CI, CGHS-P, CGHS-G, CGHS- (with no letter?)
    # Use regex to find CGHS- followed by optional letters, then optional whitespace, then alias part
    # We want to extract category and alias region

    # Find all CGHS occurrences in row (should be one per row)
    for cghs_match in re.finditer(r'CGHS[-\s]*([A-Z]{1,3})', upper):
        cat_raw = cghs_match.group(1) or ""
        # cat_raw may include extra? e.g., "L", "C", "RI", "CI", "P", "G"
        # But for cases like "CGHS-RI 001", cat_raw = "RI"
        # For "CGHS-P T005", cat_raw = "P"
        # For "CGHS-C N002", cat_raw = "C"
        # For "CGHS-L B243", cat_raw = "L"
        cat = cat_raw.strip()

        # Alias region: substring after CGHS match, up to maybe 60 chars, until next CGHS or end
        start = cghs_match.end()
        alias_sub = row_text[cghs_match.start(): cghs_match.start()+100]  # use original case but upper for parsing
        # Actually take from match end
        alias_region = row_text[start: start+100]

        # Clean alias region: replace newlines, pipes, multiple spaces
        alias_region = alias_region.replace("|"," ").replace("\n"," ")
        # Fix fragmented numbers: (\d)\s+(\d) -> \1\2 twice
        alias_region = re.sub(r'(\d)\s+(\d)', r'\1\2', alias_region)
        alias_region = re.sub(r'(\d)\s+(\d)', r'\1\2', alias_region)
        # Remove year suffixes
        alias_region = re.sub(r'-\s*20\s*25', '', alias_region, flags=re.IGNORECASE)
        alias_region = re.sub(r'-\s*2025', '', alias_region, flags=re.IGNORECASE)
        alias_region = re.sub(r'-\s*20\s*2[0-9]', '', alias_region, flags=re.IGNORECASE)
        alias_region = re.sub(r'-\s*20\b', '', alias_region, flags=re.IGNORECASE)
        alias_region = re.sub(r'-\s*202\b', '', alias_region, flags=re.IGNORECASE)

        # Now search for alias pattern: first occurrence of [A-Z]{0,2}\d{3} optionally with + parts
        # Example: B243, N002, 001, T005, P009, C002, C012, B042+043+044, B042+0 43+044 already cleaned to B042+043+044
        # Use regex to find code-like token: ([A-Z]{0,2}\d{3}(?:\s*\+\s*[A-Z]*\d{3})*)
        # But we want to capture merged
        alias_match = re.search(r'([A-Z]{0,2}\d{3}(?:\s*\+\s*[A-Z]*\d{3})*)', alias_region, re.IGNORECASE)
        if not alias_match:
            # Try finding just 3-digit number
            alias_match = re.search(r'(\d{3})', alias_region)
            if not alias_match:
                continue
            alias_str = alias_match.group(1)
        else:
            alias_str = alias_match.group(1)

        # Clean alias_str: remove spaces around +
        alias_str = re.sub(r'\s*\+\s*', '+', alias_str)
        # Replace fragmented: already done
        # Split merged by +
        parts = [p.strip() for p in alias_str.split('+') if p.strip()]

        # Track current prefix for bare numbers
        current_prefix = None  # letter prefix for number
        expanded_codes = []

        for part in parts:
            # part like B042, 043, N002, 001, T005, P009, C002
            m = re.match(r'^([A-Z]{0,2})(\d{3})$', part, re.IGNORECASE)
            if m:
                pref = m.group(1).upper()  # may be empty, or B, N, T, P, C, etc
                num = m.group(2)
                if pref:
                    current_prefix = pref
                    full_token = pref + num  # e.g., B042, N002, T005
                else:
                    # No prefix, use current_prefix if exists
                    if current_prefix:
                        full_token = current_prefix + num  # e.g., 043 with current B => B043
                        pref = current_prefix
                    else:
                        full_token = num  # e.g., 001 with no prefix
                        pref = ""
                # Now generate candidate final codes
                # Candidate logic:
                candidates = []
                # cat + full_token (e.g., L + B042 = LB042, C + N002 = CN002, P + T005 = PT005, G + P009 = GP009, C + C002 = CC002, RI + 001 = RI001)
                # For cat like RI, full_token may be 001, so cat+full_token = RI001
                # For cat L, full_token B042 => LB042
                # For cat C, full_token C002 => CC002
                # Also candidate = full_token itself if valid (e.g., C012 itself valid)
                # Also candidate = cat + num (e.g., C + 002 = C002, but we want CC002 for oxygen, but generate both and validate)
                cat_upper = cat.upper()

                # Candidate 1: cat + full_token (if full_token has prefix, this will double)
                if full_token:
                    cand1 = cat_upper + full_token
                    candidates.append(cand1)
                # Candidate 2: if full_token itself is valid and length >=3, include
                candidates.append(full_token.upper())
                # Candidate 3: cat + num
                cand3 = cat_upper + num
                candidates.append(cand3)
                # Candidate 4: if pref exists, cat + pref + num already covered by cand1, but also pref+num is full_token
                # For cases where cat is multi-letter like RI, and full_token is B042? Unlikely, but include RI + B042? That would be RIB042 invalid
                # So we filter candidates to those that look like valid CGHS code: [A-Z]{1,3}\d{3}

                # Normalize candidates: remove any that are not matching pattern [A-Z]{1,4}\d{3} or DRUG100 etc
                # And check against VALID_CODES
                chosen = None
                reason = ""
                for cand in candidates:
                    cand_up = cand.upper()
                    # Clean cand: remove any non-alphanumeric? Keep letters and digits
                    cand_up = re.sub(r'[^A-Z0-9]', '', cand_up)
                    # Must match pattern: 1-3 letters + 3 digits, or special
                    if re.match(r'^[A-Z]{1,3}\d{3}$', cand_up) or cand_up in ["DRUG100","CNSU100","CN002","WC001","CC001","CC002"]:
                        if cand_up in VALID_CODES:
                            chosen = cand_up
                            reason = f"Row evidence '{part}' with category '{cat}' => {cand_up} (candidate from {candidates})"
                            break
                if chosen:
                    expanded_codes.append((chosen, reason))
                else:
                    # If no candidate valid, try alternative: maybe cat itself is part of final code? e.g., cat RI, num 133 => RI133 valid, we already tried cat+num which is RI133, which should be valid
                    # If still not valid, reject
                    pass
            else:
                # part doesn't match, maybe it's like "B042+0" already split? Actually we split, so should match
                continue

        # Add expanded codes to results
        for code, reason in expanded_codes:
            # Validate code is not hallucinated from duration like 101hr - our alias extraction limited to 100 chars after CGHS and first code token, so should not include 101hr
            # Additionally, reject if code is CC010 and row contains OXYGEN? Actually CC010 could be valid but need evidence: our alias extraction for OXYGEN row gave C002, not 101, so CC010 would not appear
            results.append((code, reason))

    return results
