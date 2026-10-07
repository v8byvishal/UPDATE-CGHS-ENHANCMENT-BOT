"""CGHS bill parsing engine (PDF -> EnhancementPlan items).

Canonical owner: this module.  Moved out of the legacy monolithic ``app.py`` at
baseline commit c3ccdf32e2170891fad9b150c850053461c85a25.

The ONLY change made during the extraction is a seam: the geometry/cluster code
now consumes ``pages_blocks`` (a list of PyMuPDF ``page.get_text("blocks")``
results) instead of re-opening the PDF three times.  That removes two redundant
full PDF parses per bill AND makes the locked business rules testable without
PyMuPDF.  Rule behaviour is byte-for-byte the behaviour of the baseline.

LOCKED RULES PRESERVED EXACTLY:
  * CC001  = count of ROOM RENT(ICU) rows            (Room Rent section only)
  * WC001  = count of qualifying ward Room Rent rows (Room Rent section only)
  * CN002  = (icu_rows * 3) + (ward_rows * 2)        (locked Room Rent calculation)
  * raw CN002 / CC001 / WC001 found on consultation rows are REJECTED, never
    allowed to override the locked derived result
  * CC002  = OXYGEN rows only (FULL DAY = 24, HALF DAY = 12, else 1)
  * DRUG100 = IP Pharmacy subtotal + OT Pharmacy subtotal (Patient Payable excluded)
  * CNSU100 = consumable department subtotals
"""

import os
import re
from typing import Any, Dict, List, Tuple  # noqa: F401

from .locators import VALID_CODES
from .rules import (
    EXECUTABLE,
    REVIEW_REQUIRED,
    extract_consumables_total,
    extract_dept_subtotal,
    reconcile_pharmacy,
    normalize_cghs_code,
    parse_oxygen_quantity,
    parse_row_quantity,
    resolve_cghs_codes,
)


def _open_pdf(pdf_path):
    """Lazy PyMuPDF import so that importing the rules does not require PyMuPDF."""
    import fitz  # noqa: WPS433 - deliberately lazy
    return fitz.open(pdf_path)


def _cluster_blocks_into_rows(blocks):
    """Cluster PyMuPDF text blocks into visual rows by y-proximity.

    Extracted verbatim from the two identical copies that existed in the
    baseline ``extract_room_rent_section`` / ``extract_service_rows``.
    """
    sorted_blocks = sorted(blocks, key=lambda b: (b[1], b[0]))
    clusters = []
    cur_cluster = []
    cur_y = None
    for b in sorted_blocks:
        x0, y0, x1, y1, txt = b[0], b[1], b[2], b[3], b[4]
        if not txt.strip():
            continue
        if cur_y is None or abs(y0 - cur_y) < 12:
            cur_cluster.append(b)
            if cur_y is None:
                cur_y = y0
        else:
            clusters.append(cur_cluster)
            cur_cluster = [b]
            cur_y = y0
    if cur_cluster:
        clusters.append(cur_cluster)
    return clusters


def extract_room_rent_section(pdf_path: str):
    """Disk wrapper - kept for API compatibility with the baseline."""
    doc = _open_pdf(pdf_path)
    return extract_room_rent_section_from_pages([p.get_text("blocks") for p in doc])


def extract_service_rows(pdf_path: str):
    """Disk wrapper - kept for API compatibility with the baseline."""
    doc = _open_pdf(pdf_path)
    return extract_service_rows_from_pages([p.get_text("blocks") for p in doc])


def extract_room_rent_section_from_pages(pages_blocks):
    """
    Extract Room Rent rows from PDF - only source for CC001/WC001
    Returns (icu_rows, ward_rows, details)
    """
    room_rent_rows = []  # list of dict
    current_section = None

    # Department header pattern
    dept_pattern = re.compile(r'([A-Za-z ]+)\(\s*999311\s*\)', re.IGNORECASE)

    for page_idx, blocks in enumerate(pages_blocks):
        clusters = _cluster_blocks_into_rows(blocks)

        for cluster in clusters:
            # Combine cluster text sorted by x
            combined = " ".join([c[4].replace("\n"," ").strip() for c in sorted(cluster, key=lambda x: x[0])])
            combined = combined.strip()
            if not combined:
                continue
            # Check if this cluster is a department header
            dept_match = dept_pattern.search(combined)
            if dept_match and len(combined) < 80:  # header short
                dept_name = dept_match.group(1).strip().upper()
                current_section = dept_name
                #print(f"Dept header: {dept_name} at page {page_idx}")
                continue

            # If current section is ROOM RENT and combined contains ROOM RENT(
            if current_section and "ROOM RENT" in current_section:
                if "ROOM RENT(" in combined.upper():
                    # This is a Room Rent service row
                    upper = combined.upper()
                    row_type = None
                    if "ICU" in upper:
                        row_type = "ICU"
                    elif "AC MULTIBEDS" in upper or "AC MULTIBED" in upper or "MULTIBEDS" in upper:
                        row_type = "WARD_AC"
                    elif "SINGLE" in upper:
                        row_type = "WARD_SINGLE"
                    else:
                        # Other ward types? Treat as ward if contains ROOM RENT but not ICU?
                        # For safety, treat as ward if not ICU
                        if "ROOM RENT(" in upper:
                            row_type = "WARD_OTHER"
                    if row_type:
                        qty = parse_row_quantity(combined)
                        room_rent_rows.append({
                            "page": page_idx,
                            "row_text": combined,
                            "type": row_type,
                            "qty": qty,
                            "section": current_section,
                            "source_row_text": combined
                        })
            # Also need to detect Room Rent header that may not be via dept_pattern but via block containing "Room Rent(999311)"
            # If combined contains "Room Rent(999311)" as header, set current_section
            if "ROOM RENT(999311" in combined.upper() or "ROOM RENT (999311" in combined.upper():
                current_section = "ROOM RENT"
                continue

    # Calculate counts
    icu_count = sum(1 for r in room_rent_rows if r["type"] == "ICU")
    ward_ac = sum(1 for r in room_rent_rows if r["type"] == "WARD_AC")
    ward_single = sum(1 for r in room_rent_rows if r["type"] == "WARD_SINGLE")
    ward_other = sum(1 for r in room_rent_rows if r["type"] == "WARD_OTHER")
    ward_count = ward_ac + ward_single + ward_other

    # For CN002 = ICU*3 + Ward*2
    cn002 = icu_count*3 + ward_count*2

    details = {
        "icu_rows": [r for r in room_rent_rows if r["type"]=="ICU"],
        "ward_rows": [r for r in room_rent_rows if r["type"]!="ICU"],
        "all_rows": room_rent_rows,
        "icu_count": icu_count,
        "ward_ac": ward_ac,
        "ward_single": ward_single,
        "ward_other": ward_other,
        "ward_count": ward_count,
        "cn002": cn002
    }
    return details

def extract_service_rows_from_pages(pages_blocks):
    """
    Row-aware extraction of CGHS service rows
    Returns list of dict with page, row_text, section, service_name, etc.
    """
    service_rows = []
    current_section = None
    dept_pattern = re.compile(r'([A-Za-z ]+)\(\s*999311\s*\)', re.IGNORECASE)

    for page_idx, blocks in enumerate(pages_blocks):
        clusters = _cluster_blocks_into_rows(blocks)

        for cluster in clusters:
            combined = " ".join([c[4].replace("\n"," ").strip() for c in sorted(cluster, key=lambda x: x[0])])
            combined = combined.strip()
            if not combined:
                continue
            # Check dept header
            dept_match = dept_pattern.search(combined)
            if dept_match and len(combined) < 80:
                dept_name = dept_match.group(1).strip().upper()
                current_section = dept_name
                continue
            if "ROOM RENT(999311" in combined.upper():
                current_section = "ROOM RENT"
                continue

            # If row contains CGHS and not Room Rent service row (Room Rent rows don't have CGHS)
            if "CGHS" in combined.upper():
                # Exclude Room Rent section CGHS? Room Rent rows shouldn't have CGHS, but just in case
                if current_section == "ROOM RENT":
                    # Room Rent should not have CGHS, but skip if it does
                    continue
                # This is a CGHS service row
                service_rows.append({
                    "page": page_idx,
                    "row_text": combined,
                    "section": current_section,
                    "service_name": combined.split("CGHS")[0][:200],  # text before CGHS as service name approx
                    "source_row_text": combined
                })

    return service_rows

class CGHSParsingEngine:
    def __init__(self, logger=None):
        self.logger = logger
        self.valid_codes = VALID_CODES
        self.portal_option_map = {"DRUG100": "drugs(DRGU100-None)", "CNSU100": "consumables(CNSU100-None)"}

    def parse(self, pdf_path: str):
        """Parse a PDF from disk.  Thin I/O wrapper around :meth:`parse_document`."""
        doc = _open_pdf(pdf_path)
        pages_blocks = [page.get_text("blocks") for page in doc]
        full_text = ""
        for page in doc:
            full_text += page.get_text("text") + "\n"
        return self.parse_document(pages_blocks, full_text, pdf_path)

    def parse_document(self, pages_blocks, full_text, pdf_path="<document>"):
        """Pure parse over already-extracted page blocks + full text.

        This is the canonical implementation.  Keeping it free of file I/O is what
        makes the locked CGHS rules (CC001 / WC001 / CN002 / DRUG100 / CNSU100 /
        CC002) testable with deterministic fixtures.
        """

        # Room Rent section - only source for CC001/WC001/CN002
        room_details = extract_room_rent_section_from_pages(pages_blocks)
        icu_count = room_details["icu_count"]
        ward_count = room_details["ward_count"]
        cn002_qty = room_details["cn002"]

        raw_occurrences = []
        rejected = []
        aggregation_log = []

        # Process Room Rent derived codes
        # CC001
        if icu_count > 0:
            raw_occurrences.append({
                "code": "CC001",
                "qty": icu_count,
                "page": -1,
                "source_block": f"ROOM RENT ICU rows={icu_count}",
                "category": "ROOM_RENT_ICU",
                "source_page": -1,
                "source_section": "ROOM RENT(999311)",
                "source_service_name": "ROOM RENT(ICU)",
                "source_row_text": f"{icu_count} ICU rows",
                "normalization_reason": f"Count of ROOM RENT(ICU) rows = {icu_count}",
                "provenance": room_details["icu_rows"]
            })
            aggregation_log.append(f"[ROOM_RENT] CC001 = {icu_count} ICU rows")

        # WC001
        if ward_count > 0:
            raw_occurrences.append({
                "code": "WC001",
                "qty": ward_count,
                "page": -1,
                "source_block": f"ROOM RENT WARD rows={ward_count} (AC={room_details['ward_ac']} Single={room_details['ward_single']} Other={room_details['ward_other']})",
                "category": "ROOM_RENT_WARD",
                "source_page": -1,
                "source_section": "ROOM RENT(999311)",
                "source_service_name": "ROOM RENT(AC Multibeds/Single)",
                "source_row_text": f"{ward_count} Ward rows",
                "normalization_reason": f"Count of ROOM RENT(AC Multibeds)+ROOM RENT(Single) = {ward_count}",
                "provenance": room_details["ward_rows"]
            })
            aggregation_log.append(f"[ROOM_RENT] WC001 = {ward_count} Ward rows (AC {room_details['ward_ac']} + Single {room_details['ward_single']})")

        # Service rows for other CGHS codes
        service_rows = extract_service_rows_from_pages(pages_blocks)

        # For CC002 oxygen - need separate handling row-by-row
        oxygen_rows = []  # list of dict with qty 24/12/1

        for row in service_rows:
            row_text = row["row_text"]
            page = row["page"]
            section = row["section"]
            service_name = row["service_name"]

            # Identify every code occurrence in this row FIRST, each one
            # resolved independently (a '+' compound is several occurrences).
            # Aggregation happens further down, strictly after resolution.
            occurrences = resolve_cghs_codes(row_text, page=page,
                                             section=section,
                                             service_name=service_name)

            # An alias that no locked rule resolves is real evidence: it is
            # reported for review, never silently dropped and never executed.
            for occ in occurrences:
                if occ["status"] != EXECUTABLE:
                    rejected.append({
                        "page": str(page),
                        "block": row_text[:300],
                        "reason": f"{REVIEW_REQUIRED}: {occ['normalization_reason']}",
                        "section": section,
                        "code": occ["original_token"],
                        "status": REVIEW_REQUIRED,
                        "raw_expression": occ["raw_expression"],
                        "original_token": occ["original_token"],
                    })

            normalized = [(o["final_code"], o["normalization_reason"])
                          for o in occurrences if o["status"] == EXECUTABLE]
            locked_quantities = {o["final_code"]: o["quantity"]
                                 for o in occurrences
                                 if o["status"] == EXECUTABLE
                                 and o["quantity"] is not None}

            if not normalized:
                if not occurrences:
                    # CGHS marker but nothing code-shaped at all in the row.
                    rejected.append({
                        "page": str(page),
                        "block": row_text[:300],
                        "reason": "CGHS marker but no valid code could be normalized from row",
                        "section": section
                    })
                # occurrences that need review are already recorded above
                continue

            for code, reason in normalized:
                # Special handling for CN002 - must be discarded, calculated from Room Rent only
                if code == "CN002":
                    rejected.append({
                        "page": str(page),
                        "block": row_text[:300],
                        "reason": "CN002 raw occurrence rejected - CN002 must be calculated from Room Rent only, not from consultation rows",
                        "section": section,
                        "code": code
                    })
                    continue

                # For CC001/WC001, we already have from Room Rent, so reject any raw CC001/WC001 from other sections (should not happen, but just in case)
                if code in ["CC001","WC001"]:
                    # If this row is not from Room Rent, it's not valid source per new rule
                    # Room Rent rows don't have CGHS, so any CC001/WC001 from CGHS rows is suspicious - reject unless it's actually Room Rent?
                    # Actually CC001 can also appear as ICU code? But spec says CC001 and WC001 must be from Room Rent only, so reject CGHS-derived CC001/WC001
                    rejected.append({
                        "page": str(page),
                        "block": row_text[:300],
                        "reason": f"{code} raw occurrence rejected - {code} must be from Room Rent(999311) only, not from {section}",
                        "section": section,
                        "code": code
                    })
                    continue

                # For CC002 oxygen - handle separately
                if code == "CC002" or code == "C002":
                    # Check if row contains OXYGEN
                    if "OXYGEN" in row_text.upper():
                        qty = parse_oxygen_quantity(row_text)
                        # Normalize to CC002
                        final_code = "CC002"
                        oxygen_rows.append({
                            "code": final_code,
                            "qty": qty,
                            "page": page,
                            "source_block": row_text[:500],
                            "category": "OXYGEN",
                            "source_page": page,
                            "source_section": section,
                            "source_service_name": service_name,
                            "source_row_text": row_text[:500],
                            "normalization_reason": f"OXYGEN row: {reason} + oxygen qty {qty} based on FULL/HALF in same row"
                        })
                        continue
                    else:
                        # CC002 without OXYGEN? Could be other CC002? But spec says CC002 is OXYGEN
                        # Still treat as CC002 with qty 1? But better reject if not OXYGEN?
                        # For safety, if code is C002 but not OXYGEN, maybe it's not CC002? Let's check
                        # In our earlier data, OXYGEN rows had CGHS-C C002, so code C002 is actually CC002 per normalization
                        # So we should still treat C002 as CC002 only if OXYGEN present
                        # If C002 appears without OXYGEN, maybe it's not OXYGEN? But spec says CC002 is OXYGEN, so only count when OXYGEN present
                        if "OXYGEN" not in row_text.upper():
                            # Might be mis-normalized, reject?
                            # Let's check if row actually contains OXYGEN keyword nearby, if not, this might be hallucinated C002 from other
                            rejected.append({
                                "page": str(page),
                                "block": row_text[:300],
                                "reason": f"{code} without OXYGEN keyword rejected - CC002 must have OXYGEN in same row",
                                "section": section,
                                "code": code
                            })
                            continue

                # Quantity: a locked rule wins (Oxygen full/half day), else
                # the explicit row quantity.  An amount is never a quantity.
                qty = locked_quantities.get(code)
                if qty is None:
                    qty = parse_row_quantity(row_text)

                # Validate code against VALID_CODES (already done in normalize)
                if code not in VALID_CODES:
                    rejected.append({
                        "page": str(page),
                        "block": row_text[:300],
                        "reason": f"Code {code} not in VALID_CODES registry",
                        "section": section,
                        "code": code
                    })
                    continue

                # If we reach here, we have valid code with evidence
                occ = {
                    "code": code,
                    "qty": qty,
                    "page": page,
                    "source_block": row_text[:500],
                    "category": section,
                    "source_page": page,
                    "source_section": section,
                    "source_service_name": service_name[:200],
                    "source_row_text": row_text[:500],
                    "normalization_reason": reason
                }
                raw_occurrences.append(occ)

        # Handle CC002 aggregation: sum oxygen quantities
        if oxygen_rows:
            total_oxy_qty = sum(r["qty"] for r in oxygen_rows)
            # Aggregate all oxygen rows into one CC002 occurrence with total qty
            # But need to keep provenance of each row
            raw_occurrences.append({
                "code": "CC002",
                "qty": total_oxy_qty,
                "page": -1,
                "source_block": f"OXYGEN total {total_oxy_qty} from {len(oxygen_rows)} rows",
                "category": "OXYGEN",
                "source_page": -1,
                "source_section": "Equipment",
                "source_service_name": "OXYGEN",
                "source_row_text": " + ".join([r["source_row_text"][:100] for r in oxygen_rows[:3]]),
                "normalization_reason": f"Sum of OXYGEN rows: " + ", ".join([f"{r['qty']}" for r in oxygen_rows]),
                "provenance": oxygen_rows,
                "oxygen_details": oxygen_rows
            })
            aggregation_log.append(f"[OXYGEN] CC002 total {total_oxy_qty} = sum of {len(oxygen_rows)} rows: " + ", ".join([f"{r['qty']}" for r in oxygen_rows]))
        else:
            # Check if there were any CC002 that were not OXYGEN? Already rejected
            pass

        # DRUG100 and CNSU100 from dept subtotals - preserve existing logic
        try:
            pharmacy = reconcile_pharmacy(full_text)
            ip_amt = pharmacy["ip_detailed"]
            ot_amt = pharmacy["ot_detailed"]
            drugs_amt = pharmacy["total"]
            if drugs_amt > 0:
                # No upper bound.  A real bill's pharmacy spend runs well
                # past any round number; the old ``drugs_amt < 500000`` gate,
                # combined with the same cutoff inside extract_dept_subtotal,
                # silently zeroed the large pharmacy department and emitted
                # only the small one.
                #
                # Nor is execution gated on the Service Summary agreeing with
                # the detailed subtotals: a summary line can aggregate several
                # detailed departments, so a difference is structure, not a
                # contradiction.  See rules.reconcile_pharmacy.
                raw_occurrences.append({
                    "code": "DRUG100",
                    "qty": 1,
                    "amount": round(drugs_amt,2),
                    "page": -1,
                    "source_block": f"IP Pharmacy {ip_amt:.2f} + OT Pharmacy {ot_amt:.2f}",
                    "category": "DRUGS",
                    "source_page": -1,
                    "source_section": "Pharmacy",
                    "source_service_name": "IP+OT Pharmacy",
                    "source_row_text": f"IP {ip_amt} OT {ot_amt}",
                    "pharmacy_provenance": pharmacy["provenance"],
                    "normalization_reason": pharmacy["reason"],
                })
                aggregation_log.append(f"[DRUG100] {ip_amt:.2f} + {ot_amt:.2f} = {drugs_amt:.2f}")
        except Exception as e:
            rejected.append({"page": "DRUGS", "block": str(e)[:200], "reason": "DRUG100 extraction error"})

        try:
            cons_total, cons_details = extract_consumables_total(full_text)
            if cons_total > 0:   # no magnitude cutoff - see rules.extract_dept_subtotal
                raw_occurrences.append({
                    "code": "CNSU100",
                    "qty": 1,
                    "amount": round(cons_total,2),
                    "page": -1,
                    "source_block": " + ".join([f"{pat} {val:.2f}" for pat,val in cons_details]),
                    "category": "CONSUMABLES",
                    "source_page": -1,
                    "source_section": "Consumables",
                    "source_service_name": "Consumables",
                    "source_row_text": str(cons_details)[:500],
                    "normalization_reason": f"CNSU100 total {cons_total:.2f} from consumable departments"
                })
                aggregation_log.append(f"[CNSU100] total {cons_total:.2f}")
        except Exception as e:
            rejected.append({"page": "CONSUMABLES", "block": str(e)[:200], "reason": "CNSU100 extraction error"})

        # CN002 from Room Rent - must be added after removing raw CN002
        if cn002_qty is not None and cn002_qty > 0:
            raw_occurrences.append({
                "code": "CN002",
                "qty": cn002_qty,
                "page": -1,
                "source_block": f"CN002 = ICU {icu_count}*3 + Ward {ward_count}*2 = {cn002_qty}",
                "category": "ROOM_RENT_CALC",
                "source_page": -1,
                "source_section": "ROOM RENT(999311)",
                "source_service_name": "CN002 calculated",
                "source_row_text": f"ICU {icu_count} Ward {ward_count} => {cn002_qty}",
                "normalization_reason": f"CN002 = ({icu_count}*3)+({ward_count}*2) = {cn002_qty} from Room Rent rows only"
            })
            aggregation_log.append(f"[CN002] {icu_count}*3 + {ward_count}*2 = {cn002_qty}")

        # Aggregation
        aggregated = {}
        aggregated_amount = {}
        for occ in raw_occurrences:
            c = occ["code"]
            if c in ["DRUG100","CNSU100"] and "amount" in occ:
                aggregated_amount[c] = aggregated_amount.get(c,0.0) + float(occ["amount"])
                aggregated[c] = 1
            else:
                aggregated[c] = aggregated.get(c,0) + occ["qty"]

        # Build final_items
        final_items = []
        # Order: CN002 first if exists
        if "CN002" in aggregated:
            final_items.append({"code":"CN002","qty": aggregated["CN002"], "provenance": [o for o in raw_occurrences if o["code"]=="CN002"]})
        for code, qty in sorted(aggregated.items()):
            if code == "CN002":
                continue
            if code in ["DRUG100","CNSU100"]:
                amt = round(aggregated_amount.get(code,0.0),2)
                if amt>0:
                    final_items.append({"code":code,"qty":1,"amount":amt, "provenance": [o for o in raw_occurrences if o["code"]==code]})
                continue
            final_items.append({"code":code,"qty":qty, "provenance": [o for o in raw_occurrences if o["code"]==code]})

        # Patient name
        name_match = re.search(r'Name\s*:\s*([A-Za-z\.\s]+)', full_text, re.IGNORECASE)
        patient_name = name_match.group(1).strip().split("\n")[0].strip() if name_match else os.path.basename(pdf_path)
        patient_name = re.sub(r'\s+', ' ', patient_name).strip()
        if len(patient_name) < 3:
            patient_name = os.path.basename(pdf_path)

        return final_items, raw_occurrences, patient_name, rejected, aggregation_log
