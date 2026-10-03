"""Deterministic in-memory CGHS portal double.

This is NOT a mock of our own code: it is a model of the *portal* that both the
baseline ``app.py`` automation (loaded from git) and the new ``cghs`` package
drive through the ordinary Selenium API.  That is what makes the before/after
performance numbers comparable and the safety tests meaningful.

It counts every browser round trip, so "fewer DOM calls" is measured, not
claimed, and it can inject the exact faults the acceptance contract requires
(stale elements, click exceptions, delayed commits, unrelated mutations,
virtualized/empty tables, near-matching option codes, overlapping locators).
"""

from __future__ import annotations

import itertools
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from selenium.common.exceptions import (
    ElementClickInterceptedException,
    StaleElementReferenceException,
    WebDriverException,
)

CONTROL = "\ue009"
BACKSPACE = "\ue003"
TAB = "\ue004"
ENTER = "\ue007"

_ids = itertools.count(1)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class Latency:
    """Portal reaction times, in seconds."""

    dropdown: float = 0.04
    speciality: float = 0.04
    commit: float = 0.05
    quantity: float = 0.0
    reason_options: float = 0.02

    @classmethod
    def fast(cls):
        return cls(0.01, 0.01, 0.01, 0.0, 0.0)

    @classmethod
    def medium(cls):
        return cls(0.06, 0.06, 0.08, 0.01, 0.02)

    @classmethod
    def slow(cls):
        return cls(0.20, 0.18, 0.25, 0.03, 0.08)

    @classmethod
    def instant(cls):
        return cls(0.0, 0.0, 0.0, 0.0, 0.0)


@dataclass
class Faults:
    """Fault injection switches (all default off)."""

    #: codes whose Plus click raises before the portal ever sees it
    click_raises_stale: set = field(default_factory=set)
    #: codes whose Plus click raises AFTER the portal accepted the mutation
    click_raises_after_dispatch: set = field(default_factory=set)
    #: codes whose commit never lands
    commit_never: set = field(default_factory=set)
    #: codes whose commit lands only after `late_commit_delay` seconds
    commit_late: set = field(default_factory=set)
    late_commit_delay: float = 0.6
    #: codes that commit with the WRONG quantity
    commit_wrong_qty: set = field(default_factory=set)
    #: codes that commit under a DIFFERENT code
    commit_wrong_code: Dict[str, str] = field(default_factory=dict)
    #: codes that trigger an unrelated row mutation instead of the target row
    unrelated_mutation: set = field(default_factory=set)
    #: make the procedure input go stale right after the dropdown renders
    stale_procedure_after_dropdown: set = field(default_factory=set)
    #: raise StaleElementReferenceException on the first N procedure send_keys
    stale_procedure_on_send_keys: int = 0
    #: raise StaleElementReferenceException on the first N quantity writes
    stale_quantity_on_set: int = 0
    #: raise StaleElementReferenceException on the first N speciality reads
    stale_speciality_reads: int = 0
    #: raise on the next N find_elements calls for this locator substring
    browser_disconnect_after: Optional[int] = None
    #: the compact probe is not supported (forces the Selenium fallback path)
    probe_unsupported: bool = False
    #: both TABLE_ROWS strategies match the same nodes (nested markup)
    overlapping_row_locators: bool = False
    #: table renders zero rows even though the page is valid (virtualized grid)
    virtualized_table: bool = False
    #: reason control is absent from the DOM
    reason_absent: bool = False
    #: reason control present but 'Others' missing
    reason_without_others: bool = False


@dataclass
class PortalConfig:
    latency: Latency = field(default_factory=Latency)
    faults: Faults = field(default_factory=Faults)
    #: which row markup the portal renders: "table" (//tbody//tr) or "grid"
    #: (//div[treatment-grid]//div[row]).  Exactly one layout exists at a time.
    table_layout: str = "table"
    locked_codes: set = field(default_factory=set)
    #: Which stages the portal CLEARS after a successful Add.  The real portal's
    #: behaviour here is not observable from this sandbox, so BOTH behaviours
    #: are modelled and the engine is required to be correct under each.
    reset_procedure_after_add: bool = False
    reset_speciality_after_add: bool = False
    reset_reason_after_add: bool = False
    speciality_for: Dict[str, str] = field(default_factory=dict)
    default_speciality: str = "GENERAL MEDICINE"
    #: extra near-matching options the dropdown should offer
    decoy_options: List[str] = field(default_factory=list)
    patient_name: str = "Mrs. LAXMI DEVI SHRIVASTAVA"
    ip_case: str = "BPLIP40343"
    bill_number: str = "BPL-ICR-28519"
    url: str = "https://portal.example.gov.in/claims/treatment-plan/40343"
    title: str = "Treatment Plan - Enhancement"
    in_iframe: bool = False
    #: index of the iframe that actually hosts the controls (None = top level)
    controls_in_frame: Optional[int] = None
    #: how many iframes the page exposes when controls_in_frame is set
    iframe_count: int = 3
    #: set False to model a tab that is not a Treatment Plan at all
    treatment_plan_present: bool = True
    extra_tabs: List[Dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Elements
# ---------------------------------------------------------------------------

class FakeElement:
    def __init__(self, portal: "FakePortal", kind: str, *, text: str = "",
                 value: str = "", tag: str = "input", attrs: Optional[Dict[str, Any]] = None,
                 cells: Optional[List[str]] = None, payload: Any = None,
                 eid: Optional[str] = None):
        self.portal = portal
        self.kind = kind
        # A STABLE id models real Selenium: the same DOM node returned through
        # two different locator strategies carries the same element id, which
        # is what SmartDOMResolver._identity() dedupes on.  Without this the
        # double reports each row once per matching strategy.
        self.id = eid if eid is not None else f"{kind}-{next(_ids)}"
        self._text = text
        self._value = value
        self.tag_name = tag
        self.attrs = attrs or {}
        self.cells = cells
        self.payload = payload
        self.stale = False
        self.displayed = True

    # -- selenium surface ------------------------------------------------
    def _check(self):
        if self.stale:
            raise StaleElementReferenceException(f"{self.id} is stale")

    @property
    def text(self):
        self._check()
        self.portal.counters["element_reads"] += 1
        return self._text

    def is_displayed(self):
        self._check()
        self.portal.counters["element_reads"] += 1
        return self.displayed

    def is_enabled(self):
        self._check()
        self.portal.counters["element_reads"] += 1
        return not self.attrs.get("disabled")

    def get_attribute(self, name):
        self._check()
        self.portal.counters["element_reads"] += 1
        if name == "value":
            return self._value
        if name == "class":
            return self.attrs.get("class", "")
        return self.attrs.get(name)

    def click(self):
        self._check()
        self.portal.counters["element_clicks"] += 1
        self.portal.handle_click(self)

    def send_keys(self, *keys):
        self._check()
        self.portal.counters["send_keys"] += 1
        self.portal.handle_send_keys(self, "".join(str(k) for k in keys))

    def clear(self):
        self._check()
        self.portal.handle_send_keys(self, BACKSPACE)

    def find_elements(self, by, value):
        self._check()
        self.portal.counters["find_elements"] += 1
        if self.cells is not None and ".//td" in str(value):
            return [FakeElement(self.portal, "cell", text=c, tag="td") for c in self.cells]
        return []

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"<FakeElement {self.kind} value={self._value!r} text={self._text!r}>"


# ---------------------------------------------------------------------------
# The portal
# ---------------------------------------------------------------------------

class FakePortal:
    """Stateful portal model + instrumented WebDriver facade."""

    def __init__(self, config: Optional[PortalConfig] = None,
                 catalogue: Optional[Dict[str, str]] = None):
        self.config = config or PortalConfig()
        self.catalogue = catalogue or {}
        self.counters: Dict[str, int] = {
            "execute_script": 0, "find_elements": 0, "element_reads": 0,
            "element_clicks": 0, "send_keys": 0, "default_content": 0,
            "frame_switches": 0, "window_handles": 0, "window_switches": 0,
            "probe_calls": 0, "legacy_table_state": 0,
        }
        # ---- portal state ----
        self.procedure_typed = ""
        self.procedure_value = ""
        self.procedure_code = ""
        self.speciality_value = ""
        self.quantity_value = "1"
        self.quantity_locked = False
        self.reason_value = ""
        self.procedure_name_value = ""
        self.amount_value = ""
        self.dropdown_options: List[str] = []
        self.reason_options: List[str] = []
        #: Treatment Plan rows are PER PATIENT, exactly like the real portal:
        #: opening another patient's plan shows that patient's rows.
        self._rows_by_patient: Dict[str, List[Dict[str, str]]] = {}
        #: controls whose markup has been re-rendered (locator cache must miss)
        self.renamed_controls: set = set()
        self.mutation_detected = False
        self.mutation_count = 0
        self.plus_clicks: List[Dict[str, Any]] = []
        self.plus_clicks_by_code: Dict[str, int] = {}
        self._events: List[tuple] = []
        self._procedure_el: Optional[FakeElement] = None
        self._find_calls = 0
        self.current_frame: Optional[int] = None
        self.handles = ["TAB-MAIN"] + [t["handle"] for t in self.config.extra_tabs]
        self.current_handle = "TAB-MAIN"
        self.stale_marks: List[FakeElement] = []

    # ================= scheduling =====================================
    def _schedule(self, delay: float, fn: Callable[[], None]):
        self._events.append((time.monotonic() + max(delay, 0.0), fn))

    def _tick(self):
        if not self._events:
            return
        now = time.monotonic()
        due = [e for e in self._events if e[0] <= now]
        if due:
            self._events = [e for e in self._events if e[0] > now]
            for _, fn in sorted(due, key=lambda e: e[0]):
                fn()

    # ================= portal behaviour ===============================
    def options_for(self, typed: str) -> List[str]:
        typed = (typed or "").strip().upper()
        if not typed:
            return []
        out = []
        for code, label in self.catalogue.items():
            if typed in code.upper() or typed in label.upper():
                out.append(label)
        out.extend(d for d in self.config.decoy_options if typed in d.upper())
        return out

    def handle_send_keys(self, el: FakeElement, keys: str):
        if el.kind == "procedure" and self.config.faults.stale_procedure_on_send_keys > 0:
            self.config.faults.stale_procedure_on_send_keys -= 1
            el.stale = True
            raise StaleElementReferenceException("procedure input went stale mid-type")
        if CONTROL in keys or keys == BACKSPACE:
            if el.kind == "procedure":
                self.procedure_typed = ""
                el._value = ""
            elif el.kind == "quantity":
                self.quantity_value = ""
                el._value = ""
            elif el.kind == "procedure_name":
                self.procedure_name_value = ""
            elif el.kind == "amount":
                self.amount_value = ""
            return
        if keys in (TAB, ENTER):
            return
        if el.kind == "procedure":
            self.procedure_typed += keys
            el._value = self.procedure_typed
            typed = self.procedure_typed
            self._schedule(self.config.latency.dropdown,
                           lambda t=typed: self._open_dropdown(t))
        elif el.kind == "quantity":
            if self.quantity_locked:
                return
            self.quantity_value += keys
            el._value = self.quantity_value
        elif el.kind == "procedure_name":
            self.procedure_name_value += keys
        elif el.kind == "amount":
            self.amount_value += keys

    def _open_dropdown(self, typed: str):
        if typed != self.procedure_typed:
            return
        self.dropdown_options = self.options_for(typed)
        code = self._code_of(typed)
        if code and code in self.config.faults.stale_procedure_after_dropdown:
            if self._procedure_el is not None:
                self._procedure_el.stale = True

    def _code_of(self, text: str) -> str:
        match = re.search(r"\b([A-Z]{1,3}\d{3,4}|DRGU100|CNSU100|DRUG100)\b",
                          (text or "").upper())
        return match.group(1) if match else ""

    def handle_click(self, el: FakeElement):
        self._tick()
        if el.kind == "option":
            self._select_option(el)
        elif el.kind == "reason_option":
            self.reason_value = el._text
            self.reason_options = []
        elif el.kind == "reason":
            if not self.config.faults.reason_without_others:
                opts = ["Others", "Rate revision", "Clinical necessity"]
            else:
                opts = ["Rate revision", "Clinical necessity"]
            self._schedule(self.config.latency.reason_options,
                           lambda o=opts: setattr(self, "reason_options", o))
        elif el.kind == "speciality_clear":
            self.speciality_value = ""
        elif el.kind == "plus":
            self._handle_plus()

    def _select_option(self, el: FakeElement):
        label = el._text
        self.procedure_value = label
        self.procedure_code = self._code_of(label)
        self.dropdown_options = []
        if self._procedure_el is not None and not self._procedure_el.stale:
            self._procedure_el._value = label
        code = self.procedure_code
        internal = self._internal_code(code)
        self.quantity_locked = internal in self.config.locked_codes
        self.quantity_value = "1"
        speciality = self.config.speciality_for.get(internal, self.config.default_speciality)
        self._schedule(self.config.latency.speciality,
                       lambda s=speciality: setattr(self, "speciality_value", s))

    def _internal_code(self, portal_code: str) -> str:
        if portal_code == "DRGU100":
            return "DRUG100"
        return portal_code

    # ---- the mutation under test --------------------------------------
    def _handle_plus(self):
        code = self._internal_code(self.procedure_code)
        faults = self.config.faults

        if code in faults.click_raises_stale:
            # The portal NEVER sees this click.
            raise StaleElementReferenceException(
                f"plus button went stale before dispatch for {code}")

        self.plus_clicks.append({"code": code, "qty": self.quantity_value,
                                 "at": time.monotonic()})
        self.plus_clicks_by_code[code] = self.plus_clicks_by_code.get(code, 0) + 1

        # Portal-side form reset after Add (configurable - see PortalConfig).
        if self.config.reset_procedure_after_add:
            self.procedure_value = ""
            self.procedure_typed = ""
            self.procedure_code = ""
        if self.config.reset_speciality_after_add:
            self.speciality_value = ""
        if self.config.reset_reason_after_add:
            self.reason_value = ""

        qty = "1" if self.quantity_locked else (self.quantity_value or "1")
        row_code = faults.commit_wrong_code.get(code, self.procedure_code or code)
        row_qty = str(int(qty) + 1) if code in faults.commit_wrong_qty else qty

        if code in faults.unrelated_mutation:
            self._schedule(self.config.latency.commit,
                           lambda: self._append_row("ZZ999", "7"))
        elif code in faults.commit_never:
            pass
        elif code in faults.commit_late:
            self._schedule(faults.late_commit_delay,
                           lambda c=row_code, q=row_qty: self._append_row(c, q))
        else:
            self._schedule(self.config.latency.commit,
                           lambda c=row_code, q=row_qty: self._append_row(c, q))

        if code in faults.click_raises_after_dispatch:
            # Transport-level failure AFTER the portal accepted the click:
            # the renderer never answered, so the caller cannot know whether
            # the mutation happened.  (An ElementClickInterceptedException
            # would be the wrong model - that one PROVES the element was not
            # clicked.)
            raise WebDriverException(
                f"timed out receiving message from renderer after dispatching {code}")

    def _append_row(self, code: str, qty: str):
        self.rows.append({"code": code, "qty": str(qty)})
        self.mutation_detected = True
        self.mutation_count += 1

    def commit_pending(self):
        """Force all scheduled portal reactions to land (test helper)."""
        for _, fn in sorted(self._events, key=lambda e: e[0]):
            fn()
        self._events = []

    # ================= WebDriver surface ==============================
    @property
    def window_handles(self):
        self.counters["window_handles"] += 1
        return list(self.handles)

    @property
    def current_window_handle(self):
        return self.current_handle

    @property
    def current_url(self):
        return self._tab_cfg().get("url", self.config.url)

    @property
    def title(self):
        return self._tab_cfg().get("title", self.config.title)

    @property
    def page_source(self):
        return "<html><body>fake portal</body></html>"

    def save_screenshot(self, path):
        return True

    def execute(self, *args, **kwargs):
        # Selenium ActionChains reaches for this; the fake portal has no
        # W3C actions endpoint, so the ActionChains fallback always fails here.
        raise WebDriverException("W3C actions are not supported by the fake portal")

    # ---- patient-scoped Treatment Plan ---------------------------------
    @property
    def rows(self) -> List[Dict[str, str]]:
        return self._rows_by_patient.setdefault(self.config.patient_name, [])

    def seed_rows(self, rows):
        """Pre-populate the Treatment Plan (models a partly-billed patient)."""
        table = self._rows_by_patient.setdefault(self.config.patient_name, [])
        for code, qty in rows:
            table.append({"code": str(code), "qty": str(qty)})

    def rename_control(self, kind: str):
        """Re-render one control so its previously cached locator stops matching."""
        self.renamed_controls.add(kind)

    def switch_patient(self, name: str, ip_case: str = "", bill_number: str = ""):
        """Model the OPERATOR opening another patient's Treatment Plan."""
        self.config.patient_name = name
        self.config.ip_case = ip_case or f"IP-{abs(hash(name)) % 100000}"
        self.config.bill_number = bill_number or f"BILL-{abs(hash(name)) % 1000}"
        self._rows_by_patient.setdefault(name, [])
        self.mutation_detected = True
        self.mutation_count += 1

    def _tab_cfg(self) -> Dict[str, Any]:
        if self.current_handle == "TAB-MAIN":
            if self.config.controls_in_frame is None:
                controls = True
            else:
                controls = self.current_frame == self.config.controls_in_frame
            return {"url": self.config.url, "title": self.config.title,
                    "treatment_plan": self.config.treatment_plan_present,
                    "controls": controls and self.config.treatment_plan_present,
                    "patient": self.config.patient_name, "ip": self.config.ip_case,
                    "bill": self.config.bill_number}
        for tab in self.config.extra_tabs:
            if tab["handle"] == self.current_handle:
                return tab
        return {}

    @property
    def switch_to(self):
        return _SwitchTo(self)

    # ---- find_elements -------------------------------------------------
    def find_elements(self, by, value):
        self.counters["find_elements"] += 1
        self._find_calls += 1
        self._tick()
        disconnect_after = self.config.faults.browser_disconnect_after
        if disconnect_after is not None and self._find_calls > disconnect_after:
            raise WebDriverException("chrome not reachable (injected)")
        v = str(value).lower()
        if "iframe" in v and str(by).lower() in ("tag name", "tag_name"):
            if self.config.controls_in_frame is None:
                return []
            if self.current_frame is not None:
                return []                      # no nested frames in this model
            return [FakeElement(self, "iframe", tag="iframe", payload=i)
                    for i in range(self.config.iframe_count)]
        if not self._tab_cfg().get("controls", False):
            return []
        return self._resolve(str(by), v)

    def _resolve(self, by: str, value: str) -> List[FakeElement]:
        v = value.lower()
        if "iframe" in v and by.lower() in ("tag name", "tag_name"):
            return []
        if "spinner" in v or "loader" in v or "ngx-overlay" in v:
            return []
        if "m9fzljqxbdjyfhzambbf" in v or "btnadd" in v or "'add'" in v or "plus" in v:
            return [self._plus_element()]
        if "treatment plan" in v:
            return [FakeElement(self, "header", text="Treatment Plan", tag="div")]
        # NOTE: PROCEDURE_INPUT strategy #2 also mentions 'procedureName', so the
        # Procedure Name control is only matched by selectors that do NOT also
        # address the code input.
        if ("procedure name" in v or "procedurename" in v) \
                and "@formcontrolname='procedure'" not in v:
            return [FakeElement(self, "procedure_name", value=self.procedure_name_value)]
        if "amount" in v:
            return [FakeElement(self, "amount", value=self.amount_value)]
        if "clear" in v or "ng-value-icon" in v or "m14.348" in v:
            return ([FakeElement(self, "speciality_clear", tag="span")]
                    if self.speciality_value else [])
        if "speciality" in v:
            el = FakeElement(self, "speciality", value=self.speciality_value)
            if self.config.faults.stale_speciality_reads > 0:
                self.config.faults.stale_speciality_reads -= 1
                el.stale = True
            return [el]
        if "reason" in v:
            if self.config.faults.reason_absent:
                return []
            return [FakeElement(self, "reason", value=self.reason_value, tag="ng-select")]
        if ("option" in v) or ("mat-option" in v) or ("ng-option" in v):
            return self._option_elements()
        if "tbody" in v or "treatment-grid" in v:
            if self.config.faults.overlapping_row_locators:
                # A DOM that nests a <table> inside a treatment-grid div: both
                # registered strategies match the SAME nodes.
                return self._row_elements(duplicate=True)
            # Faithful default: a real portal renders exactly ONE row layout,
            # so only one of the two registered strategies can match.  The
            # TABLE_ROWS strategies target structurally different markup
            # (//table//tbody//tr vs //div[treatment-grid]//div[row]).
            if ("tbody" in v) != (self.config.table_layout == "table"):
                return []
            return self._row_elements()
        if ("procedure" in v) or ("@formcontrolname='procedure'" in v):
            if "procedure" in self.renamed_controls:
                # The control was re-rendered: only the LAST registered
                # strategy still matches, so a cached strategy must miss.
                if "contains(@id" not in v:
                    return []
            return [self._procedure_element()]
        if "number" in v or "days" in v or "unit" in v or "noofdays" in v:
            return [self._quantity_element()]
        if "//input | //select" in v or "//input|//select" in v.replace(" ", ""):
            return [self._procedure_element(), self._quantity_element()]
        if v.strip() == "//input":
            return [self._procedure_element(), self._quantity_element()]
        return []

    def _procedure_element(self) -> FakeElement:
        if self._procedure_el is None or self._procedure_el.stale:
            self._procedure_el = FakeElement(
                self, "procedure", value=self.procedure_value or self.procedure_typed)
        else:
            self._procedure_el._value = self.procedure_value or self.procedure_typed
        return self._procedure_el

    def _quantity_element(self) -> FakeElement:
        attrs = {"class": "form-control"}
        if self.quantity_locked:
            attrs["disabled"] = True
            attrs["class"] = "form-control disabled"
        return FakeElement(self, "quantity", value=self.quantity_value, attrs=attrs)

    def _plus_element(self) -> FakeElement:
        return FakeElement(self, "plus", tag="img",
                           attrs={"class": "m9FzljqXbDJyFhzambbf"})

    def _option_elements(self) -> List[FakeElement]:
        texts = self.dropdown_options or self.reason_options
        kind = "option" if self.dropdown_options else "reason_option"
        return [FakeElement(self, kind, text=t, tag="div") for t in texts]

    def _row_elements(self, duplicate: bool = False) -> List[FakeElement]:
        if self.config.faults.virtualized_table:
            return []
        out = []
        for index, row in enumerate(self.rows):
            cells = ["", row["code"], "desc", row["qty"], "0.00"]
            out.append(FakeElement(self, "row", text=" ".join(cells), tag="tr",
                                   cells=cells, eid=f"row-{index}"))
        if duplicate:
            out = out + out          # the same nodes matched by a 2nd strategy
        return out

    # ---- execute_script -------------------------------------------------
    def execute_script(self, script: str, *args):
        self.counters["execute_script"] += 1
        self._tick()
        text = str(script)

        if text.startswith("/*CGHS_PROBE_V1*/"):
            self.counters["probe_calls"] += 1
            if self.config.faults.probe_unsupported:
                return None
            return self._probe(args[0] if args else {})

        if "getAllAngularTestabilities" in text:
            return True
        if "__cghsObserver" in text and "MutationObserver" in text:
            self.mutation_detected = False
            self.mutation_count = 0
            return True
        if "__cghsObserver" in text:            # disconnect
            return True
        if "__cghsMutationDetected" in text:
            return self.mutation_detected
        if "XPathResult.ORDERED_NODE_SNAPSHOT_TYPE" in text and "snapshotLength" in text:
            self.counters["legacy_table_state"] += 1
            return {"count": len(self.rows),
                    "hash": "".join(r["code"] for r in self.rows[:5]),
                    "procVal": self.procedure_value}
        if "scrollIntoView" in text:
            return True
        if "cdk-overlay-backdrop" in text:
            return True
        if ".click()" in text or "click();" in text:
            if args and isinstance(args[0], FakeElement):
                args[0]._check()
                self.counters["element_clicks"] += 1
                self.handle_click(args[0])
            return True
        if "HTMLInputElement.prototype" in text:
            el, value = args[0], str(args[1])
            if el.kind == "quantity" and self.config.faults.stale_quantity_on_set > 0:
                self.config.faults.stale_quantity_on_set -= 1
                el.stale = True
                raise StaleElementReferenceException("quantity input went stale")
            el._check()
            if el.kind == "quantity":
                if not self.quantity_locked:
                    self.quantity_value = value
                    el._value = value
            elif el.kind == "amount":
                self.amount_value = value
            elif el.kind == "procedure_name":
                self.procedure_name_value = value
            return True
        if "arguments[0].disabled" in text:
            el = args[0]
            el._check()
            return bool(el.attrs.get("disabled"))
        if "arguments[0].value" in text:
            el = args[0]
            el._check()
            return el.get_attribute("value")
        if "dispatchEvent" in text and "speciality" in text.lower():
            self.speciality_value = ""
            return True
        return None

    # ---- the compact probe ------------------------------------------------
    def _probe(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        fields = list(spec.get("fields") or [])
        token = str(spec.get("token") or "").upper()
        tab = self._tab_cfg()
        out: Dict[str, Any] = {"ok": True, "v": 1}

        def ctl(present, value="", disabled=False, readonly=False, tag="input"):
            return {"present": present, "visible": present, "value": value,
                    "disabled": disabled, "readonly": readonly, "tag": tag}

        has_controls = bool(tab.get("controls"))
        if "busy" in fields:
            out["busy"] = False
            out["angular_stable"] = True
        if "ctx" in fields:
            out["ctx"] = {"inputs": 4 if has_controls else 0,
                          "procedure": has_controls,
                          "marker": bool(tab.get("treatment_plan"))}
        if "procedure" in fields:
            out["procedure"] = ctl(has_controls, self.procedure_value or self.procedure_typed)
        if "speciality" in fields:
            out["speciality"] = ctl(has_controls, self.speciality_value)
        if "quantity" in fields:
            out["quantity"] = ctl(has_controls, self.quantity_value,
                                  disabled=self.quantity_locked)
        if "reason" in fields:
            if self.config.faults.reason_absent or not has_controls:
                out["reason"] = ctl(False)
            else:
                out["reason"] = ctl(True, self.reason_value, tag="ng-select")
        if "plus" in fields:
            out["plus"] = ctl(has_controls)
            out["plus"]["clickable"] = has_controls
        if "options" in fields:
            texts = self.dropdown_options or self.reason_options
            data = {"count": len(texts), "texts": texts[:60]}
            if token:
                idx = -1
                for i, t in enumerate(texts):
                    if token in t.upper():
                        idx = i
                        break
                data["token_index"] = idx
            out["options"] = data
        if "rows" in fields:
            # A virtualized grid hides OFF-SCREEN rows from the DOM, but the
            # compact probe reads the rendered model, so it still sees them.
            if True:
                items = [{"i": i, "code": r["code"], "qty": r["qty"]}
                         for i, r in enumerate(self.rows)]
                out["rows"] = {
                    "count": len(items),
                    "hash": "".join(f"{r['code']}:{r['qty']}|" for r in self.rows[:5]),
                    "items": items,
                }
        if "signature" in fields:
            out["signature"] = {"url": tab.get("url", ""), "title": tab.get("title", ""),
                                "treatment_plan": bool(tab.get("treatment_plan"))}
        if "patient" in fields:
            out["patient"] = {"name": tab.get("patient", ""), "ip": tab.get("ip", ""),
                              "bill": tab.get("bill", "")}
        if "mutation" in fields:
            out["mutation"] = {"detected": self.mutation_detected,
                               "count": self.mutation_count}
        return out

    # ---- reporting --------------------------------------------------------
    def dom_calls(self) -> int:
        return (self.counters["execute_script"] + self.counters["find_elements"]
                + self.counters["element_reads"])

    def stats(self) -> Dict[str, int]:
        data = dict(self.counters)
        data["dom_calls"] = self.dom_calls()
        data["rows"] = len(self.rows)
        data["plus_clicks"] = len(self.plus_clicks)
        return data


class _SwitchTo:
    def __init__(self, portal: FakePortal):
        self.portal = portal

    def default_content(self):
        self.portal.counters["default_content"] += 1
        self.portal.current_frame = None

    def frame(self, element):
        self.portal.counters["frame_switches"] += 1
        index = getattr(element, "payload", None)
        self.portal.current_frame = 0 if index is None else index

    def window(self, handle):
        self.portal.counters["window_switches"] += 1
        if handle not in self.portal.handles:
            raise WebDriverException(f"no such window: {handle}")
        self.portal.current_handle = handle


# ---------------------------------------------------------------------------
# Catalogue helpers
# ---------------------------------------------------------------------------

def default_catalogue(codes: List[str]) -> Dict[str, str]:
    """``{"LB012": "LB012 - LIPID PROFILE"}`` style portal option labels."""
    catalogue = {}
    for code in codes:
        if code == "DRUG100":
            catalogue["DRGU100"] = "drugs(DRGU100-None)"
        elif code == "CNSU100":
            catalogue["CNSU100"] = "consumables(CNSU100-None)"
        else:
            catalogue[code] = f"{code} - SERVICE {code}"
    return catalogue


def build_portal(codes: List[str], **kwargs) -> FakePortal:
    config = PortalConfig(**kwargs)
    return FakePortal(config=config, catalogue=default_catalogue(codes))
