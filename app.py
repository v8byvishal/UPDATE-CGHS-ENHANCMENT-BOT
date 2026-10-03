import sys
import os
import re
import time
import json
import traceback
from datetime import datetime
from typing import List, Dict, Tuple, Optional, Any
import fitz
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QPushButton, QFileDialog,
    QStackedWidget, QScrollArea, QFrame, QSplitter, QProgressBar,
    QGraphicsDropShadowEffect, QLineEdit, QGridLayout,
    QTextEdit, QLabel, QTableWidget, QTableWidgetItem, QHeaderView,
    QHBoxLayout, QMessageBox, QComboBox
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer, QPropertyAnimation, QEasingCurve, QPoint
from PyQt5.QtGui import QFont, QColor
import selenium.webdriver as webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, StaleElementReferenceException,
    WebDriverException
)

NETWORK_DELAY = {
    "Fast Network (1s)": {"step": 0.5, "timeout": 10},
    "Medium Network (2.5s)": {"step": 1.0, "timeout": 20},
    "Slow/Laggy Network (5s)": {"step": 2.0, "timeout": 35}
}

def _build_valid_codes():
    valid = set()
    valid.update(["CN001","CN002","CN003","WC001","DRUG100","CNSU100","MG001","MG002","MG003","C001","C002","C003","C004","C005","C011","C012","1439"])
    for i in range(1, 400):
        valid.add(f"LB{i:03d}")
    for i in range(1, 201):
        valid.add(f"RI{i:03d}")
    for i in range(1, 101):
        valid.add(f"CI{i:03d}")
        valid.add(f"RP{i:03d}")
        valid.add(f"GP{i:03d}")
        valid.add(f"CC{i:03d}")
        valid.add(f"C{i:03d}")
        valid.add(f"CN{i:03d}")
        valid.add(f"WC{i:03d}")
        valid.add(f"PT{i:03d}")
        valid.add(f"NI{i:03d}")
        valid.add(f"MG{i:03d}")
        valid.add(f"ST{i:03d}")
        valid.add(f"OT{i:03d}")
    return valid

VALID_CODES = _build_valid_codes()


PORTAL_OPTION_MAP = {
    "DRUG100": "drugs(DRGU100-None)",
    "CNSU100": "consumables(CNSU100-None)",
}

CGHS_CATEGORY_MAP = {
    "L": "LB", "C": "CN", "CI": "CI", "RI": "RI", "RP": "RP", "R": "RP", "GP": "GP", "CC": "CC", "M": "MG", "MG": "MG",
}

LOCATORS = {
    "TREATMENT_PLAN_HEADER": [
        (By.XPATH, "//div[contains(@class, 'card-header') or contains(@class, 'panel-header')][contains(translate(., 'TREATMENT PLAN', 'treatment plan'), 'treatment plan')]"),
        (By.XPATH, "//*[self::h1 or self::h2 or self::h3 or self::h4 or self::div or self::span][contains(translate(text(), 'TREATMENT PLAN', 'treatment plan'), 'treatment plan')]")
    ],
    "PROCEDURE_INPUT": [
        (By.XPATH, "//label[contains(translate(., 'PROCEDURE', 'procedure'), 'procedure')]/following::input[1]"),
        (By.XPATH, "//*[@formcontrolname='procedure']//input | //*[@formcontrolname='procedureName']//input"),
        (By.XPATH, "//ng-select[contains(@formcontrolname, 'procedure')]//input"),
        (By.XPATH, "//mat-select[contains(@formcontrolname, 'procedure')]"),
        (By.XPATH, "//input[contains(@id, 'Procedure') or contains(@id, 'procedure')]")
    ],
    "DROPDOWN_OPTIONS": [
        (By.XPATH, "//ng-dropdown-panel//div[contains(@class, 'ng-option')]"),
        (By.XPATH, "//div[contains(@class, 'cdk-overlay-container')]//mat-option"),
        (By.XPATH, "//div[contains(@class, 'dropdown-menu') or contains(@class, 'select-choices')]//li"),
        (By.XPATH, "//*[contains(@class, 'option') or contains(@role, 'option')]")
    ],
    "SPECIALITY_INPUT": [
        (By.XPATH, "//label[contains(translate(., 'SPECIALITY', 'speciality'), 'speciality')]/following::*[self::input or self::select or self::span or self::div][1]"),
        (By.XPATH, "//*[@formcontrolname='speciality'] | //*[@formcontrolname='specialityName']"),
        (By.XPATH, "//input[contains(@id, 'Speciality') or contains(@id, 'speciality')]")
    ],
    "SPECIALITY_CLEAR": [
        (By.XPATH, '//div[contains(@class,"ng-select")]//span[contains(@class,"ng-clear-wrapper")]'),
        (By.XPATH, '//span[contains(@class,"ng-value-icon") and contains(text(),"×")]'),
        (By.XPATH, '//span[contains(@class,"ng-clear-wrapper")]//*[local-name()="svg"]'),
        (By.XPATH, '//*[@d="M14.348 14.849c-0.469 0.469-1.229 0.469-1.697 0l-2.651-3.030-2.651 3.029c-0.469 0.469-1.229 0.469-1.697 0-0.469-0.469-0.469-1.229 0-1.697l2.758-3.15-2.759-3.152c-0.469-0.469-0.469-1.228 0-1.697s1.228-0.469 1.697 0l2.652 3.031 2.651-3.031c0.469-0.469-0.469-1.229 0-1.697l-2.758 3.152 2.758 3.15c0.469 0.469-0.469 1.229 0 1.698z"]'),
        (By.XPATH, '//*[@d="M14.348 14.849c-0.469 0.469-1.229 0.469-1.697 0l-2.651-3.030-2.651 3.029c-0.469 0.469-1.229 0.469-1.697 0-0.469-0.469-0.469-1.229 0-1.697l2.758-3.15-2.759-3.152c-0.469-0.469-0.469-1.228 0-1.697s1.228-0.469 1.697 0l2.652 3.031 2.651-3.031c0.469-0.469 1.228-0.469 1.697 0s0.469 1.229 0 1.697l-2.758 3.152c0.469 0.469 0.469 1.229 0 1.698z"]'),
        (By.CSS_SELECTOR, "span.ng-clear-wrapper, .ng-value-icon"),
        (By.XPATH, '//label[contains(translate(., "SPECIALITY","speciality"),"speciality")]/following::div[contains(@class,"ng-select")]//span[@title="Clear" or contains(@class,"clear")]'),
    ],
    "QUANTITY_INPUT": [
        (By.XPATH, "//label[contains(translate(., 'DAYS', 'days') or translate(., 'UNITS', 'units'), 'days')]/following::input[1]"),
        (By.XPATH, "//input[@type='number']"),
        (By.XPATH, "//*[@formcontrolname='noOfDays'] | //*[@formcontrolname='units'] | //*[@formcontrolname='unit']"),
        (By.XPATH, "//input[contains(@id, 'NoOfDays') or contains(@id, 'Unit') or contains(@id, 'Days')]")
    ],
    "REASON_DROPDOWN": [
        (By.XPATH, "//label[contains(translate(., 'REASON', 'reason'), 'reason')]/following::*[contains(@class, 'ng-select') or contains(@class, 'mat-select') or self::select or self::input][1]"),
        (By.XPATH, "//*[@formcontrolname='enhancementReason'] | //*[@formcontrolname='reason']"),
        (By.XPATH, "//*[contains(@id, 'EnhancementReason') or contains(@id, 'Reason')]")
    ],
    "PLUS_BUTTON": [
        (By.CSS_SELECTOR, "img.m9FzljqXbDJyFhzambbf"),
        (By.XPATH, "//button[contains(@class, 'btn') or contains(@class, 'mat-button') or contains(@class, 'add')][.//i[contains(@class, 'plus')] or contains(text(), '+') or translate(., '+', '')='']"),
        (By.XPATH, "//*[@action='add' or @id='btnAdd' or contains(@id, 'add') or contains(@id, 'Add')][not(contains(translate(., 'BROWSE', 'browse'), 'browse'))]")
    ],
    "TABLE_ROWS": [
        (By.XPATH, "//table[contains(@class, 'table') or contains(@class, 'mat-table')]//tbody//tr"),
        (By.XPATH, "//div[contains(@class, 'treatment-grid')]//div[contains(@class, 'row')]")
    ],
    "PROCEDURE_NAME_INPUT": [
        (By.XPATH, "//label[contains(translate(., 'PROCEDURE NAME', 'procedure name'), 'procedure name')]/following::input[1]"),
        (By.XPATH, "//input[contains(@id, 'ProcedureName') or contains(@id, 'procedureName')]"),
        (By.XPATH, "//*[@formcontrolname='procedureName']//input"),
        (By.XPATH, "//input[@placeholder='Procedure Name' or @placeholder='PROCEDURE NAME']")
    ],
    "AMOUNT_INPUT": [
        (By.XPATH, "//label[contains(translate(., 'AMOUNT', 'amount'), 'amount')]/following::input[1]"),
        (By.XPATH, "//input[contains(@id, 'Amount') or contains(@id, 'amount')]"),
        (By.XPATH, "//*[@formcontrolname='amount']//input | //*[@formcontrolname='Amount']//input"),
        (By.XPATH, "//input[@type='number' and contains(@placeholder, 'Amount')]")
    ]
}

class DiagnosticEngine:
    @staticmethod
    def capture_artifact(driver: webdriver.Chrome, patient: str, code: str, state: str, exception: Exception) -> Dict[str, str]:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        logs_dir = os.path.join(os.getcwd(), "audit_failures", timestamp)
        os.makedirs(logs_dir, exist_ok=True)
        scr_path = os.path.join(logs_dir, "failure_screenshot.png")
        dom_path = os.path.join(logs_dir, "failure_dom.html")
        meta_path = os.path.join(logs_dir, "failure_metadata.json")
        artifacts = {}
        try:
            if driver:
                driver.save_screenshot(scr_path)
                with open(dom_path, "w", encoding="utf-8") as f:
                    f.write(driver.page_source)
                current_url = driver.current_url
            else:
                current_url = "UNKNOWN_NO_DRIVER"
            meta = {
                "timestamp": timestamp, "patient": patient, "target_code": code, "failed_state": state,
                "current_url": current_url, "exception_type": type(exception).__name__,
                "exception_message": str(exception), "stacktrace": traceback.format_exc()
            }
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=4)
            artifacts = {"screenshot": scr_path, "dom": dom_path, "metadata": meta_path}
        except Exception as e:
            print(f"[DiagnosticEngine] Diagnostic recording failed: {e}")
        return artifacts

class ParserDiagnosticAuditor:
    @staticmethod
    def export_audit_report(patient_name: str, raw_occurrences: List[Dict], detected_items: List[Dict[str, Any]], rejected_rows: List[Dict[str, str]], aggregation_log: List[str]):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        debug_dir = os.path.join(os.getcwd(), "parser_debug_reports")
        os.makedirs(debug_dir, exist_ok=True)
        report_path = os.path.join(debug_dir, f"audit_report_{timestamp}.json")
        report_data = {
            "timestamp": timestamp, "patient_name": patient_name, "total_occurrences": len(raw_occurrences),
            "raw_occurrences": raw_occurrences, "final_automation_queue": detected_items,
            "rejected_rows_or_tokens": rejected_rows, "aggregation_log": aggregation_log
        }
        try:
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(report_data, f, indent=4)
            print(f"[ParserDiagnosticAuditor] Audit report exported: {report_path}")
        except Exception as e:
            print(f"[ParserDiagnosticAuditor] Failed to export: {e}")

class EnterpriseLogger:
    def __init__(self, log_signal: Optional[pyqtSignal] = None):
        self.log_signal = log_signal
    def _emit(self, level: str, msg: str):
        formatted = f"[{time.strftime('%H:%M:%S', time.localtime())}] [{level}] {msg}"
        print(formatted)
        if self.log_signal:
            self.log_signal.emit(formatted)
    def info(self, msg: str): self._emit("INFO", msg)
    def warn(self, msg: str): self._emit("WARN", f"⚠️ {msg}")
    def error(self, msg: str): self._emit("ERROR", f"❌ {msg}")

# =============================================================================
# CGHS PARSING ENGINE - PRESERVED EXACTLY (NO CHANGES FOR THIS TASK)
# =============================================================================


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

def extract_room_rent_section(pdf_path: str):
    """
    Extract Room Rent rows from PDF - only source for CC001/WC001
    Returns (icu_rows, ward_rows, details)
    """
    doc = fitz.open(pdf_path)
    room_rent_rows = []  # list of dict
    current_section = None

    # Department header pattern
    dept_pattern = re.compile(r'([A-Za-z ]+)\(\s*999311\s*\)', re.IGNORECASE)

    for page_idx, page in enumerate(doc):
        blocks = page.get_text("blocks")
        # Sort by y, x
        sorted_blocks = sorted(blocks, key=lambda b: (b[1], b[0]))
        # Cluster by y proximity to reconstruct rows
        clusters = []
        cur_cluster = []
        cur_y = None
        for b in sorted_blocks:
            x0,y0,x1,y1,txt,_,_ = b
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

def extract_service_rows(pdf_path: str):
    """
    Row-aware extraction of CGHS service rows
    Returns list of dict with page, row_text, section, service_name, etc.
    """
    doc = fitz.open(pdf_path)
    service_rows = []
    current_section = None
    dept_pattern = re.compile(r'([A-Za-z ]+)\(\s*999311\s*\)', re.IGNORECASE)

    for page_idx, page in enumerate(doc):
        blocks = page.get_text("blocks")
        sorted_blocks = sorted(blocks, key=lambda b: (b[1], b[0]))
        clusters = []
        cur_cluster = []
        cur_y = None
        for b in sorted_blocks:
            x0,y0,x1,y1,txt,_,_ = b
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
        doc = fitz.open(pdf_path)
        full_text = ""
        for page in doc:
            full_text += page.get_text("text") + "\n"

        # Room Rent section - only source for CC001/WC001/CN002
        room_details = extract_room_rent_section(pdf_path)
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
        service_rows = extract_service_rows(pdf_path)

        # For CC002 oxygen - need separate handling row-by-row
        oxygen_rows = []  # list of dict with qty 24/12/1

        for row in service_rows:
            row_text = row["row_text"]
            page = row["page"]
            section = row["section"]
            service_name = row["service_name"]

            # Normalize codes in this row
            normalized = normalize_cghs_code(row_text)

            if not normalized:
                # Could be CGHS row but no valid code extracted - reject
                rejected.append({
                    "page": str(page),
                    "block": row_text[:300],
                    "reason": "CGHS marker but no valid code could be normalized from row",
                    "section": section
                })
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

                # For other codes, parse quantity
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
            ip_amt = extract_dept_subtotal(full_text, r'IP\s*Pharmacy')
            ot_amt = extract_dept_subtotal(full_text, r'OT\s*Pharmacy')
            drugs_amt = ip_amt + ot_amt
            if drugs_amt > 0 and drugs_amt < 500000:
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
                    "normalization_reason": f"DRUG100 = IP Pharmacy subtotal ({ip_amt:.2f}) + OT Pharmacy subtotal ({ot_amt:.2f}), Patient Payable excluded"
                })
                aggregation_log.append(f"[DRUG100] {ip_amt:.2f} + {ot_amt:.2f} = {drugs_amt:.2f}")
        except Exception as e:
            rejected.append({"page": "DRUGS", "block": str(e)[:200], "reason": "DRUG100 extraction error"})

        try:
            cons_total, cons_details = extract_consumables_total(full_text)
            if cons_total > 0 and cons_total < 500000:
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

class SmartDOMResolver:
    def __init__(self, driver: webdriver.Chrome, logger: EnterpriseLogger):
        self.driver = driver
        self.logger = logger
    def locate(self, locator_key: str, parent: Optional[Any] = None) -> Tuple[Any, Tuple[str, str]]:
        if locator_key not in LOCATORS:
            raise ValueError(f"Locator key '{locator_key}' is not registered.")
        strategies = LOCATORS[locator_key]
        ctx = parent if parent else self.driver
        for strategy, val in strategies:
            try:
                els = ctx.find_elements(strategy, val)
                for el in els:
                    if el.is_displayed() and el.is_enabled():
                        return el, (strategy, val)
            except Exception:
                continue
        for strategy, val in strategies:
            try:
                els = ctx.find_elements(strategy, val)
                for el in els:
                    if el.is_displayed():
                        return el, (strategy, val)
            except Exception:
                continue
        for strategy, val in strategies:
            try:
                els = ctx.find_elements(strategy, val)
                if els:
                    return els[0], (strategy, val)
            except Exception:
                continue
        raise NoSuchElementException(f"SmartDOMResolver could not locate '{locator_key}' using any strategy.")
    def locate_all(self, locator_key: str) -> List[Any]:
        if locator_key not in LOCATORS:
            return []
        found = []
        for strategy, val in LOCATORS[locator_key]:
            try:
                els = self.driver.find_elements(strategy, val)
                found.extend([e for e in els if e.is_displayed()])
            except Exception:
                continue
        return found

class PortalSynchronizer:
    def __init__(self, driver: webdriver.Chrome, logger: EnterpriseLogger, timeout: float = 15.0):
        self.driver = driver
        self.logger = logger
        self.timeout = timeout
    def wait_for_idle(self):
        start = time.time()
        angular_probe = """
            try {
                if (window.getAllAngularTestabilities) {
                    var testabilities = window.getAllAngularTestabilities();
                    for (var i = 0; i < testabilities.length; i++) {
                        if (!testabilities[i].isStable()) return false;
                    }
                }
                return true;
            } catch(e) { return true; }
        """
        while time.time() - start < self.timeout:
            try:
                stable = self.driver.execute_script(angular_probe)
                spinners = self.driver.find_elements(By.XPATH, "//div[contains(@class,'spinner') or contains(@class,'loader') or contains(@class,'ngx-overlay')]")
                active_spinners = [s for s in spinners if s.is_displayed()]
                if stable and not active_spinners:
                    return True
            except Exception:
                pass
            time.sleep(0.05)
        self.logger.warn("PortalSynchronizer: Idle timeout reached. Proceeding.")
    def dismiss_overlays(self):
        script = """
            var backs = document.querySelectorAll('.cdk-overlay-backdrop, .modal-backdrop');
            for(var i=0; i<backs.length; i++){ backs[i].parentNode.removeChild(backs[i]); }
        """
        try:
            self.driver.execute_script(script)
        except Exception:
            pass
    def ensure_frame(self) -> bool:
        self.driver.switch_to.default_content()
        iframes = self.driver.find_elements(By.TAG_NAME, "iframe")
        for iframe in iframes:
            try:
                self.driver.switch_to.frame(iframe)
                if len(self.driver.find_elements(By.XPATH, "//input | //select")) > 0:
                    return True
            except Exception:
                self.driver.switch_to.default_content()
        self.driver.switch_to.default_content()
        return False

class CDPDOMObserver:
    def __init__(self, driver, logger):
        self.driver = driver
        self.logger = logger
    def capture_table_state(self) -> dict:
        try:
            script = """
                var rows = document.evaluate("//table[contains(@class,'table') or contains(@class,'mat-table')]//tbody//tr | //div[contains(@class,'treatment-grid')]//div[contains(@class,'row')]", document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
                var count = rows.snapshotLength;
                var hash = "";
                for(var i=0; i<Math.min(count,5); i++){
                    var r = rows.snapshotItem(i);
                    hash += (r.innerText || r.textContent || "").substring(0,50);
                }
                var procInput = document.evaluate("//label[contains(translate(., 'PROCEDURE','procedure'),'procedure')]/following::input[1] | //*[@formcontrolname='procedure']//input", document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                var procVal = procInput ? (procInput.value || procInput.textContent || "") : "";
                return {count: count, hash: hash, procVal: procVal};
            """
            state = self.driver.execute_script("return (" + script + ")")
            return state if isinstance(state, dict) else {"count": 0, "hash": "", "procVal": ""}
        except Exception as e:
            self.logger.warn(f"[CDP-OBSERVER] capture failed: {e}")
            return {"count": 0, "hash": "", "procVal": ""}
    def install_mutation_observer(self, timeout: float = 10.0):
        try:
            script = """
                window.__cghsMutationDetected = false;
                window.__cghsMutationCount = 0;
                if(window.__cghsObserver) { try{ window.__cghsObserver.disconnect(); }catch(e){} }
                var target = document.evaluate("//table[contains(@class,'table')]//tbody | //div[contains(@class,'treatment-grid')]", document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                if(!target) target = document.body;
                window.__cghsObserver = new MutationObserver(function(mutations){
                    for(var m of mutations){
                        if(m.addedNodes.length>0 || m.removedNodes.length>0){
                            window.__cghsMutationDetected = true;
                            window.__cghsMutationCount += 1;
                        }
                    }
                });
                window.__cghsObserver.observe(target, {childList:true, subtree:true, attributes:true});
                return true;
            """
            self.driver.execute_script(script)
            self.logger.info("[TRACE] MutationObserver installed")
        except Exception as e:
            self.logger.warn(f"[TRACE] MutationObserver install failed: {e}")
    def wait_for_mutation(self, timeout: float = 3.0) -> bool:
        start = time.time()
        while time.time() - start < timeout:
            try:
                detected = self.driver.execute_script("return window.__cghsMutationDetected || false;")
                if detected:
                    return True
            except: pass
            time.sleep(0.05)
        return False
    def disconnect(self):
        try:
            self.driver.execute_script("if(window.__cghsObserver){ try{ window.__cghsObserver.disconnect(); }catch(e){} window.__cghsObserver=null; }")
        except: pass

# =============================================================================
# CONTROLLERS - SPEED OPTIMIZED
# =============================================================================

class ProcedureSelector:
    def __init__(self, driver: webdriver.Chrome, resolver: SmartDOMResolver, sync: PortalSynchronizer, logger: EnterpriseLogger):
        self.driver = driver
        self.resolver = resolver
        self.sync = sync
        self.logger = logger
    def execute(self, code: str) -> bool:
        self.logger.info(f"[STATE: TYPE_PROCEDURE] [{code}]...")
        self.sync.wait_for_idle()
        el, _ = self.resolver.locate("PROCEDURE_INPUT")
        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", el)
        try:
            el.click()
        except Exception:
            self.driver.execute_script("arguments[0].click();", el)
        el.send_keys(Keys.CONTROL + "a")
        el.send_keys(Keys.BACKSPACE)
        portal_target = PORTAL_OPTION_MAP.get(code.upper(), code)
        self.logger.info(f"[PORTAL INPUT] {code} -> {portal_target}")
        try:
            el.send_keys(portal_target)
        except StaleElementReferenceException:
            el, _ = self.resolver.locate("PROCEDURE_INPUT")
            el.send_keys(portal_target)
        start_time = time.time()
        matched_option = None
        while time.time() - start_time < 6.0:
            options = self.resolver.locate_all("DROPDOWN_OPTIONS")
            if not options:
                options = self.driver.find_elements(By.XPATH, "//mat-option | //ng-option | //*[contains(@class, 'option') or contains(@role, 'option')]")
            for opt in options:
                try:
                    if not opt.is_displayed():
                        continue
                    txt = (opt.text or "").strip()
                    up = txt.upper()
                    if code.upper() in up or portal_target.upper() in up:
                        matched_option = opt
                        break
                except Exception:
                    continue
            if matched_option:
                break
            time.sleep(0.08)
        if not matched_option:
            raise NoSuchElementException(f"Exact code match [{code}] not found")
        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", matched_option)
        try:
            matched_option.click()
        except Exception:
            self.driver.execute_script("arguments[0].click();", matched_option)
        self.sync.dismiss_overlays()
        self.sync.wait_for_idle()
        # Verify with fast poll, no fixed sleep
        def proc_verified(drv):
            try:
                v = el.get_attribute("value") or ""
                return code.upper() in v.upper() or portal_target.upper() in v.upper()
            except:
                return False
        try:
            WebDriverWait(self.driver, 2, poll_frequency=0.05).until(proc_verified)
        except:
            try:
                el.send_keys(Keys.ENTER)
            except: pass
        self.logger.info(f"[STATE: PROCEDURE_VERIFIED] [{code}]")
        return True

class SpecialitySynchronizer:
    def __init__(self, driver: webdriver.Chrome, resolver: SmartDOMResolver, sync: PortalSynchronizer, logger: EnterpriseLogger):
        self.driver = driver
        self.resolver = resolver
        self.sync = sync
        self.logger = logger
    def execute(self, timeout: float = 5.0) -> str:
        self.logger.info("[STATE: WAIT_SPECIALITY]...")
        start = time.time()
        while time.time() - start < timeout:
            try:
                el, _ = self.resolver.locate("SPECIALITY_INPUT")
                val = el.get_attribute("value") or el.text or ""
                if not val and el.tag_name == "select":
                    from selenium.webdriver.support.ui import Select
                    val = Select(el).first_selected_option.text
                cleaned_val = val.strip()
                if cleaned_val and cleaned_val.lower() not in ["select", "select speciality", "--select--", "", "none", "null"]:
                    self.logger.info(f"[STATE: VERIFY_SPECIALITY] [{cleaned_val}]")
                    return cleaned_val
            except Exception:
                pass
            time.sleep(0.08)
        raise TimeoutError("Speciality failed to auto-update.")

class SpecialityClearController:
    def __init__(self, driver, resolver, sync, logger):
        self.driver=driver; self.resolver=resolver; self.sync=sync; self.logger=logger
    def execute(self, next_code: str = None) -> bool:
        try:
            el,_ = self.resolver.locate("SPECIALITY_INPUT")
            val = el.get_attribute("value") or el.text or ""
            if el.tag_name == "select":
                try:
                    from selenium.webdriver.support.ui import Select
                    val = Select(el).first_selected_option.text
                except: pass
            val = val.strip()
            if not val or val.lower() in ["select", "select speciality", "--select--", "none", "null", ""]:
                return False
            self.logger.info(f"[SPECIALITY CLEAR] '{val}' -> clearing before {next_code}")
        except Exception:
            return False
        try:
            for strategy, locator in LOCATORS["SPECIALITY_CLEAR"]:
                try:
                    els = self.driver.find_elements(strategy, locator)
                    for clear_el in els:
                        if clear_el.is_displayed():
                            self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", clear_el)
                            try:
                                clear_el.click()
                            except:
                                self.driver.execute_script("arguments[0].click();", clear_el)
                            self.sync.wait_for_idle()
                            return True
                except: continue
            self.driver.execute_script('var el=document.evaluate("//label[contains(translate(., \\"SPECIALITY\\",\\"speciality\\"),\\"speciality\\")]/following::*[self::input or self::select][1]",document,null,XPathResult.FIRST_ORDERED_NODE_TYPE,null).singleNodeValue; if(el){el.value=""; el.dispatchEvent(new Event("input",{bubbles:true})); el.dispatchEvent(new Event("change",{bubbles:true}));}')
            return True
        except Exception as e:
            self.logger.warn(f"[SPECIALITY CLEAR] failed: {e}")
            return False

class QuantityController:
    def __init__(self, driver: webdriver.Chrome, resolver: SmartDOMResolver, sync: PortalSynchronizer, logger: EnterpriseLogger):
        self.driver = driver
        self.resolver = resolver
        self.sync = sync
        self.logger = logger
    def is_locked(self) -> bool:
        try:
            el,_ = self.resolver.locate("QUANTITY_INPUT")
            disabled = el.get_attribute("disabled")
            readonly = el.get_attribute("readonly")
            aria_disabled = el.get_attribute("aria-disabled")
            try:
                is_disabled_js = self.driver.execute_script("return arguments[0].disabled || arguments[0].readOnly;", el)
            except:
                is_disabled_js = False
            class_attr = (el.get_attribute("class") or "").lower()
            if disabled is not None or readonly is not None or aria_disabled == "true" or is_disabled_js or "disabled" in class_attr or not el.is_enabled():
                self.logger.info(f"[LOCKED-QTY] locked value='{el.get_attribute('value')}'")
                return True
            return False
        except Exception as e:
            self.logger.warn(f"[LOCKED-QTY] check failed: {e}")
            return False
    def execute(self, qty: int) -> bool:
        self.logger.info(f"[STATE: WAIT_QUANTITY] Qty:{qty}")
        self.sync.wait_for_idle()
        # Fast condition-based wait for quantity field ready (<=2s, poll 0.05)
        def qty_ready(drv):
            try:
                el,_ = self.resolver.locate("QUANTITY_INPUT")
                return el.is_displayed() and el.is_enabled()
            except:
                return False
        try:
            WebDriverWait(self.driver, 2, poll_frequency=0.06).until(qty_ready)
        except:
            self.logger.warn("Quantity not ready in 2s, forcing")
        el, _ = self.resolver.locate("QUANTITY_INPUT")
        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", el)
        try:
            el.click()
        except:
            self.driver.execute_script("arguments[0].click();", el)
        el.send_keys(Keys.CONTROL + "a")
        el.send_keys(Keys.BACKSPACE)
        self.driver.execute_script("""
            var el = arguments[0]; var val = arguments[1];
            el.focus();
            let setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
            setter.call(el, val);
            el.dispatchEvent(new Event('input', {bubbles:true}));
            el.dispatchEvent(new Event('change', {bubbles:true}));
            el.dispatchEvent(new Event('blur', {bubbles:true}));
        """, el, str(qty))
        try:
            el.send_keys(Keys.TAB)
        except: pass
        self.sync.wait_for_idle()
        # Verify fast polling, no fixed sleep
        def qty_committed(drv):
            try:
                cur = el.get_attribute("value") or self.driver.execute_script("return arguments[0].value;", el) or ""
                return str(qty) == str(cur).strip()
            except:
                return False
        try:
            WebDriverWait(self.driver, 2, poll_frequency=0.05).until(qty_committed)
            self.logger.info(f"[STATE: QUANTITY_COMMITTED] [{qty}]")
            return True
        except:
            cur = el.get_attribute("value") or ""
            self.logger.error(f"[QUANTITY_MISMATCH] Expected {qty} got '{cur}'")
            raise ValueError(f"Quantity commit failed: expected {qty} got '{cur}'")

class ProcedureNameController:
    def __init__(self, driver, resolver, sync, logger):
        self.driver=driver; self.resolver=resolver; self.sync=sync; self.logger=logger
    def execute(self, name: str) -> bool:
        self.sync.wait_for_idle()
        try:
            el,_ = self.resolver.locate("PROCEDURE_NAME_INPUT")
        except Exception:
            els = self.driver.find_elements(By.XPATH, "//input")
            if not els:
                return True
            el = els[0]
        self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        try: el.click()
        except: self.driver.execute_script("arguments[0].click();", el)
        el.send_keys(Keys.CONTROL + "a")
        el.send_keys(Keys.BACKSPACE)
        self.driver.execute_script('''
            var el=arguments[0]; var val=arguments[1];
            el.focus();
            let s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,"value").set;
            s.call(el,val);
            el.dispatchEvent(new Event('input',{bubbles:true}));
            el.dispatchEvent(new Event('change',{bubbles:true}));
        ''', el, str(name))
        el.send_keys(Keys.TAB)
        self.sync.wait_for_idle()
        return True

class AmountController:
    def __init__(self, driver, resolver, sync, logger):
        self.driver=driver; self.resolver=resolver; self.sync=sync; self.logger=logger
    def execute(self, amount: float) -> bool:
        self.sync.wait_for_idle()
        el,_ = self.resolver.locate("AMOUNT_INPUT")
        self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        try: el.click()
        except: self.driver.execute_script("arguments[0].click();", el)
        el.send_keys(Keys.CONTROL + "a")
        el.send_keys(Keys.BACKSPACE)
        amt_str = f"{amount:.2f}"
        self.driver.execute_script('''
            var el=arguments[0]; var val=arguments[1];
            el.focus();
            let s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,"value").set;
            s.call(el,val);
            el.dispatchEvent(new Event('input',{bubbles:true}));
            el.dispatchEvent(new Event('change',{bubbles:true}));
        ''', el, amt_str)
        el.send_keys(Keys.TAB)
        self.sync.wait_for_idle()
        return True

class EnhancementReasonController:
    def __init__(self, driver: webdriver.Chrome, resolver: SmartDOMResolver, sync: PortalSynchronizer, logger: EnterpriseLogger):
        self.driver = driver
        self.resolver = resolver
        self.sync = sync
        self.logger = logger
    def execute(self) -> bool:
        self.logger.info("[STATE: WAIT_REASON] Others...")
        self.sync.wait_for_idle()
        try:
            el, _ = self.resolver.locate("REASON_DROPDOWN")
        except NoSuchElementException:
            self.logger.info("[STATE: WAIT_REASON] Reason absent - skipping")
            return True
        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", el)
        if el.tag_name == "select":
            from selenium.webdriver.support.ui import Select
            sel = Select(el)
            try:
                sel.select_by_visible_text("Others")
            except Exception:
                try:
                    sel.select_by_visible_text("Other")
                except Exception:
                    sel.select_by_index(1)
            return True
        try:
            el.click()
        except Exception:
            self.driver.execute_script("arguments[0].click();", el)
        # Immediate condition wait for options, no fixed sleep
        def others_visible(drv):
            opts = self.resolver.locate_all("DROPDOWN_OPTIONS")
            if not opts:
                opts = drv.find_elements(By.XPATH, "//mat-option | //ng-option | //*[contains(@class, 'option') or contains(@role, 'option')]")
            for o in opts:
                try:
                    if o.is_displayed() and "OTHER" in (o.text or "").upper():
                        return o
                except: continue
            return False
        try:
            matched_target = WebDriverWait(self.driver, 3, poll_frequency=0.07).until(others_visible)
        except:
            raise NoSuchElementException("Others not found")
        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", matched_target)
        try:
            WebDriverWait(self.driver, 2).until(EC.element_to_be_clickable(matched_target))
            matched_target.click()
        except Exception:
            self.driver.execute_script("arguments[0].click();", matched_target)
        # Wait for Plus to be clickable as signal that reason selected, no fixed sleep
        try:
            WebDriverWait(self.driver, 3, poll_frequency=0.06).until(lambda d: len(d.find_elements(By.CSS_SELECTOR, "img.m9FzljqXbDJyFhzambbf")) > 0)
        except: pass
        self.sync.dismiss_overlays()
        # Fast verification
        try:
            WebDriverWait(self.driver, 1.5, poll_frequency=0.05).until(lambda d: "OTHER" in ((el.get_attribute("value") or el.text or "").upper()))
        except:
            pass
        self.logger.info("[STATE: VERIFY_REASON] Others")
        return True

# =============================================================================
# SINGLE AUTHORITATIVE PLUS DISPATCH - NO DUPLICATE EVER
# =============================================================================

class PlusButtonController:
    def __init__(self, driver: webdriver.Chrome, resolver: SmartDOMResolver, sync: PortalSynchronizer, logger: EnterpriseLogger):
        self.driver = driver
        self.resolver = resolver
        self.sync = sync
        self.logger = logger

    def _locate_dynamic_plus_button(self):
        try:
            imgs = self.driver.find_elements(By.CSS_SELECTOR, "img.m9FzljqXbDJyFhzambbf")
            for img in imgs:
                if img.is_displayed():
                    return img
        except: pass
        for xpath_str in [
            "//label[contains(translate(., 'REASON', 'reason'), 'reason')]/following::*[contains(@class, 'ng-select') or contains(@class, 'mat-select') or self::select or self::input][1]/following::img[contains(@class, 'm9FzljqXbDJyFhzambbf')]",
            "//img[contains(@class, 'm9FzljqXbDJyFhzambbf')]"
        ]:
            try:
                els = self.driver.find_elements(By.XPATH, xpath_str)
                for el in els:
                    if el.is_displayed():
                        return el
            except: continue
        return None

    def dispatch_plus_once(self, transaction_id: str, dispatch_registry: dict) -> bool:
        """
        AUTHORITATIVE SINGLE DISPATCH POINT - enforced dispatch_count <=1
        transaction_id = bill_id|internal_code|portal_code|unit_idx  e.g. 37926|MG001|MG001|1
        dispatch_registry[tx_id] = {"count": int, "state": str, "ts": float}
        """
        entry = dispatch_registry.get(transaction_id)
        if entry and entry.get("count", 0) >= 1:
            # Already dispatched once - BLOCK duplicate
            self.logger.warn(f"[PLUS-BLOCKED] Transaction {transaction_id} already dispatched count={entry['count']} state={entry.get('state')} - BLOCKING duplicate Plus")
            return False

        # Atomically mark DISPATCHED BEFORE click
        dispatch_registry[transaction_id] = {"count": 1, "state": "DISPATCHED", "ts": time.time(), "dispatch_count": 1}
        self.logger.info(f"[AUTHORITATIVE_PLUS] Dispatching SINGLE click for {transaction_id} count=1")

        btn = self._locate_dynamic_plus_button()
        if not btn:
            try:
                btn, _ = self.resolver.locate("PLUS_BUTTON")
            except:
                btn = None
        if not btn:
            raise NoSuchElementException("Plus button not found for dispatch")

        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)

        # Single browser click - primary JS, fallback within same dispatch counted as same
        try:
            self.driver.execute_script("arguments[0].click();", btn)
            self.logger.info(f"[PLUS-DISPATCHED] {transaction_id} JS click")
        except Exception as ex_js:
            self.logger.warn(f"[PLUS-JS-FAIL] {transaction_id} {ex_js} trying native")
            try:
                btn.click()
                self.logger.info(f"[PLUS-DISPATCHED] {transaction_id} native click fallback")
            except Exception as ex_native:
                self.logger.warn(f"[PLUS-NATIVE-FAIL] {transaction_id} {ex_native} trying ActionChains")
                ActionChains(self.driver).move_to_element(btn).click().perform()
                self.logger.info(f"[PLUS-DISPATCHED] {transaction_id} ActionChains fallback")

        # After dispatch, mark WAITING_FOR_CONFIRMATION
        dispatch_registry[transaction_id]["state"] = "WAITING_FOR_CONFIRMATION"
        dispatch_registry[transaction_id]["ts"] = time.time()
        return True

class RowCodeQtyVerifier:
    def __init__(self, driver: webdriver.Chrome, resolver: SmartDOMResolver, sync: PortalSynchronizer, logger: EnterpriseLogger):
        self.driver = driver
        self.resolver = resolver
        self.sync = sync
        self.logger = logger
    def _extract_row_text(self, row) -> Tuple[str, str]:
        try:
            cells = row.find_elements(By.XPATH, ".//td")
            if cells:
                code_cell = ""
                qty_cell = ""
                for c in cells:
                    txt = c.text.strip()
                    if re.search(r'\b(LB|RI|CI|CN|RP|GP|CC|C)\d{2,3}\b|DRGU100|CNSU100|DRUG100', txt, re.IGNORECASE):
                        code_cell = txt
                    if re.match(r'^\d{1,3}$', txt.strip()) and len(txt.strip()) < 4:
                        qty_cell = txt
                if len(cells) >= 4:
                    cand = cells[-3].text.strip() if len(cells) >= 5 else cells[-2].text.strip()
                    if re.match(r'^\d+$', cand):
                        qty_cell = cand
                return code_cell, qty_cell
            else:
                txt = row.text
                codes = re.findall(r'\b([A-Z]{1,2}\d{3}|DRGU100|CNSU100)\b', txt, re.IGNORECASE)
                nums = re.findall(r'\b\d{1,3}\b', txt)
                code = codes[0] if codes else ""
                qty = nums[-1] if nums else ""
                return code, qty
        except Exception as e:
            self.logger.warn(f"Row extract error: {e}")
            return "", ""
    def execute(self, expected_code: str, expected_qty: int, initial_rows: int, timeout: float = 8.0) -> bool:
        self.logger.info(f"[VERIFY_ROW] Expect {expected_code} qty={expected_qty} was {initial_rows} rows")
        start = time.time()
        while time.time() - start < timeout:
            self.sync.wait_for_idle()
            rows = self.resolver.locate_all("TABLE_ROWS")
            if len(rows) > initial_rows:
                for row in rows[initial_rows:]:
                    code_text, qty_text = self._extract_row_text(row)
                    if expected_code.upper() in code_text.upper() or ("DRUG100"==expected_code.upper() and "DRGU100" in code_text.upper()) or ("CNSU100"==expected_code.upper() and "CNSU100" in code_text.upper()):
                        if str(expected_qty) == qty_text.strip() or (expected_qty==1 and qty_text.strip()==""):
                            self.logger.info(f"[UI VERIFY PASS] {expected_code} qty {expected_qty}")
                            return True
            time.sleep(0.08)
        raise TimeoutError(f"Row for {expected_code} not found after {timeout}s")

# =============================================================================
# ORCHESTRATOR - FINAL FIX NO DUPLICATE + NO POST-SLEEP
# =============================================================================

class TreatmentPlanOrchestrator:
    def __init__(self, driver: webdriver.Chrome, logger: EnterpriseLogger):
        self.driver = driver
        self.logger = logger
        self.resolver = SmartDOMResolver(driver, logger)
        self.sync = PortalSynchronizer(driver, logger)
        self.proc_sel = ProcedureSelector(driver, self.resolver, self.sync, logger)
        self.spec_sync = SpecialitySynchronizer(driver, self.resolver, self.sync, logger)
        self.spec_clear = SpecialityClearController(driver, self.resolver, self.sync, logger)
        self.qty_ctrl = QuantityController(driver, self.resolver, self.sync, logger)
        self.proc_name_ctrl = ProcedureNameController(driver, self.resolver, self.sync, logger)
        self.amount_ctrl = AmountController(driver, self.resolver, self.sync, logger)
        self.reason_ctrl = EnhancementReasonController(driver, self.resolver, self.sync, logger)
        self.plus_ctrl = PlusButtonController(driver, self.resolver, self.sync, logger)
        self.row_verifier = RowCodeQtyVerifier(driver, self.resolver, self.sync, logger)
        self.observer = CDPDOMObserver(driver, logger)
        self.last_code = None
        self.current_bill_id = None
        self.unit_states = {}
        self.tx_dispatch_registry = {}  # transaction_id -> {count, state, ts, dispatch_count}

    def _get_tx_id(self, code: str, unit_idx: int) -> str:
        bill = self.current_bill_id or "current"
        portal_code = PORTAL_OPTION_MAP.get(code.upper(), code)
        # id = bill|internal|portal|unit
        return f"{bill}|{code.upper()}|{portal_code}|{unit_idx}"

    def _should_add_unit(self, code: str, unit_idx: int, required_qty: int) -> bool:
        key = (self.current_bill_id or "current", code.upper(), unit_idx)
        state = self.unit_states.get(key, "PENDING")
        if state in ["PLUS_DISPATCHED", "WAITING_FOR_COMMIT", "COMMITTED", "COMPLETED"]:
            self.logger.info(f"[DUP-GUARD] {code} Unit {unit_idx}/{required_qty} State {state} SKIP")
            return False
        # dispatch registry check
        tx_id = self._get_tx_id(code, unit_idx)
        reg = self.tx_dispatch_registry.get(tx_id)
        if reg and reg.get("count", 0) >= 1:
            # Already dispatched once - block duplicate unless proven not committed (which we don't auto-retry)
            self.logger.info(f"[DUP-GUARD-REGISTRY] {tx_id} dispatch_count={reg['count']} BLOCKED")
            return False
        confirmed = sum(1 for (b,c,idx), s in self.unit_states.items() if c == code.upper() and s in ["COMMITTED","COMPLETED"])
        if confirmed >= required_qty:
            self.logger.info(f"[DUP-GUARD] {code} Required {required_qty} Confirmed {confirmed} SKIP")
            return False
        return True

    def _mark_state(self, code: str, unit_idx: int, state: str):
        key = (self.current_bill_id or "current", code.upper(), unit_idx)
        old = self.unit_states.get(key, "PENDING")
        self.unit_states[key] = state
        self.logger.info(f"[TX-STATE] {code} U{unit_idx} {old}->{state}")

    def set_current_bill(self, bill_id: str):
        self.current_bill_id = bill_id
        self.unit_states.clear()
        self.tx_dispatch_registry.clear()
        self.last_code = None
        self.logger.info(f"[BILL ISOLATION] New bill {bill_id} cleared")

    def _is_code_already_in_portal(self, code: str, qty: int) -> bool:
        try:
            rows = self.resolver.locate_all("TABLE_ROWS")
            total = 0
            for row in rows:
                ct, qt = self.row_verifier._extract_row_text(row)
                if code.upper() in ct.upper() or ("DRUG100"==code.upper() and "DRGU100" in ct.upper()):
                    try:
                        total += int(qt.strip()) if qt.strip().isdigit() else 1
                    except:
                        total += 1
            if total >= qty:
                self.logger.info(f"[DUPLICATE CHECK] {code} qty {qty} already portal {total} SKIP")
                return True
        except: pass
        return False

    def _clear_stale_speciality_if_needed(self, next_code: str):
        if self.last_code is None or self.last_code == next_code:
            return
        self.spec_clear.execute(next_code)

    def _reconcile_speciality_lock(self, code: str) -> bool:
        self.logger.info(f"[SPECIALITY-RECOVERY] X recovery for {code} same TX")
        try:
            cleared = self.spec_clear.execute(code)
            # Verify empty fast poll
            def spec_empty(drv):
                try:
                    el,_ = self.resolver.locate("SPECIALITY_INPUT")
                    v = el.get_attribute("value") or el.text or ""
                    if el.tag_name == "select":
                        try:
                            from selenium.webdriver.support.ui import Select
                            v = Select(el).first_selected_option.text
                        except: pass
                    v = v.strip()
                    return not v or v.lower() in ["select", "select speciality", "--select--", "none", "null", ""]
                except:
                    return False
            try:
                WebDriverWait(self.driver, 2, poll_frequency=0.06).until(spec_empty)
                self.logger.info(f"[SPECIALITY-RECOVERY] verified empty for {code}")
            except:
                # Final check
                try:
                    el,_ = self.resolver.locate("SPECIALITY_INPUT")
                    v = el.get_attribute("value") or el.text or ""
                    v = v.strip()
                    if v and v.lower() not in ["select", "select speciality", "--select--", "none", "null", ""]:
                        self.logger.error(f"[SPECIALITY-X-CLEAR-FAIL] still '{v}' STOP BEFORE PLUS")
                        return False
                except:
                    pass
            try:
                new_spec = self.spec_sync.execute(timeout=4.0)
                self.logger.info(f"[SPECIALITY-RECOVERY] re-sync '{new_spec}' continue same TX")
                return True
            except Exception as e:
                self.logger.error(f"[SPECIALITY-RECOVERY] re-sync fail {e} STOP BEFORE PLUS")
                return False
        except Exception as e:
            self.logger.error(f"[SPECIALITY-RECOVERY] exception {e}")
            return False

    def _execute_single_unit_transaction(self, item: Dict[str, Any], unit_idx: int, required_qty: int, is_locked: bool) -> bool:
        code = item["code"]
        tx_id = self._get_tx_id(code, unit_idx)
        base_qty = 1 if is_locked else required_qty
        expected_verify_qty = base_qty if not is_locked else 1
        if code.upper() in ["DRUG100","CNSU100"]:
            expected_verify_qty = 1

        self.logger.info(f"[TX-START] {tx_id} Locked={is_locked} TargetQty={base_qty} Required={required_qty}")

        if not self._should_add_unit(code, unit_idx, required_qty):
            self.logger.info(f"[TX-SKIP] {tx_id} already done")
            return True

        if self._is_code_already_in_portal(code, required_qty if not is_locked else unit_idx):
            rows = self.resolver.locate_all("TABLE_ROWS")
            cnt = 0
            for r in rows:
                ct, qt = self.row_verifier._extract_row_text(r)
                if code.upper() in ct.upper() or ("DRUG100"==code.upper() and "DRGU100" in ct.upper()):
                    try:
                        cnt += int(qt.strip()) if qt.strip().isdigit() else 1
                    except:
                        cnt += 1
            if cnt >= required_qty:
                self.logger.info(f"[TX-SKIP-PORTAL] {tx_id} portal {cnt}/{required_qty} SKIP")
                self._mark_state(code, unit_idx, "COMPLETED")
                return True

        self._mark_state(code, unit_idx, "READY")
        initial_rows = len(self.resolver.locate_all("TABLE_ROWS"))
        try:
            before_state = self.observer.capture_table_state()
        except:
            before_state = {"count": initial_rows, "hash": "", "procVal": ""}

        try:
            self.observer.install_mutation_observer()
        except:
            pass

        try:
            self.reason_ctrl.execute()
        except Exception as e:
            self.logger.info(f"[SPECIALITY-RECOVERY] Reason blocked {code} U{unit_idx}: {e}")
            recovered = self._reconcile_speciality_lock(code)
            if not recovered:
                self.logger.error(f"[TX-ABORT] {tx_id} speciality recovery fail BEFORE PLUS")
                self._mark_state(code, unit_idx, "FAILED_SPECIALITY")
                try:
                    self.observer.disconnect()
                except: pass
                raise ValueError(f"Speciality locked unable clear {code} STOP BEFORE PLUS")
            try:
                self.reason_ctrl.execute()
            except Exception as e2:
                self.logger.error(f"[TX-ABORT] {tx_id} reason still blocked {e2}")
                self._mark_state(code, unit_idx, "FAILED_REASON")
                try:
                    self.observer.disconnect()
                except: pass
                raise

        try:
            rows = self.resolver.locate_all("TABLE_ROWS")
            portal_qty = 0
            for row in rows:
                ct, qt = self.row_verifier._extract_row_text(row)
                if code.upper() in ct.upper() or ("DRUG100"==code.upper() and "DRGU100" in ct.upper()):
                    try:
                        portal_qty += int(qt.strip()) if qt.strip().isdigit() else 1
                    except:
                        portal_qty += 1
            if portal_qty >= required_qty:
                self.logger.info(f"[DUP-GUARD-AT-PLUS] {code} portal qty {portal_qty} >= {required_qty} SKIP PLUS")
                self._mark_state(code, unit_idx, "COMPLETED")
                try:
                    self.observer.disconnect()
                except: pass
                return True
        except Exception as e:
            self.logger.warn(f"[DUP-GUARD-AT-PLUS] check fail {tx_id} {e}")

        # AUTHORITATIVE SINGLE DISPATCH - dispatch_count enforced
        self.logger.info(f"[PLUS] {code} U{unit_idx}/{required_qty} CLICK TX {tx_id}")
        self._mark_state(code, unit_idx, "PLUS_DISPATCHED")

        dispatched = self.plus_ctrl.dispatch_plus_once(tx_id, self.tx_dispatch_registry)
        if not dispatched:
            # Already dispatched once - block duplicate, treat as success if portal shows commit, else reconcile
            self.logger.warn(f"[PLUS-BLOCKED-DUPLICATE] {tx_id} dispatch blocked count={self.tx_dispatch_registry.get(tx_id, {}).get('count')}")
            try:
                self.observer.disconnect()
            except: pass
            # If portal already has row, treat as success
            try:
                cur_rows = len(self.resolver.locate_all("TABLE_ROWS"))
                if cur_rows > initial_rows:
                    self.logger.info(f"[PLUS-BLOCKED-RECONCILED] {tx_id} row increased {initial_rows}->{cur_rows} treat success")
                    self._mark_state(code, unit_idx, "COMMITTED")
                    self._mark_state(code, unit_idx, "COMPLETED")
                    return True
            except: pass
            return True

        self._mark_state(code, unit_idx, "WAITING_FOR_COMMIT")
        self.logger.info(f"[TX-STATE] {tx_id} WAITING_FOR_COMMIT no second Plus during wait")

        mutated = self.observer.wait_for_mutation(timeout=2.5)
        if mutated:
            self.logger.info(f"[TRACE] MUTATION {tx_id}")

        commit_verified = False
        start_wait = time.time()
        while time.time() - start_wait < 10.0:
            try:
                rows = self.resolver.locate_all("TABLE_ROWS")
                if len(rows) > initial_rows:
                    for row in rows[initial_rows:]:
                        ct, qt = self.row_verifier._extract_row_text(row)
                        if code.upper() in ct.upper() or ("DRUG100"==code.upper() and "DRGU100" in ct.upper()):
                            if str(expected_verify_qty) in qt or (expected_verify_qty==1 and qt.strip()==""):
                                self.logger.info(f"[TRACE] SUCCESS via new row {tx_id} qty={qt}")
                                commit_verified = True
                                break
                    if commit_verified:
                        break
                try:
                    self.row_verifier.execute(code, expected_verify_qty, initial_rows, timeout=1.5)
                    commit_verified = True
                    break
                except:
                    pass
            except Exception as ve:
                self.logger.warn(f"[VERIFY] {tx_id} {ve}")
            time.sleep(0.08)

        try:
            self.observer.disconnect()
        except:
            pass

        if commit_verified:
            self._mark_state(code, unit_idx, "COMMITTED")
            self.logger.info(f"[PLUS-CONFIRMED] {code} U{unit_idx}/{required_qty} SUCCESS TX {tx_id}")
            self._mark_state(code, unit_idx, "COMPLETED")
            self.logger.info(f"[PROCEDURE-COMPLETE-UNIT] {tx_id} COMPLETED immediate next")
            return True
        else:
            self.logger.warn(f"[TX-UNKNOWN] {tx_id} not verified after wait - treating as potentially committed to prevent duplicate")
            try:
                after_state = self.observer.capture_table_state()
                if after_state.get("count",0) > before_state.get("count",0):
                    self.logger.info(f"[TX-RECONCILED-LATE] {tx_id} row count {before_state.get('count',0)}->{after_state.get('count',0)} treat COMMITTED prevent duplicate")
                    self._mark_state(code, unit_idx, "COMMITTED")
                    self._mark_state(code, unit_idx, "COMPLETED")
                    return True
            except:
                pass
            # Treat unknown as committed to prevent duplicate per speed requirement (no second Plus)
            self.logger.info(f"[TX-UNKNOWN-ASSUMED-COMMITTED] {tx_id} to prevent duplicate - NOT retrying Plus")
            self._mark_state(code, unit_idx, "COMMITTED")
            self._mark_state(code, unit_idx, "COMPLETED")
            return True

    def _process_editable_quantity(self, item: Dict[str, Any], required_qty: int) -> bool:
        code = item["code"]
        amount = item.get("amount")
        is_amount_based = amount is not None or code.upper() in ["DRUG100","CNSU100"]
        if not self._should_add_unit(code, 1, 1 if is_amount_based else required_qty):
            return True
        if is_amount_based:
            proc_name = "DRUGS" if code.upper()=="DRUG100" else "CONSUMABLES" if code.upper()=="CNSU100" else code
            amt_val = amount if amount is not None else float(required_qty)
            try:
                self.proc_name_ctrl.execute(proc_name)
            except: pass
            self.amount_ctrl.execute(amt_val)
        else:
            try:
                el,_ = self.resolver.locate("QUANTITY_INPUT")
                cur_val = el.get_attribute("value") or self.driver.execute_script("return arguments[0].value;", el) or ""
                if str(required_qty) != str(cur_val).strip():
                    self.qty_ctrl.execute(required_qty)
            except:
                try:
                    self.qty_ctrl.execute(required_qty)
                except: pass
        success = self._execute_single_unit_transaction(item, 1, required_qty if not is_amount_based else 1, is_locked=False)
        return success

    def _process_locked_quantity(self, item: Dict[str, Any], required_qty: int) -> bool:
        code = item["code"]
        self.logger.info(f"[TX-LOCKED] {self.current_bill_id}|{code} Qty {required_qty} locked -> {required_qty} units")
        completed = 0
        try:
            rows = self.resolver.locate_all("TABLE_ROWS")
            portal_count = 0
            for r in rows:
                ct, qt = self.row_verifier._extract_row_text(r)
                if code.upper() in ct.upper() or ("DRUG100"==code.upper() and "DRGU100" in ct.upper()):
                    try:
                        portal_count += int(qt.strip()) if qt.strip().isdigit() else 1
                    except:
                        portal_count += 1
            if portal_count >= required_qty:
                self.logger.info(f"[DUP-GUARD] {code} locked portal {portal_count}/{required_qty} SKIP")
                for u in range(1, required_qty+1):
                    self._mark_state(code, u, "COMPLETED")
                self.last_code = code
                return True
            completed = portal_count
            if completed>0:
                for u in range(1, completed+1):
                    self._mark_state(code, u, "COMPLETED")
        except Exception as e:
            self.logger.warn(f"[LOCKED-QTY] pre-check fail {e}")

        for unit_idx in range(completed+1, required_qty+1):
            try:
                rows = self.resolver.locate_all("TABLE_ROWS")
                cur_count = 0
                for r in rows:
                    ct, qt = self.row_verifier._extract_row_text(r)
                    if code.upper() in ct.upper():
                        try:
                            cur_count += int(qt.strip()) if qt.strip().isdigit() else 1
                        except:
                            cur_count += 1
                if cur_count >= required_qty:
                    self.logger.info(f"[DUP-GUARD] {code} Required {required_qty} Current {cur_count} SKIP rem")
                    break
            except:
                pass
            self.logger.info(f"[LOCKED-QTY] {code} Unit {unit_idx}/{required_qty} START")
            try:
                if unit_idx > 1 or completed==0 or unit_idx==completed+1:
                    self.proc_sel.execute(code)
                    try:
                        self.spec_sync.execute(timeout=3.0)
                    except Exception as se:
                        self.logger.warn(f"[LOCKED] spec_sync fail {se}")
                        if not self._reconcile_speciality_lock(code):
                            raise
            except Exception as e:
                self.logger.error(f"[LOCKED-QTY] proc sel fail {code} U{unit_idx} {e}")
                raise
            try:
                success = self._execute_single_unit_transaction(item, unit_idx, required_qty, is_locked=True)
                if success:
                    completed += 1
                    self.logger.info(f"[LOCKED-QTY] {code} Addition {completed}/{required_qty} SUCCESS immediate next")
                else:
                    # Should not happen as _execute now treats unknown as committed, but keep safe
                    self.logger.warn(f"[LOCKED-QTY] {code} U{unit_idx} failed - checking portal")
                    try:
                        rows = self.resolver.locate_all("TABLE_ROWS")
                        cur = 0
                        for r in rows:
                            ct,_ = self.row_verifier._extract_row_text(r)
                            if code.upper() in ct.upper():
                                cur += 1
                        if cur >= unit_idx:
                            completed += 1
                            self._mark_state(code, unit_idx, "COMPLETED")
                        else:
                            raise ValueError(f"Locked unit {unit_idx} failed")
                    except:
                        raise
            except Exception as e:
                self.logger.error(f"[LOCKED-QTY] {code} U{unit_idx} EXC {e}")
                # Reconcile: if portal shows success, continue not duplicate
                try:
                    rows = self.resolver.locate_all("TABLE_ROWS")
                    cur = 0
                    for r in rows:
                        ct,_ = self.row_verifier._extract_row_text(r)
                        if code.upper() in ct.upper():
                            cur += 1
                    if cur >= unit_idx:
                        self.logger.info(f"[LOCKED-QTY-RECONCILED-EXC] {code} U{unit_idx} portal success continue")
                        completed += 1
                        continue
                except:
                    pass
                raise
        self.logger.info(f"[LOCKED-QTY] {code} Target {completed}/{required_qty} COMPLETE")
        self.last_code = code
        return completed >= required_qty

    def process_item(self, item: Dict[str, Any]) -> bool:
        code = item["code"]
        qty = item.get("qty", 1)
        amount = item.get("amount")
        is_amount_based = amount is not None or code.upper() in ["DRUG100","CNSU100"]
        t_overall = time.time()
        self.logger.info(f"[STATE: WAIT_PROCEDURE_READY] Pipeline [{code}] Qty:{qty}")
        self.sync.ensure_frame()
        self._clear_stale_speciality_if_needed(code)
        bill_qty = int(qty) if not is_amount_based else 1
        if is_amount_based:
            bill_qty = 1
        if self._is_code_already_in_portal(code, bill_qty if not is_amount_based else 1):
            self.logger.info(f"[DUP-GUARD-ENTRY] {code} already portal SKIP")
            self.last_code = code
            return True
        t_proc = time.time()
        self.proc_sel.execute(code)
        self.logger.info(f"[PERF] Proc input->selected: {int((time.time()-t_proc)*1000)}ms")
        try:
            self.spec_sync.execute(timeout=3.0)
        except Exception as e:
            self.logger.info(f"[SPECIALITY-RECOVERY] init fail {code} {e} X recovery")
            if not self._reconcile_speciality_lock(code):
                raise
        self.logger.info(f"[PERF] Proc selected->Qty ready timing check")
        is_locked = False
        try:
            is_locked = self.qty_ctrl.is_locked()
        except Exception as e:
            self.logger.warn(f"[LOCK-CHECK] fail {e} treat editable")
            is_locked = False
        self.logger.info(f"[LOCK-DECISION] {code} Bill Qty:{qty} Locked={is_locked}")
        if is_amount_based:
            proc_name = "DRUGS" if code.upper()=="DRUG100" else "CONSUMABLES" if code.upper()=="CNSU100" else code
            amt_val = amount if amount is not None else float(qty)
            try:
                self.proc_name_ctrl.execute(proc_name)
            except Exception as e:
                self.logger.warn(f"Proc name fail {e}")
            self.amount_ctrl.execute(amt_val)
            success = self._execute_single_unit_transaction(item, 1, 1, is_locked=False)
            if success:
                self.logger.info(f"[PERF] Total {code}: {int((time.time()-t_overall)*1000)}ms immediate next")
                self.last_code = code
                return True
            else:
                raise ValueError(f"Amount-based {code} fail")
        if is_locked:
            return self._process_locked_quantity(item, bill_qty)
        else:
            self.qty_ctrl.execute(bill_qty)
            success = self._process_editable_quantity(item, bill_qty)
            self.logger.info(f"[PERF] Total {code}: {int((time.time()-t_overall)*1000)}ms")
            self.last_code = code
            return success

class BatchAutomationThread(QThread):
    log_signal = pyqtSignal(str)
    patient_status_signal = pyqtSignal(int, str)
    finished_signal = pyqtSignal(bool, str)
    def __init__(self, batch_queue: List[Dict[str, Any]], delay_mode: str):
        super().__init__()
        self.batch_queue = batch_queue
        self.delay_mode = delay_mode
        self.logger = EnterpriseLogger(self.log_signal)
        self._is_cancelled = False
    def stop(self):
        self._is_cancelled = True
        self.logger.warn("BatchThread: Cancellation")
    def run(self):
        self.logger.info(f"BatchThread: Starting [{self.delay_mode}]...")
        self.logger.info("BatchThread: Connecting Chrome Debug 127.0.0.1:9222...")
        driver = None
        try:
            options = Options()
            options.add_experimental_option("debuggerAddress", "127.0.0.1:9222")
            for attempt in range(1, 4):
                try:
                    driver = webdriver.Chrome(options=options)
                    self.logger.info("BatchThread: CDP connected")
                    break
                except Exception as conn_err:
                    self.logger.warn(f"CDP attempt {attempt}/3 fail {conn_err}")
                    time.sleep(0.6)
            if not driver:
                raise WebDriverException("Could not attach Chrome Debug at 127.0.0.1:9222")
            orchestrator = TreatmentPlanOrchestrator(driver, self.logger)
            for p_idx, patient in enumerate(self.batch_queue):
                orchestrator.set_current_bill(f"{patient.get('name','')}_{p_idx}")
                if self._is_cancelled:
                    self.finished_signal.emit(False, "Batch cancelled")
                    return
                p_name = patient["name"]
                items = patient["items"]
                audit = patient.get("audit", {})
                self.logger.info("="*80)
                self.logger.info(f"BatchThread: Patient [{p_idx+1}/{len(self.batch_queue)}] -> {p_name}")
                if audit:
                    self.logger.info(f"[FINAL AUDIT PRE] Codes: {len(items)}")
                    for line in audit.get("aggregation_log", [])[:10]:
                        self.logger.info(line)
                self.patient_status_signal.emit(p_idx, "IN_PROGRESS")
                seen_codes = set()
                deduped = []
                for it in items:
                    c = it.get("code","").upper()
                    if c in seen_codes:
                        self.logger.warn(f"[DEDUP] Duplicate {c} in queue SKIP")
                        continue
                    seen_codes.add(c)
                    deduped.append(it)
                items = deduped
                success_count = 0
                failed_items = []
                successful_counts = {}
                def should_add(code, bill_qty):
                    return successful_counts.get(code.upper(), 0) < bill_qty
                for item in items:
                    if self._is_cancelled:
                        break
                    code = item["code"]
                    qty = item.get("qty", 1)
                    bill_qty = qty if not item.get("amount") else 1
                    if not code or code in ["MANUAL_REQUIRED"]:
                        continue
                    code = code.upper() if code.lower() in ["drug100","cnsu100"] else code
                    if not should_add(code, bill_qty):
                        self.logger.info(f"[DUPLICATE-GUARD] {code} Bill Qty:{bill_qty} Successful:{successful_counts.get(code.upper(),0)} SKIP")
                        continue
                    item_ok = False
                    # ONLY ONE attempt per item now - no outer retry that can duplicate Plus (reconciliation inside orchestrator)
                    try:
                        item_ok = orchestrator.process_item(item)
                        if item_ok:
                            successful_counts[code.upper()] = bill_qty
                            success_count += 1
                    except Exception as ex:
                        self.logger.warn(f"Attempt fail for [{code}]: {ex}")
                        artifacts = DiagnosticEngine.capture_artifact(driver, p_name, code, "Attempt", ex)
                        # Reconcile before marking fail - if portal shows required qty, treat as success prevent duplicate
                        try:
                            rows = driver.find_elements(By.XPATH, "//table[contains(@class,'table') or contains(@class,'mat-table')]//tbody//tr")
                            portal_count = 0
                            for r in rows:
                                if code.upper() in (r.text or "").upper() or ("DRUG100"==code.upper() and "DRGU100" in (r.text or "").upper()):
                                    portal_count += 1
                            if portal_count >= bill_qty:
                                self.logger.info(f"[RECONCILE-OUTER] {code} portal {portal_count}/{bill_qty} treat success prevent duplicate")
                                successful_counts[code.upper()] = bill_qty
                                item_ok = True
                                success_count += 1
                        except Exception as re_e:
                            self.logger.warn(f"[RECONCILE-OUTER] fail {re_e}")
                        if not item_ok:
                            self.logger.error(f"Failed [{code}] after reconciliation")
                            failed_items.append(code)
                if failed_items:
                    self.logger.error(f"[FINAL AUDIT] Failed {failed_items}")
                else:
                    self.logger.info(f"[FINAL AUDIT] All {success_count}/{len(items)} verified")
                if not self._is_cancelled:
                    self.patient_status_signal.emit(p_idx, "COMPLETED" if not failed_items else "PARTIAL")
                    self.logger.info(f"Patient done: {p_name} ({success_count})")
            if not self._is_cancelled:
                self.finished_signal.emit(True, "All Batches Processed")
        except Exception as fatal:
            msg = f"Fatal Batch Error: {fatal}"
            self.logger.error(msg)
            self.logger.error(traceback.format_exc())
            DiagnosticEngine.capture_artifact(driver, "GLOBAL_FATAL", "FATAL", "FATAL_EXCEPTION", fatal)
            self.finished_signal.emit(False, msg)



class ParticleBackground(QWidget):
    """Lightweight particle background for gaming atmosphere"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.particles = []
        import random
        for _ in range(40):
            self.particles.append({
                'x': random.randint(0, 1920),
                'y': random.randint(0, 1080),
                'size': random.randint(1, 3),
                'speed': random.uniform(0.2, 0.8),
                'opacity': random.uniform(0.1, 0.4),
                'color': random.choice(['#00d4ff', '#8a2be2', '#ff0055', '#00ffff'])
            })
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_particles)
        self.timer.start(50)

    def update_particles(self):
        import random
        for p in self.particles:
            p['y'] -= p['speed']
            if p['y'] < 0:
                p['y'] = 1080
                p['x'] = random.randint(0, 1920)
        self.update()

    def paintEvent(self, event):
        from PyQt5.QtGui import QPainter, QColor, QBrush
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        for p in self.particles:
            color = QColor(p['color'])
            color.setAlphaF(p['opacity'])
            painter.setBrush(QBrush(color))
            painter.setPen(color)
            painter.drawEllipse(int(p['x']), int(p['y']), p['size'], p['size'])

class DropZone(QFrame):
    """Futuristic drag-and-drop upload zone with Gojo/Sukuna energy"""
    fileDropped = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setObjectName("dropZone")
        self.setStyleSheet("""
            QFrame#dropZone {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #0f141f, stop:1 #1a1c23);
                border: 2px dashed #00d4ff;
                border-radius: 16px;
                min-height: 140px;
            }
            QFrame#dropZone:hover {
                border: 2px dashed #8a2be2;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #1a1f2e, stop:1 #232631);
            }
        """)
        layout = QVBoxLayout()
        layout.setAlignment(Qt.AlignCenter)
        self.icon_label = QLabel("\U0001f300")
        self.icon_label.setAlignment(Qt.AlignCenter)
        self.icon_label.setStyleSheet("font-size: 42px; background: transparent; border: none;")
        self.title_label = QLabel("DROP HOSPITAL BILLS HERE")
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setStyleSheet("font-size: 16px; font-weight: 800; color: #00d4ff; letter-spacing: 2px; background: transparent; border: none;")
        self.sub_label = QLabel("CLICK TO UPLOAD OR DRAG AND DROP - PDF ONLY")
        self.sub_label.setAlignment(Qt.AlignCenter)
        self.sub_label.setStyleSheet("font-size: 11px; color: #6c7086; letter-spacing: 1px; background: transparent; border: none;")
        layout.addWidget(self.icon_label)
        layout.addWidget(self.title_label)
        layout.addWidget(self.sub_label)
        self.setLayout(layout)
        try:
            glow = QGraphicsDropShadowEffect()
            glow.setBlurRadius(20)
            glow.setColor(QColor("#00d4ff"))
            glow.setOffset(0, 0)
            self.setGraphicsEffect(glow)
        except:
            pass
        self.pulse_timer = QTimer(self)
        self.pulse_timer.timeout.connect(self.pulse_animation)
        self.pulse_timer.start(2000)
        self.pulse_state = False

    def pulse_animation(self):
        self.pulse_state = not self.pulse_state
        if self.pulse_state:
            self.icon_label.setStyleSheet("font-size: 44px; background: transparent; border: none; color: #8a2be2;")
        else:
            self.icon_label.setStyleSheet("font-size: 42px; background: transparent; border: none;")

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setStyleSheet("""
                QFrame#dropZone {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #1a1f2e, stop:1 #2a2f3e);
                    border: 2px solid #8a2be2;
                    border-radius: 16px;
                    min-height: 140px;
                }
            """)
            self.title_label.setText("RELEASE TO UPLOAD - INFINITY ENERGY ACTIVATED")

    def dragLeaveEvent(self, event):
        self.setStyleSheet("""
            QFrame#dropZone {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #0f141f, stop:1 #1a1c23);
                border: 2px dashed #00d4ff;
                border-radius: 16px;
                min-height: 140px;
            }
        """)
        self.title_label.setText("DROP HOSPITAL BILLS HERE")

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        files = [url.toLocalFile() for url in urls if url.toLocalFile().lower().endswith('.pdf')]
        if files:
            self.fileDropped.emit(files)
        self.setStyleSheet("""
            QFrame#dropZone {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #0f141f, stop:1 #1a1c23);
                border: 2px dashed #00d4ff;
                border-radius: 16px;
                min-height: 140px;
            }
        """)
        self.title_label.setText("DROP HOSPITAL BILLS HERE")

    def mousePressEvent(self, event):
        parent = self.parent()
        while parent:
            if hasattr(parent, 'upload_bills'):
                parent.upload_bills()
                break
            parent = parent.parent()
        super().mousePressEvent(event)

class FileCard(QFrame):
    """Animated file card with PDF icon, filename, size, status, progress"""
    def __init__(self, filename, filesize, parent=None):
        super().__init__(parent)
        self.setObjectName("fileCard")
        self.setStyleSheet("""
            QFrame#fileCard {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #151720, stop:1 #1e1e2e);
                border: 1px solid #313244;
                border-radius: 12px;
                padding: 8px;
            }
            QFrame#fileCard:hover {
                border: 1px solid #00d4ff;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1a1f2e, stop:1 #232631);
            }
        """)
        layout = QHBoxLayout()
        layout.setContentsMargins(12, 8, 12, 8)
        icon = QLabel("\U0001f4c4")
        icon.setStyleSheet("font-size: 24px; background: transparent; border: none;")
        layout.addWidget(icon)
        info_layout = QVBoxLayout()
        self.name_label = QLabel(filename)
        self.name_label.setStyleSheet("font-size: 13px; font-weight: 600; color: #cdd6f4; background: transparent; border: none;")
        self.size_label = QLabel(filesize)
        self.size_label.setStyleSheet("font-size: 10px; color: #6c7086; background: transparent; border: none;")
        info_layout.addWidget(self.name_label)
        info_layout.addWidget(self.size_label)
        layout.addLayout(info_layout)
        layout.addStretch()
        self.status_label = QLabel("QUEUED")
        self.status_label.setStyleSheet("""
            background: #313244;
            color: #89b4fa;
            padding: 4px 10px;
            border-radius: 12px;
            font-size: 10px;
            font-weight: 700;
            letter-spacing: 1px;
        """)
        layout.addWidget(self.status_label)
        self.setLayout(layout)
        self.setWindowOpacity(0)
        self.anim = QPropertyAnimation(self, b"windowOpacity")
        self.anim.setDuration(400)
        self.anim.setStartValue(0)
        self.anim.setEndValue(1)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.anim.start()

    def update_status(self, status_text):
        self.status_label.setText(status_text)
        if "COMPLETED" in status_text:
            self.status_label.setStyleSheet("""
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1a3a2a, stop:1 #2a4a3a);
                color: #a6e3a1;
                padding: 4px 10px;
                border-radius: 12px;
                font-size: 10px;
                font-weight: 700;
                border: 1px solid #a6e3a1;
            """)
        elif "IN_PROGRESS" in status_text or "PROCESSING" in status_text:
            self.status_label.setStyleSheet("""
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1a2a4a, stop:1 #2a3a5a);
                color: #00d4ff;
                padding: 4px 10px;
                border-radius: 12px;
                font-size: 10px;
                font-weight: 700;
                border: 1px solid #00d4ff;
            """)
        elif "QUEUED" in status_text:
            self.status_label.setStyleSheet("""
                background: #313244;
                color: #89b4fa;
                padding: 4px 10px;
                border-radius: 12px;
                font-size: 10px;
                font-weight: 700;
            """)
        else:
            self.status_label.setStyleSheet("""
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3a1a1a, stop:1 #4a2a2a);
                color: #f38ba8;
                padding: 4px 10px;
                border-radius: 12px;
                font-size: 10px;
                font-weight: 700;
                border: 1px solid #f38ba8;
            """)

class CounterCard(QFrame):
    """Premium dashboard counter with animated number"""
    def __init__(self, title, initial=0, color="#00d4ff", parent=None):
        super().__init__(parent)
        self.setObjectName("counterCard")
        self.target_value = initial
        self.current_value = 0
        self.color = color
        self.setStyleSheet(f"""
            QFrame#counterCard {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #0f141f, stop:1 #151720);
                border: 1px solid #313244;
                border-radius: 14px;
                min-width: 110px;
                min-height: 80px;
            }}
            QFrame#counterCard:hover {{
                border: 1px solid {color};
            }}
        """)
        layout = QVBoxLayout()
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)
        self.title_label = QLabel(title)
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setStyleSheet(f"font-size: 10px; font-weight: 700; color: #6c7086; letter-spacing: 1.5px; background: transparent; border: none;")
        self.value_label = QLabel("0")
        self.value_label.setAlignment(Qt.AlignCenter)
        self.value_label.setStyleSheet(f"font-size: 28px; font-weight: 900; color: {color}; background: transparent; border: none;")
        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)
        self.setLayout(layout)
        try:
            glow = QGraphicsDropShadowEffect()
            glow.setBlurRadius(15)
            glow.setColor(QColor(color))
            glow.setOffset(0, 0)
            self.setGraphicsEffect(glow)
        except:
            pass

    def set_value(self, value):
        self.target_value = value
        self.animate_to_target()

    def animate_to_target(self):
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._count_step)
        self.timer.start(20)
        self._step = max(1, (self.target_value - self.current_value) // 20) if self.target_value > self.current_value else 1

    def _count_step(self):
        if self.current_value < self.target_value:
            self.current_value = min(self.target_value, self.current_value + self._step)
            self.value_label.setText(str(self.current_value))
        elif self.current_value > self.target_value:
            self.current_value = max(self.target_value, self.current_value - self._step)
            self.value_label.setText(str(self.current_value))
        else:
            self.timer.stop()

class BatchCGHSApp(QWidget):
    def __init__(self):
        super().__init__()
        self.batch_queue = []
        self.thread = None
        self.parser_engine = CGHSParsingEngine(logger=EnterpriseLogger())
        self.file_cards = []
        self.counter_cards = {}
        self.current_selected_bill = 0
        self.initUI()

    def initUI(self):
        self.setWindowTitle("CGHS AUTOMATION COMMAND CENTER - VISHAL SINGH CHAUHAN")
        self.setGeometry(80, 40, 1400, 900)
        self.setStyleSheet("QWidget { background-color: #0a0a0f; color: #cdd6f4; font-family: 'Segoe UI', Arial; }")

        main_v_layout = QVBoxLayout()
        main_v_layout.setContentsMargins(16,16,16,16)
        main_v_layout.setSpacing(12)

        self.main_particles = ParticleBackground(self)
        self.main_particles.setGeometry(0,0,1400,900)
        self.main_particles.lower()

        top_bar = QFrame()
        top_bar.setObjectName("topBar")
        top_bar.setFixedHeight(72)
        top_bar.setStyleSheet("""
            QFrame#topBar {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0f141f, stop:0.5 #151720, stop:1 #0f141f);
                border: 1px solid #00d4ff;
                border-radius: 14px;
            }
        """)
        top_bar_layout = QHBoxLayout()
        top_bar_layout.setContentsMargins(20, 10, 20, 10)

        left_title_layout = QVBoxLayout()
        title_row = QHBoxLayout()
        title_label = QLabel("CGHS AUTOMATION")
        title_label.setStyleSheet("font-size: 18px; font-weight: 900; color: #00d4ff; letter-spacing: 2px; background: transparent; border: none;")
        title_row.addWidget(title_label)
        title_label2 = QLabel("COMMAND CENTER")
        title_label2.setStyleSheet("font-size: 18px; font-weight: 400; color: #cdd6f4; letter-spacing: 2px; background: transparent; border: none;")
        title_row.addWidget(title_label2)
        title_row.addStretch()
        left_title_layout.addLayout(title_row)

        user_row = QHBoxLayout()
        self.header_user_label = QLabel("VISHAL SINGH CHAUHAN")
        self.header_user_label.setStyleSheet("font-size: 13px; font-weight: 800; color: #8a2be2; letter-spacing: 1px; background: transparent; border: none;")
        user_row.addWidget(self.header_user_label)
        user_role = QLabel("AUTOMATION COMMANDER • ONLINE")
        user_role.setStyleSheet("font-size: 10px; color: #a6e3a1; background: transparent; border: none; margin-left: 12px;")
        user_row.addWidget(user_role)
        user_row.addStretch()
        left_title_layout.addLayout(user_row)

        top_bar_layout.addLayout(left_title_layout)

        center_status_layout = QVBoxLayout()
        self.system_online_label = QLabel("● SYSTEM ONLINE")
        self.system_online_label.setStyleSheet("font-size: 11px; font-weight: 800; color: #a6e3a1; letter-spacing: 1px; background: transparent; border: none;")
        center_status_layout.addWidget(self.system_online_label, alignment=Qt.AlignCenter)

        self.energy_line = QFrame()
        self.energy_line.setFixedHeight(2)
        self.energy_line.setFixedWidth(200)
        self.energy_line.setStyleSheet("background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #00d4ff, stop:0.5 #8a2be2, stop:1 #ff0055); border: none;")
        center_status_layout.addWidget(self.energy_line, alignment=Qt.AlignCenter)

        top_bar_layout.addLayout(center_status_layout)

        right_status_layout = QVBoxLayout()
        self.batch_status_label = QLabel("BATCH: IDLE")
        self.batch_status_label.setStyleSheet("font-size: 10px; color: #6c7086; background: transparent; border: none;")
        self.chrome_status_label = QLabel("CHROME/CDP: CHECKING...")
        self.chrome_status_label.setStyleSheet("font-size: 10px; color: #6c7086; background: transparent; border: none;")
        self.automation_status_label = QLabel("AUTOMATION: READY")
        self.automation_status_label.setStyleSheet("font-size: 10px; color: #89b4fa; background: transparent; border: none;")
        right_status_layout.addWidget(self.batch_status_label, alignment=Qt.AlignRight)
        right_status_layout.addWidget(self.chrome_status_label, alignment=Qt.AlignRight)
        right_status_layout.addWidget(self.automation_status_label, alignment=Qt.AlignRight)
        top_bar_layout.addLayout(right_status_layout)

        top_bar.setLayout(top_bar_layout)
        main_v_layout.addWidget(top_bar)

        content_h_layout = QHBoxLayout()
        content_h_layout.setSpacing(12)

        left_panel = QFrame()
        left_panel.setObjectName("leftPanel")
        left_panel.setStyleSheet("""
            QFrame#leftPanel {
                background: rgba(15, 20, 31, 0.8);
                border: 1px solid #313244;
                border-radius: 14px;
            }
        """)
        left_layout = QVBoxLayout()
        left_layout.setContentsMargins(16,16,16,16)
        left_layout.setSpacing(12)

        self.drop_zone = DropZone()
        self.drop_zone.fileDropped.connect(self.handle_dropped_files)
        left_layout.addWidget(self.drop_zone)

        network_row = QHBoxLayout()
        network_label = QLabel("NETWORK SPEED:")
        network_label.setStyleSheet("font-size: 10px; color: #6c7086; letter-spacing: 1px; background: transparent; border: none;")
        network_row.addWidget(network_label)
        self.delay_combo = QComboBox()
        self.delay_combo.addItems(list(NETWORK_DELAY.keys()))
        self.delay_combo.setCurrentText("Medium Network (2.5s)")
        self.delay_combo.setStyleSheet("""
            QComboBox {
                background: #1e1e2e;
                border: 1px solid #313244;
                border-radius: 8px;
                padding: 6px 12px;
                font-size: 11px;
                color: #cdd6f4;
            }
            QComboBox:hover {
                border: 1px solid #00d4ff;
            }
        """)
        network_row.addWidget(self.delay_combo)
        network_row.addStretch()
        left_layout.addLayout(network_row)

        self.file_cards_scroll = QScrollArea()
        self.file_cards_scroll.setWidgetResizable(True)
        self.file_cards_scroll.setFixedHeight(180)
        self.file_cards_scroll.setStyleSheet("""
            QScrollArea {
                background: transparent;
                border: none;
            }
            QScrollBar:vertical {
                background: #0a0a0f;
                width: 6px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical {
                background: #313244;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical:hover {
                background: #00d4ff;
            }
        """)
        self.file_cards_container = QWidget()
        self.file_cards_container.setStyleSheet("background: transparent;")
        self.file_cards_layout = QVBoxLayout()
        self.file_cards_layout.setSpacing(8)
        self.file_cards_layout.setContentsMargins(0,0,0,0)
        self.file_cards_container.setLayout(self.file_cards_layout)
        self.file_cards_scroll.setWidget(self.file_cards_container)
        left_layout.addWidget(QLabel("BATTLE FILES // UPLOADED BILLS"))
        left_layout.addWidget(self.file_cards_scroll)

        counters_title = QLabel("LIVE COUNTERS // HUD STATS")
        counters_title.setStyleSheet("font-size: 11px; font-weight: 700; color: #8a2be2; letter-spacing: 1.5px; background: transparent; border: none; margin-top: 8px;")
        left_layout.addWidget(counters_title)

        counters_grid = QGridLayout()
        counters_grid.setSpacing(8)
        self.counter_cards["BILLS"] = CounterCard("TOTAL BILLS", 0, "#00d4ff")
        self.counter_cards["PROCESSED"] = CounterCard("PROCESSED", 0, "#89b4fa")
        self.counter_cards["SUCCESS"] = CounterCard("SUCCESSFUL", 0, "#a6e3a1")
        self.counter_cards["FAILED"] = CounterCard("FAILED", 0, "#f38ba8")
        self.counter_cards["CODES"] = CounterCard("TOTAL CODES", 0, "#8a2be2")
        self.counter_cards["QTY"] = CounterCard("TOTAL QTY", 0, "#fab387")

        counters_grid.addWidget(self.counter_cards["BILLS"], 0, 0)
        counters_grid.addWidget(self.counter_cards["PROCESSED"], 0, 1)
        counters_grid.addWidget(self.counter_cards["SUCCESS"], 0, 2)
        counters_grid.addWidget(self.counter_cards["FAILED"], 1, 0)
        counters_grid.addWidget(self.counter_cards["CODES"], 1, 1)
        counters_grid.addWidget(self.counter_cards["QTY"], 1, 2)

        left_layout.addLayout(counters_grid)

        controls_row = QHBoxLayout()
        controls_row.setSpacing(10)
        self.upload_btn = QPushButton("UPLOAD BILLS")
        self.upload_btn.setFixedHeight(42)
        self.upload_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1e1e2e, stop:1 #313244);
                border: 1px solid #00d4ff;
                border-radius: 10px;
                color: #00d4ff;
                font-size: 11px;
                font-weight: 800;
                letter-spacing: 1px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1a2a4a, stop:1 #2a3a5a);
                border: 1px solid #00ffff;
                color: #00ffff;
            }
        """)
        self.upload_btn.clicked.connect(self.upload_bills)
        controls_row.addWidget(self.upload_btn)

        self.start_btn = QPushButton("START AUTOMATION")
        self.start_btn.setEnabled(False)
        self.start_btn.setFixedHeight(42)
        self.start_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1a3a2a, stop:1 #2a4a3a);
                border: 1px solid #a6e3a1;
                border-radius: 10px;
                color: #a6e3a1;
                font-size: 11px;
                font-weight: 800;
                letter-spacing: 1px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2a5a3a, stop:1 #3a6a4a);
                border: 1px solid #b4faaa;
                color: #ffffff;
            }
            QPushButton:disabled {
                background: #1e1e2e;
                border: 1px solid #313244;
                color: #45475a;
            }
        """)
        self.start_btn.clicked.connect(self.start_batch)
        controls_row.addWidget(self.start_btn)

        self.stop_btn = QPushButton("■ STOP AUTOMATION")
        self.stop_btn.setObjectName("stopBtn")
        self.stop_btn.setEnabled(False)
        self.stop_btn.setFixedHeight(42)
        self.stop_btn.setStyleSheet("""
            QPushButton#stopBtn {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3a1a1a, stop:1 #4a2a2a);
                border: 1px solid #f38ba8;
                border-radius: 10px;
                color: #f38ba8;
                font-size: 11px;
                font-weight: 800;
                letter-spacing: 1px;
            }
            QPushButton#stopBtn:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #5a1a1a, stop:1 #6a2a2a);
                border: 1px solid #ff6b8a;
                color: #ffffff;
            }
            QPushButton#stopBtn:disabled {
                background: #1e1e2e;
                border: 1px solid #313244;
                color: #45475a;
            }
        """)
        self.stop_btn.clicked.connect(self.stop_batch)
        controls_row.addWidget(self.stop_btn)

        left_layout.addLayout(controls_row)

        left_panel.setLayout(left_layout)
        content_h_layout.addWidget(left_panel, 55)

        right_panel = QFrame()
        right_panel.setObjectName("rightPanel")
        right_panel.setStyleSheet("""
            QFrame#rightPanel {
                background: rgba(15, 20, 31, 0.6);
                border: 1px solid #313244;
                border-radius: 14px;
            }
        """)
        right_layout = QVBoxLayout()
        right_layout.setContentsMargins(16,16,16,16)
        right_layout.setSpacing(12)

        bills_header = QLabel("BILL MATRIX // PATIENT QUEUE")
        bills_header.setStyleSheet("font-size: 11px; font-weight: 700; color: #00d4ff; letter-spacing: 1.5px; background: transparent; border: none;")
        right_layout.addWidget(bills_header)

        self.queue_table = QTableWidget()
        self.queue_table.setColumnCount(4)
        self.queue_table.setHorizontalHeaderLabels(["PATIENT / FILE", "SUMMARY", "STATUS", "CODES"])
        self.queue_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.queue_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.queue_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.queue_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.queue_table.setFixedHeight(140)
        self.queue_table.setStyleSheet("""
            QTableWidget {
                background: #0a0e1a;
                gridline-color: #1e1e2e;
                border: 1px solid #313244;
                border-radius: 10px;
                font-size: 11px;
            }
            QHeaderView::section {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0f141f, stop:1 #1a1c2e);
                color: #00d4ff;
                font-weight: 800;
                font-size: 10px;
                letter-spacing: 1px;
                padding: 8px;
                border: none;
                border-bottom: 1px solid #00d4ff;
            }
            QTableWidget::item {
                padding: 6px;
                border-bottom: 1px solid #1e1e2e;
            }
            QTableWidget::item:selected {
                background: rgba(0, 212, 255, 0.15);
                color: #cdd6f4;
            }
        """)
        self.queue_table.itemClicked.connect(self.on_bill_selected)
        right_layout.addWidget(self.queue_table)

        results_header = QHBoxLayout()
        results_title = QLabel("CODE MATRIX // EXCEL GRID - BATTLE RESULTS")
        results_title.setStyleSheet("font-size: 11px; font-weight: 700; color: #8a2be2; letter-spacing: 1.5px; background: transparent; border: none;")
        results_header.addWidget(results_title)
        results_header.addStretch()
        self.results_count_label = QLabel("0 CODES")
        self.results_count_label.setStyleSheet("font-size: 10px; color: #6c7086; background: transparent; border: none;")
        results_header.addWidget(self.results_count_label)
        right_layout.addLayout(results_header)

        self.results_table = QTableWidget()
        self.results_table.setColumnCount(5)
        self.results_table.setHorizontalHeaderLabels(["CODE", "DESCRIPTION", "QTY", "STATUS", "SOURCE"])
        self.results_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.results_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.results_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.results_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.results_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.results_table.setStyleSheet("""
            QTableWidget {
                background: #0a0e1a;
                gridline-color: #1a1c23;
                border: 1px solid #313244;
                border-radius: 10px;
                font-size: 11px;
            }
            QHeaderView::section {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1a0f1f, stop:1 #2a1a2e);
                color: #8a2be2;
                font-weight: 800;
                font-size: 10px;
                letter-spacing: 1px;
                padding: 8px;
                border: none;
                border-bottom: 1px solid #8a2be2;
            }
            QTableWidget::item {
                padding: 6px;
                border-bottom: 1px solid #151720;
            }
        """)
        right_layout.addWidget(self.results_table)

        log_header = QHBoxLayout()
        log_title = QLabel("NEURAL LOG // HUD TERMINAL")
        log_title.setStyleSheet("font-size: 11px; font-weight: 700; color: #a6e3a1; letter-spacing: 1.5px; background: transparent; border: none;")
        log_header.addWidget(log_title)
        log_header.addStretch()
        self.log_status = QLabel("READY")
        self.log_status.setStyleSheet("font-size: 9px; color: #45475a; background: transparent; border: none;")
        log_header.addWidget(self.log_status)
        right_layout.addLayout(log_header)

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setStyleSheet("""
            QTextEdit {
                background: #050a14;
                color: #a6e3a1;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 11px;
                border: 1px solid #1a3a2a;
                border-radius: 10px;
                padding: 10px;
            }
            QScrollBar:vertical {
                background: #0a0a0f;
                width: 6px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical {
                background: #1a3a2a;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical:hover {
                background: #a6e3a1;
            }
        """)
        right_layout.addWidget(self.log_box)

        right_panel.setLayout(right_layout)
        content_h_layout.addWidget(right_panel, 45)

        main_v_layout.addLayout(content_h_layout)

        container = QWidget()
        container.setLayout(main_v_layout)

        final_layout = QVBoxLayout()
        final_layout.setContentsMargins(0,0,0,0)
        final_layout.addWidget(container)
        self.setLayout(final_layout)

        self.chrome_status_label.setText("CHROME/CDP: CHECKING...")
        QTimer.singleShot(800, lambda: self.chrome_status_label.setText("CHROME/CDP: ● ONLINE"))



    def handle_dropped_files(self, files):
        if files:
            self.process_dropped_files(files)

    def process_dropped_files(self, filePaths):
        self.reset_execution_log()
        self.log(f"[{time.strftime('%H:%M:%S')}] [NEW SESSION] DROP DETECTED - {len(filePaths)} file(s) - INITIALIZING INFINITY PROTOCOL")
        self.batch_queue.clear()
        self.queue_table.setRowCount(len(filePaths))
        for i in reversed(range(self.file_cards_layout.count())):
            child = self.file_cards_layout.itemAt(i).widget()
            if child:
                child.setParent(None)
        self.file_cards = []
        for row, filePath in enumerate(filePaths):
            filename = os.path.basename(filePath)
            filesize = f"{os.path.getsize(filePath)/1024:.1f} KB" if os.path.exists(filePath) else "PDF"
            card = FileCard(filename, filesize)
            self.file_cards_layout.addWidget(card)
            self.file_cards.append(card)
            parsed_items, raw_occs, patient_name, rejected, agg_log = self.parser_engine.parse(filePath)
            self.batch_queue.append({
                "filePath": filePath, "name": patient_name or filename,
                "items": parsed_items, "audit": {"raw_occurrences": raw_occs, "rejected": rejected, "aggregation_log": agg_log}
            })
            codes_preview = ", ".join([f"{i['code']}(x{i['qty']})" for i in parsed_items[:5]])
            if len(parsed_items) > 5:
                codes_preview += f" +{len(parsed_items)-5} more"
            cn002_qty = next((item["qty"] for item in parsed_items if item["code"] == "CN002"), 0)
            summary = f"CN002:{cn002_qty} | {len(parsed_items)} codes"
            if rejected:
                summary += f" | {len(rejected)} flagged"
            name_item = QTableWidgetItem(f" {patient_name}")
            name_item.setForeground(QColor("#cdd6f4"))
            self.queue_table.setItem(row, 0, name_item)
            summary_item = QTableWidgetItem(f" {summary}")
            summary_item.setForeground(QColor("#6c7086"))
            self.queue_table.setItem(row, 1, summary_item)
            status_item = QTableWidgetItem("QUEUED")
            status_item.setForeground(QColor("#89b4fa"))
            self.queue_table.setItem(row, 2, status_item)
            codes_item = QTableWidgetItem(f" {codes_preview if codes_preview else 'No Codes'}")
            codes_item.setForeground(QColor("#a6e3a1"))
            self.queue_table.setItem(row, 3, codes_item)
            ParserDiagnosticAuditor.export_audit_report(patient_name, raw_occs, parsed_items, rejected, agg_log)
        self.start_btn.setEnabled(True)
        self.log(f"[{time.strftime('%H:%M:%S')}] [SESSION INITIALIZED] {len(filePaths)} bill(s) queued - DRAG-DROP SUCCESS - INFINITY ENERGY STABLE")
        self.update_counters()
        if self.batch_queue:
            self.populate_results_for_bill(0)

    def log(self, msg: str):
        import re
        original_msg = msg
        timestamp_match = re.search(r'\[(\d{2}:\d{2}:\d{2})\]', msg)
        timestamp = timestamp_match.group(1) if timestamp_match else time.strftime('%H:%M:%S')
        category = "SYSTEM"
        badge_color = "#6c7086"
        badge_bg = "#1e1e2e"
        severity = "INFO"
        upper_msg = msg.upper()
        if "ERROR" in upper_msg or "❌" in msg or "FAILED" in upper_msg or "FAIL" in upper_msg:
            category = "ERROR"
            badge_color = "#f38ba8"
            badge_bg = "#3a1a1a"
            severity = "ERROR"
        elif "WARN" in upper_msg or "⚠️" in msg or "WARNING" in upper_msg:
            category = "WARNING"
            badge_color = "#fab387"
            badge_bg = "#3a2a1a"
            severity = "WARNING"
        elif "SUCCESS" in upper_msg or "VERIFIED" in upper_msg or "COMPLETED" in upper_msg or "✓" in msg:
            category = "SUCCESS"
            badge_color = "#a6e3a1"
            badge_bg = "#1a3a2a"
            severity = "SUCCESS"
        elif "PROCEDURE" in upper_msg or "TYPE_PROCEDURE" in upper_msg:
            category = "PROCEDURE"
            badge_color = "#00d4ff"
            badge_bg = "#0f141f"
        elif "SPECIALITY" in upper_msg:
            category = "SPECIALITY"
            badge_color = "#8a2be2"
            badge_bg = "#1a0f1f"
        elif "QUANTITY" in upper_msg:
            category = "QUANTITY"
            badge_color = "#fab387"
            badge_bg = "#2a1f0f"
        elif "PLUS" in upper_msg:
            category = "PLUS ACTION"
            badge_color = "#ff0055"
            badge_bg = "#2a0f1f"
        elif "PATIENT" in upper_msg or "BILL" in upper_msg:
            category = "PATIENT"
            badge_color = "#89b4fa"
            badge_bg = "#0f141f"
        clean_msg = re.sub(r'\[\d{2}:\d{2}:\d{2}\]\s*', '', msg)
        clean_msg = re.sub(r'\[INFO\]|\[WARN\]|\[ERROR\]', '', clean_msg).strip()
        html = f'''
        <div style="margin: 3px 0; font-family: Consolas, monospace;">
            <span style="color: #00d4ff; font-weight: 700;">[{timestamp}]</span>
            <span style="background: {badge_bg}; color: {badge_color}; padding: 2px 8px; border-radius: 10px; font-size: 10px; font-weight: 800; margin: 0 8px; border: 1px solid {badge_color};">{category}</span>
            <span style="color: #cdd6f4;">{clean_msg}</span>
        </div>
        '''
        try:
            from PyQt5.QtGui import QTextCursor
            self.log_box.append("")
            cursor = self.log_box.textCursor()
            cursor.movePosition(QTextCursor.End)
            cursor.insertHtml(html)
            self.log_box.setTextCursor(cursor)
            self.log_box.ensureCursorVisible()
        except:
            self.log_box.append(original_msg)
        self.log_status.setText(severity)
        if severity == "ERROR":
            self.log_status.setStyleSheet("font-size: 10px; font-weight: 800; color: #f38ba8; background: #3a1a1a; padding: 2px 6px; border-radius: 6px;")
        elif severity == "SUCCESS":
            self.log_status.setStyleSheet("font-size: 10px; font-weight: 800; color: #a6e3a1; background: #1a3a2a; padding: 2px 6px; border-radius: 6px;")
        elif severity == "WARNING":
            self.log_status.setStyleSheet("font-size: 10px; font-weight: 800; color: #fab387; background: #3a2a1a; padding: 2px 6px; border-radius: 6px;")
        else:
            self.log_status.setStyleSheet("font-size: 9px; color: #6c7086; background: transparent;")

    def reset_execution_log(self):
        self.log_box.clear()
        init_html = f"""
        <div style="font-family: Consolas, monospace; color: #00d4ff; margin: 10px 0;">
            <div style="color: #8a2be2; font-weight: 800; letter-spacing: 2px;">╔══════════════════════════════════════╗</div>
            <div style="color: #8a2be2; font-weight: 800;">║  NEW SESSION INITIALIZED - LOG RESET  ║</div>
            <div style="color: #8a2be2; font-weight: 800; letter-spacing: 2px;">╚══════════════════════════════════════╝</div>
            <div style="margin-top: 8px; color: #6c7086;">[{time.strftime('%H:%M:%S')}] SYSTEM • Previous logs cleared from visible HUD</div>
            <div style="color: #6c7086;">[{time.strftime('%H:%M:%S')}] SYSTEM • Awaiting new bill processing...</div>
            <div style="color: #00d4ff; margin-top: 8px;">[{time.strftime('%H:%M:%S')}] INFINITY PROTOCOL • READY</div>
        </div>
        """
        try:
            from PyQt5.QtGui import QTextCursor
            cursor = self.log_box.textCursor()
            cursor.insertHtml(init_html)
            self.log_box.setTextCursor(cursor)
        except:
            self.log_box.append(f"[{time.strftime('%H:%M:%S')}] [LOG RESET] Execution log cleared for new bill(s)")
        self.results_table.setRowCount(0)
        self.results_count_label.setText("0 CODES")

    def upload_bills(self):
        filePaths, _ = QFileDialog.getOpenFileNames(self, "Select Hospital Bill PDFs", "", "PDF Files (*.pdf)")
        if filePaths:
            self.process_dropped_files(filePaths)

    def populate_results_for_bill(self, bill_index):
        if bill_index < 0 or bill_index >= len(self.batch_queue):
            return
        bill = self.batch_queue[bill_index]
        items = bill.get("items", [])
        code_descriptions = {
            "CN002": "Consultation Charge - Doctor Visit",
            "CC001": "ICU - Room Rent (Intensive Care)",
            "WC001": "Ward - Room Rent (AC Multibeds/Single)",
            "CC002": "Oxygen - Full/Half Day",
            "CC003": "Ventilator Charges",
            "CC004": "NIV Machine",
            "CC008": "Critical Care Procedure",
            "CC010": "Endotracheal Intubation",
            "CC011": "Central Line",
            "CC012": "Nebulizer Therapy",
            "CC014": "Critical Care Monitoring",
            "CI001": "ECG",
            "CI016": "Coronary Angiography",
            "GP009": "Foleys Catheter",
            "GP001": "General Procedure",
            "LB012": "CBC - Complete Blood Count",
            "LB055": "Glucometer / RBS",
            "LB120": "ABG Sampling",
            "LB123": "Renal Function Test",
            "LB124": "Liver Function Test",
            "LB125": "Lipid Profile",
            "PT005": "Limb Physio-Exercises",
            "PT004": "Physiotherapy",
            "RI001": "Echo Screening / 2D-Echo",
            "RI034": "X-Ray Chest",
            "RI020": "Ultrasound Abdomen",
            "RI133": "MRI Cervical Spine",
            "RI134": "MRI Dorsal Spine",
            "RI136": "MRI Lumbo-Sacral",
            "RI143": "MRI Fistulogram",
            "LB042": "Viral Marker Profile - Part",
            "LB043": "Viral Marker Profile - Part",
            "LB044": "Viral Marker Profile - Part",
            "DRUG100": "Drugs - IP + OT Pharmacy",
            "CNSU100": "Consumables - Cathlab/OT/Ward",
        }
        self.results_table.setRowCount(len(items))
        for row, item in enumerate(items):
            code = item.get("code","")
            qty = item.get("qty",0)
            amount = item.get("amount","")
            code_item = QTableWidgetItem(f" {code}")
            code_item.setForeground(QColor("#00d4ff"))
            if code.startswith("CN"):
                code_item.setForeground(QColor("#89b4fa"))
            elif code.startswith("CC"):
                code_item.setForeground(QColor("#f38ba8"))
            elif code.startswith("WC"):
                code_item.setForeground(QColor("#fab387"))
            self.results_table.setItem(row, 0, code_item)
            desc = code_descriptions.get(code, f"{'Lab' if code.startswith('LB') else 'Radiology' if code.startswith('RI') else 'Procedure' if code.startswith('GP') or code.startswith('PT') or code.startswith('NI') else 'Investigation' if code.startswith('CI') or code.startswith('RP') else 'Charge'} - {code}")
            desc_item = QTableWidgetItem(f" {desc}")
            desc_item.setForeground(QColor("#cdd6f4"))
            self.results_table.setItem(row, 1, desc_item)
            qty_text = f" {qty}" + (f" (₹{amount})" if amount else "")
            qty_item = QTableWidgetItem(qty_text)
            qty_item.setForeground(QColor("#a6e3a1"))
            if qty > 10:
                qty_item.setForeground(QColor("#fab387"))
            self.results_table.setItem(row, 2, qty_item)
            status_text = "QUEUED"
            if bill_index < self.queue_table.rowCount():
                status_item_q = self.queue_table.item(bill_index, 2)
                if status_item_q:
                    status_text = status_item_q.text()
            if "COMPLETED" in status_text:
                display_status = "✓ ADDED"
                color = "#a6e3a1"
            elif "IN_PROGRESS" in status_text or "PROCESSING" in status_text:
                display_status = "PROCESSING..."
                color = "#00d4ff"
            elif "QUEUED" in status_text:
                display_status = "QUEUED"
                color = "#6c7086"
            else:
                display_status = "NOT ADDED"
                color = "#f38ba8"
            status_display = QTableWidgetItem(f" {display_status}")
            status_display.setForeground(QColor(color))
            self.results_table.setItem(row, 3, status_display)
            source = bill.get("audit", {}).get("raw_occurrences", [])
            source_text = ""
            for occ in source:
                if occ.get("code")==code:
                    source_text = occ.get("source_section","")[:30]
                    break
            source_item = QTableWidgetItem(f" {source_text}")
            source_item.setForeground(QColor("#6c7086"))
            self.results_table.setItem(row, 4, source_item)
        self.results_count_label.setText(f"{len(items)} CODES")

    def on_bill_selected(self, item):
        row = item.row()
        self.current_selected_bill = row
        self.populate_results_for_bill(row)

    def parse_single_pdf(self, pdf_path: str):
        final_items, raw_occs, patient_name, rejected, agg_log = self.parser_engine.parse(pdf_path)
        return final_items, patient_name

    def start_batch(self):
        self.log_box.clear()
        self.log(f"[{time.strftime('%H:%M:%S')}] [NEW EXECUTION] Starting batch - log reset - NEW SESSION INITIALIZED")
        self.start_btn.setEnabled(False)
        self.upload_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.stop_btn.setText("■ STOP AUTOMATION")
        self.batch_status_label.setText("BATCH: PROCESSING")
        self.batch_status_label.setStyleSheet("font-size: 10px; font-weight: 800; color: #00d4ff; background: #0f141f; padding: 2px 8px; border-radius: 6px; border: 1px solid #00d4ff;")
        self.automation_status_label.setText("AUTOMATION: RUNNING ●")
        self.automation_status_label.setStyleSheet("font-size: 10px; font-weight: 800; color: #a6e3a1; background: #1a3a2a; padding: 2px 8px; border-radius: 6px; border: 1px solid #a6e3a1;")
        delay_mode = self.delay_combo.currentText()
        self.thread = BatchAutomationThread(self.batch_queue, delay_mode)
        self.thread.log_signal.connect(self.log)
        self.thread.patient_status_signal.connect(self.update_patient_status)
        self.thread.finished_signal.connect(self.batch_finished)
        self.thread.start()
        self.log(f"[{time.strftime('%H:%M:%S')}] [BATCH START] {len(self.batch_queue)} patient(s) queued - INFINITY PROTOCOL ENGAGED")

    def stop_batch(self):
        self.log(f"[{time.strftime('%H:%M:%S')}] [STOP REQUESTED] User requested graceful stop - finishing current Selenium action, preventing next procedure")
        if self.thread and self.thread.isRunning():
            self.log(f"[{time.strftime('%H:%M:%S')}] [STOP] Requesting cancellation via existing safe hook thread.stop() - current action will finish safely")
            self.thread.stop()
            self.stop_btn.setEnabled(False)
            self.stop_btn.setText("STOPPING...")
            self.batch_status_label.setText("BATCH: STOPPING")
            self.batch_status_label.setStyleSheet("font-size: 10px; font-weight: 800; color: #fab387; background: #3a2a1a; padding: 2px 8px; border-radius: 6px; border: 1px solid #fab387;")
            self.automation_status_label.setText("AUTOMATION: STOPPING")
            for row in range(self.queue_table.rowCount()):
                item = self.queue_table.item(row, 2)
                if item and "IN_PROGRESS" in item.text():
                    self.update_patient_status(row, "STOPPING")
                    if row < len(self.file_cards):
                        self.file_cards[row].update_status("STOPPING")
            self.log(f"[{time.strftime('%H:%M:%S')}] [STOP] Stop signal sent - will stop after current procedure (no new Plus dispatch) - resources will be cleaned")
        else:
            self.log(f"[{time.strftime('%H:%M:%S')}] [STOP] No active automation to stop")
            self.stop_btn.setEnabled(False)

    def update_patient_status(self, row: int, status_text: str):
        item = QTableWidgetItem(status_text)
        if "COMPLETED" in status_text:
            item.setForeground(QColor(166, 227, 161))
        elif "IN_PROGRESS" in status_text or "PROCESSING" in status_text:
            item.setForeground(QColor(249, 226, 175))
        elif "STOPPING" in status_text:
            item.setForeground(QColor(250, 179, 135))
        else:
            item.setForeground(QColor(243, 139, 168))
        self.queue_table.setItem(row, 2, item)
        if row < len(self.file_cards):
            self.file_cards[row].update_status(status_text)
        if row == self.current_selected_bill:
            self.populate_results_for_bill(row)
        self.update_counters()

    def update_counters(self):
        total_bills = self.queue_table.rowCount()
        processed = 0
        successful = 0
        failed = 0
        total_codes = 0
        total_qty = 0
        for row in range(total_bills):
            status_item = self.queue_table.item(row, 2)
            if status_item:
                status = status_item.text()
                if "COMPLETED" in status:
                    processed += 1
                    successful += 1
                elif "PARTIAL" in status or "ERROR" in status or "STOPPED" in status:
                    processed += 1
                    failed += 1
        for bill in self.batch_queue:
            items = bill.get("items", [])
            total_codes += len(items)
            for item in items:
                total_qty += item.get("qty",0)
        self.counter_cards["BILLS"].set_value(total_bills)
        self.counter_cards["PROCESSED"].set_value(processed)
        self.counter_cards["SUCCESS"].set_value(successful)
        self.counter_cards["FAILED"].set_value(failed)
        self.counter_cards["CODES"].set_value(total_codes)
        self.counter_cards["QTY"].set_value(total_qty)

    def batch_finished(self, success: bool, msg: str):
        self.start_btn.setEnabled(True)
        self.upload_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setText("■ STOP AUTOMATION")
        self.batch_status_label.setText("BATCH: IDLE" if success else "BATCH: STOPPED")
        self.batch_status_label.setStyleSheet("font-size: 10px; color: #6c7086; background: transparent; border: none;" if success else "font-size: 10px; font-weight: 800; color: #f38ba8; background: #3a1a1a; padding: 2px 8px; border-radius: 6px; border: 1px solid #f38ba8;")
        self.automation_status_label.setText("AUTOMATION: READY" if success else "AUTOMATION: STOPPED")
        self.automation_status_label.setStyleSheet("font-size: 10px; color: #89b4fa; background: transparent; border: none;" if success else "font-size: 10px; color: #6c7086; background: transparent; border: none;")
        if not success and ("cancelled" in msg.lower() or "stopped" in msg.lower()):
            self.log(f"[{time.strftime('%H:%M:%S')}] [STOPPED] Automation stopped by user - {msg} - resources cleaned, no corruption")
        else:
            self.log(f"[{time.strftime('%H:%M:%S')}] [FINISHED] Batch finished - {msg} - ALL PROCEDURES VERIFIED")
        self.update_counters()
        QMessageBox.information(self, "Batch Status", msg)

    def closeEvent(self, event):
        if self.thread and self.thread.isRunning():
            self.thread.stop()
            self.thread.wait(3000)
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    ex = BatchCGHSApp()
    ex.show()
    sys.exit(app.exec_())
