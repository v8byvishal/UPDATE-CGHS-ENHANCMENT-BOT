"""Portal locator registry and CGHS code registry.

Static, immutable configuration shared by every automation component.
Loaded exactly once per process (module import) - never rebuilt in the hot path.

Canonical owner: this module.  Moved verbatim out of the legacy monolithic ``app.py``
at baseline commit c3ccdf32e2170891fad9b150c850053461c85a25 so that exactly one
implementation exists and so the CGHS business rules are importable (and therefore
testable) without PyQt5 / Chrome / PyMuPDF being present.

BUSINESS RULES ARE UNCHANGED.  Any behavioural change in this file is a defect.
"""

from selenium.webdriver.common.by import By

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

# ---------------------------------------------------------------------------
# Derived, immutable fast-path indexes.  Built ONCE at import time so the hot
# path never rebuilds static XPath collections (see 6.9 BATCH-LEVEL REUSE).
# ---------------------------------------------------------------------------

#: Reverse map portal option label -> internal final code (e.g. "DRGU100" -> "DRUG100").
PORTAL_VALUE_TO_CODE = {v.upper(): k for k, v in PORTAL_OPTION_MAP.items()}

#: Portal renders DRUG100 as "DRGU100" (sic - portal-side spelling).  This alias
#: table is the ONLY sanctioned fuzzy mapping; it is an exact, enumerated mapping,
#: never a substring heuristic.
PORTAL_CODE_ALIASES = {
    "DRUG100": ("DRUG100", "DRGU100"),
    "CNSU100": ("CNSU100",),
}


def portal_input_value(code):
    """Internal final code -> the exact string typed into the portal input."""
    return PORTAL_OPTION_MAP.get((code or "").upper(), code)


def portal_row_aliases(code):
    """Internal final code -> tuple of exact tokens that may appear in a portal row.

    Exact tokens only.  No prefix/substring widening is performed here; callers
    must still match on word boundaries.
    """
    up = (code or "").upper()
    return PORTAL_CODE_ALIASES.get(up, (up,))


#: Locator keys whose resolution is safe to cache for the lifetime of a verified
#: session (the control itself is re-located, only the *strategy* is cached).
CACHEABLE_LOCATOR_KEYS = frozenset(LOCATORS.keys())
