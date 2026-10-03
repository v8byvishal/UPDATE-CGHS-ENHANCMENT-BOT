"""A faithful React-Select v5 DOM double.

WHY THIS EXISTS, AND WHY IT IS NOT A DUPLICATE OF ``fake_portal``
----------------------------------------------------------------
``tests/support/fake_portal.py`` models an Angular-flavoured portal: controls
whose ``value`` property holds the selected text, and options that commit on a
plain click.  The operator's live DOM evidence proves the real CGHS portal is
built on **React-Select**, whose semantics are materially different:

  1. The combobox ``<input>`` holds only the **search text**.  Once an option is
     committed React-Select **clears the input** and renders the chosen label in
     a sibling ``singleValue`` div.  Reading ``input.value`` therefore tells you
     what was *typed*, never what was *selected*.
  2. React-Select's Option component commits on **mousedown**, not on click.  A
     JavaScript ``element.click()`` dispatches only a ``click`` event, so it
     changes the rendered DOM not at all - the React state never transitions.
  3. The menu re-renders on every keystroke, so option elements captured before
     the last render are stale.
  4. ``Enter`` commits whichever option is **focused**, tracked by
     ``aria-activedescendant``; ArrowDown/ArrowUp move that focus.

This double exists to reproduce those four behaviours exactly.  It deliberately
makes the pre-fix implementation fail, which is the point: see
``tests/test_react_select_procedure.py``.

Element ids follow the operator's observed live DOM:
    speciality  #react-select-4-input
    procedure   #react-select-5-input
    reason      #react-select-7-input
    quantity    #noofdays
    add/plus    img.m9FzljqXbDJyFhzambbf
"""

from __future__ import annotations

import itertools
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

try:  # pragma: no cover - exercised only when selenium is installed
    from selenium.common.exceptions import (
        ElementNotInteractableException,
        NoSuchElementException,
        StaleElementReferenceException,
        WebDriverException,
    )
    from selenium.webdriver.common.keys import Keys
except Exception:  # pragma: no cover
    class WebDriverException(Exception):
        pass

    class StaleElementReferenceException(WebDriverException):
        pass

    class NoSuchElementException(WebDriverException):
        pass

    class ElementNotInteractableException(WebDriverException):
        pass

    class Keys:  # type: ignore[no-redef]
        ENTER = "\ue007"
        ARROW_DOWN = "\ue015"
        ARROW_UP = "\ue013"
        BACKSPACE = "\ue003"
        CONTROL = "\ue009"
        TAB = "\ue004"
        ESCAPE = "\ue00c"


_node_ids = itertools.count(1)

#: React-Select instance numbers, straight from the operator's DOM capture.
INSTANCE = {"speciality": 4, "procedure": 5, "reason": 7}


# ---------------------------------------------------------------------------
# catalogue
# ---------------------------------------------------------------------------

@dataclass
class Option:
    """One entry in a React-Select menu."""
    code: str
    label: str
    speciality: str = ""

    @property
    def text(self) -> str:
        return self.label


def procedure_catalogue() -> List[Option]:
    """Codes that exercise the exact-match rule (section 3 / test C).

    GP001 must never lose to GP001A, GP0010 or XGP001.
    """
    return [
        Option("GP001", "GP001 - General Physician Consultation", "Consultation"),
        Option("GP001A", "GP001A - General Physician Follow Up", "Consultation"),
        Option("GP0010", "GP0010 - General Physician Night Visit", "Consultation"),
        Option("XGP001", "XGP001 - External GP Referral", "Consultation"),
        Option("CN002", "CN002 - Consultation Specialist", "Consultation"),
        Option("LB001", "LB001 - Complete Blood Count", "Laboratory"),
        Option("RI001", "RI001 - X-Ray Chest PA", "Radiology"),
        Option("BL001", "BL001 - Blood Transfusion", "Blood"),
    ]


@dataclass
class ReactSelectConfig:
    """Behavioural switches, each modelling a real portal condition."""

    #: seconds before the menu renders after a keystroke (test D / E)
    listbox_delay: float = 0.0
    #: Enter does not commit; only a real option mousedown does (test I)
    enter_rejected: bool = False
    #: re-render the combobox input once, invalidating held references (test F)
    stale_after_type: bool = False
    #: procedure selection does NOT auto-populate speciality (test K)
    speciality_auto: bool = True
    #: auto-populate speciality with this wrong value (test L)
    speciality_wrong_value: str = ""
    #: quantity control is read-only (locked) - section 8
    quantity_locked: bool = True
    #: reason option label offered by the portal
    reason_labels: Tuple[str, ...] = ("Others", "Death", "Discharge")
    #: Plus only becomes visible once every required stage is satisfied
    plus_requires_all_stages: bool = True
    #: codes absent from the catalogue entirely (test G)
    missing_codes: Tuple[str, ...] = ()
    #: the menu re-renders once after options are first read, modelling
    #: debounced/async option loading.  Any option reference captured before
    #: that render is stale - the live condition behind the JS-click fallback.
    menu_rerender_after_read: bool = False
    #: After this many Plus dispatches React REMOUNTS the procedure control:
    #: the old #react-select-5-input stays in the document but hidden (a
    #: "hidden clone"), and a NEW visible input with a DIFFERENT id is
    #: mounted in the same container.  This is the live condition behind the
    #: unit-19 `element not interactable` cascade.  0 disables it.
    procedure_remount_after_units: int = 0
    #: instance number the remounted procedure input is given
    procedure_remount_instance: int = 9
    #: True  -> the remount leaves a visible, interactable replacement
    #: False -> the control is simply gone/hidden and never comes back,
    #:          which must become one shared-context failure, not 25.
    procedure_remount_recovers: bool = True
    #: a hidden duplicate procedure input present from the very start
    hidden_duplicate_procedure_input: bool = False
    #: Stages the portal CLEARS after every Add.  The live CGHS portal clears
    #: the procedure (which is why the engine re-selects it for every locked
    #: unit); without this the double never re-touches the input and cannot
    #: reproduce a failure that only appears on unit 19.
    resets_after_add: Tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# element
# ---------------------------------------------------------------------------

class RSElement:
    """A DOM node.  Stale references raise, exactly like Selenium."""

    def __init__(self, dom: "ReactSelectDOM", node: Dict[str, Any]):
        self.dom = dom
        self._node = node
        self.id = node["nid"]

    # -- staleness ------------------------------------------------------
    def _live(self) -> Dict[str, Any]:
        node = self.dom.nodes.get(self.id)
        if node is None or node.get("detached"):
            raise StaleElementReferenceException(
                f"node {self.id} is detached from the document")
        return node

    @property
    def tag_name(self) -> str:
        return self._live().get("tag", "div")

    @property
    def text(self) -> str:
        self.dom.counters["element_reads"] += 1
        return str(self._live().get("text", ""))

    def is_displayed(self) -> bool:
        self.dom.counters["element_reads"] += 1
        return bool(self._live().get("visible", True))

    def is_enabled(self) -> bool:
        self.dom.counters["element_reads"] += 1
        return not self._live().get("disabled", False)

    def get_attribute(self, name: str):
        self.dom.counters["element_reads"] += 1
        node = self._live()
        if name == "value":
            return str(node.get("value", ""))
        if name in ("innerText", "textContent"):
            return str(node.get("text", ""))
        if name == "class":
            return str(node.get("class", ""))
        return node.get("attrs", {}).get(name)

    # -- interaction ----------------------------------------------------
    def click(self):
        """A REAL browser click: mousedown -> mouseup -> click.

        React-Select commits on mousedown, so this works.  Contrast with
        ``execute_script("arguments[0].click()")`` which fires only ``click``.
        """
        self.dom.counters["clicks"] += 1
        node = self._live()
        if not node.get("visible", True):
            raise ElementNotInteractableException(
                f"node {self.id} is not visible")
        self.dom.dispatch_mousedown(node)
        self.dom.dispatch_click(node)

    def send_keys(self, *keys):
        self.dom.counters["send_keys"] += 1
        node = self._live()
        # Real Selenium refuses to type into a control the user could not
        # type into.  THIS is the exception the operator saw at unit 19.
        if not node.get("visible", True):
            raise ElementNotInteractableException(
                f"element not interactable: node {self.id} is not displayed")
        if node.get("disabled", False):
            raise ElementNotInteractableException(
                f"element not interactable: node {self.id} is disabled")
        rect = node.get("rect", (1, 1))
        if rect[0] <= 0 or rect[1] <= 0:
            raise ElementNotInteractableException(
                f"element not interactable: node {self.id} has a zero-area rect")
        self.dom.handle_keys(node, "".join(str(k) for k in keys))

    def clear(self):
        node = self._live()
        node["value"] = ""

    def find_elements(self, by, value):
        return self.dom.find_elements(by, value, root=self._live())

    def __repr__(self):  # pragma: no cover
        n = self.dom.nodes.get(self.id, {})
        return f"<RSElement {n.get('tag')} #{n.get('attrs',{}).get('id','')}>"


# ---------------------------------------------------------------------------
# the DOM
# ---------------------------------------------------------------------------

class ReactSelectDOM:
    """A React-Select-accurate Treatment Plan page."""

    def __init__(self, config: Optional[ReactSelectConfig] = None,
                 catalogue: Optional[List[Option]] = None):
        self.config = config or ReactSelectConfig()
        self.catalogue = catalogue if catalogue is not None else procedure_catalogue()
        self.nodes: Dict[int, Dict[str, Any]] = {}
        self.counters: Dict[str, int] = {
            "find_elements": 0, "execute_script": 0, "element_reads": 0,
            "clicks": 0, "send_keys": 0, "js_clicks": 0, "mousedown": 0,
        }
        #: committed React state, the ONLY source of truth for "selected"
        self.selected: Dict[str, Optional[Option]] = {
            "procedure": None, "speciality": None, "reason": None}
        self.quantity_value = ""
        self.rows: List[Dict[str, str]] = []
        self.plus_clicks = 0
        self.plus_clicks_by_code: Dict[str, int] = {}
        self.stage_events: List[str] = []
        self._stale_budget = 1 if self.config.stale_after_type else 0
        self._build()

    # -- construction ---------------------------------------------------
    def _mk(self, tag, *, text="", value="", cls="", attrs=None,
            visible=True, parent=None) -> Dict[str, Any]:
        node = {
            "nid": next(_node_ids), "tag": tag, "text": text, "value": value,
            "class": cls, "attrs": dict(attrs or {}), "visible": visible,
            "children": [], "parent": parent, "detached": False,
        }
        self.nodes[node["nid"]] = node
        if parent is not None:
            parent["children"].append(node)
        return node

    def _build(self):
        self.root = self._mk("body")
        self.header = self._mk("h2", text="Treatment Plan", parent=self.root,
                               cls="plan-header")
        self.containers: Dict[str, Dict[str, Any]] = {}
        for field_name, inst in INSTANCE.items():
            self.containers[field_name] = self._build_select(field_name, inst)
        # quantity - operator evidence: #noofdays, type=number, pattern
        self.quantity = self._mk(
            "input", parent=self.root, cls="form-control",
            attrs={"id": "noofdays", "type": "number", "placeholder": "Type here",
                   "pattern": "[0-9]{0,3}"})
        if self.config.quantity_locked:
            self.quantity["attrs"]["readonly"] = "true"
        self._procedure_remounted = False
        if self.config.hidden_duplicate_procedure_input:
            # a detached-looking leftover React clone, hidden, matching the
            # same selectors and appearing EARLIER in document order
            proc_parts = self.containers["procedure"]["_parts"]
            live = proc_parts["input"]
            wrap = live["parent"]
            clone = self._mk(
                "input", parent=wrap, visible=False,
                attrs={"id": "react-select-5-input", "role": "combobox",
                       "aria-controls": "react-select-5-listbox",
                       "aria-owns": "react-select-5-listbox", "type": "text"})
            clone["rect"] = (0, 0)
            wrap["children"].remove(clone)
            wrap["children"].insert(0, clone)    # FIRST in document order
            self.hidden_clone = clone
        # add / plus - operator evidence: img.m9FzljqXbDJyFhzambbf
        self.plus = self._mk("img", parent=self.root, cls="m9FzljqXbDJyFhzambbf",
                             attrs={"id": "addbtn", "alt": "add"},
                             visible=not self.config.plus_requires_all_stages)
        self.table = self._mk("table", parent=self.root, cls="table")
        self.tbody = self._mk("tbody", parent=self.table)

    #: visible form labels, as rendered by the live portal
    LABELS = {"procedure": "Procedure", "speciality": "Speciality",
              "reason": "Enhancement Reason"}

    def _build_select(self, field_name: str, inst: int) -> Dict[str, Any]:
        self._mk("label", parent=self.root, cls="form-label",
                 text=self.LABELS.get(field_name, field_name.title()),
                 attrs={"data-for": field_name})
        container = self._mk("div", parent=self.root,
                             cls=f"css-b62m3t-container {field_name}-select",
                             attrs={"data-field": field_name})
        control = self._mk("div", parent=container, cls="css-13cymwt-control")
        value_container = self._mk("div", parent=control,
                                   cls="css-hlgwow-valueContainer")
        placeholder = self._mk("div", parent=value_container,
                               cls="css-1jqq78o-placeholder", text="Select...")
        input_wrap = self._mk("div", parent=value_container, cls="css-qbdosj-Input")
        inp = self._mk("input", parent=input_wrap,
                       attrs={"id": f"react-select-{inst}-input",
                              "role": "combobox",
                              "aria-autocomplete": "list",
                              "aria-expanded": "false",
                              "aria-controls": f"react-select-{inst}-listbox",
                              "aria-owns": f"react-select-{inst}-listbox",
                              "autocomplete": "off",
                              "type": "text"})
        container["_parts"] = {
            "field": field_name, "instance": inst, "control": control,
            "value_container": value_container, "placeholder": placeholder,
            "input": inp, "single_value": None, "listbox": None,
            "focused_index": 0, "open": False, "open_at": 0.0,
        }
        return container

    # -- helpers --------------------------------------------------------
    def _parts(self, field_name: str) -> Dict[str, Any]:
        return self.containers[field_name]["_parts"]

    def _field_of(self, node: Dict[str, Any]) -> Optional[str]:
        cur = node
        while cur is not None:
            if "_parts" in cur:
                return cur["_parts"]["field"]
            cur = cur.get("parent")
        return None

    def _detach(self, node: Optional[Dict[str, Any]]):
        if node is None:
            return
        node["detached"] = True
        for child in node.get("children", []):
            self._detach(child)
        parent = node.get("parent")
        if parent and node in parent.get("children", []):
            parent["children"].remove(node)

    # -- option filtering ----------------------------------------------
    def options_for(self, field_name: str, typed: str) -> List[Option]:
        typed = (typed or "").strip().upper()
        if field_name == "procedure":
            pool = [o for o in self.catalogue
                    if o.code.upper() not in
                    {c.upper() for c in self.config.missing_codes}]
            if not typed:
                return pool
            return [o for o in pool if typed in o.label.upper()]
        if field_name == "reason":
            return [Option(lbl, lbl) for lbl in self.config.reason_labels
                    if not typed or typed in lbl.upper()]
        if field_name == "speciality":
            specs = []
            seen = set()
            for o in self.catalogue:
                if o.speciality and o.speciality not in seen:
                    seen.add(o.speciality)
                    specs.append(Option(o.speciality, o.speciality))
            if not typed:
                return specs
            return [s for s in specs if typed in s.label.upper()]
        return []

    # -- menu rendering -------------------------------------------------
    def _render_menu(self, field_name: str):
        """Re-render the listbox.  Every keystroke detaches the old nodes -
        which is precisely what makes held option references stale."""
        parts = self._parts(field_name)
        container = self.containers[field_name]
        self._detach(parts["listbox"])
        parts["listbox"] = None

        typed = parts["input"]["value"]
        opts = self.options_for(field_name, typed)
        if not parts["open"]:
            parts["input"]["attrs"]["aria-expanded"] = "false"
            parts["input"]["attrs"].pop("aria-activedescendant", None)
            return
        parts["input"]["attrs"]["aria-expanded"] = "true"

        inst = parts["instance"]
        listbox = self._mk("div", parent=container, cls="css-1nmdiq5-menu",
                           attrs={"id": f"react-select-{inst}-listbox",
                                  "role": "listbox"})
        parts["listbox"] = listbox
        parts["focused_index"] = min(parts["focused_index"], max(0, len(opts) - 1))
        for i, opt in enumerate(opts):
            focused = (i == parts["focused_index"])
            cls = "css-d7l1ni-option" if focused else "css-10wo9uf-option"
            self._mk("div", parent=listbox, text=opt.label, cls=cls,
                     attrs={"id": f"react-select-{inst}-option-{i}",
                            "role": "option", "tabindex": "-1",
                            "data-code": opt.code,
                            "aria-selected": "true" if focused else "false"})
        if opts:
            parts["input"]["attrs"]["aria-activedescendant"] = \
                f"react-select-{inst}-option-{parts['focused_index']}"
        else:
            parts["input"]["attrs"].pop("aria-activedescendant", None)
            self._mk("div", parent=listbox, text="No options",
                     cls="css-1h4vm2y-noOptionsMessage")

    def _menu_visible(self, field_name: str) -> bool:
        parts = self._parts(field_name)
        if not parts["open"] or parts["listbox"] is None:
            return False
        return (time.perf_counter() - parts["open_at"]) >= self.config.listbox_delay

    # -- commit ---------------------------------------------------------
    def commit(self, field_name: str, opt: Option):
        """The real React state transition."""
        parts = self._parts(field_name)
        self.selected[field_name] = opt
        self.stage_events.append(f"{field_name}:selected:{opt.code}")

        # React-Select clears the search text and renders a singleValue div.
        parts["input"]["value"] = ""
        self._detach(parts["single_value"])
        parts["placeholder"]["visible"] = False
        parts["single_value"] = self._mk(
            "div", parent=parts["value_container"],
            cls="css-1dimb5e-singleValue", text=opt.label)
        parts["open"] = False
        self._render_menu(field_name)

        if field_name == "procedure":
            self._on_procedure_committed(opt)
        self._refresh_plus()

    def _on_procedure_committed(self, opt: Option):
        """The portal derives speciality from the procedure (section 5/12)."""
        target = None
        if self.config.speciality_wrong_value:
            target = Option(self.config.speciality_wrong_value,
                            self.config.speciality_wrong_value)
        elif self.config.speciality_auto and opt.speciality:
            target = Option(opt.speciality, opt.speciality)
        sp = self._parts("speciality")
        self._detach(sp["single_value"])
        sp["single_value"] = None
        sp["placeholder"]["visible"] = True
        self.selected["speciality"] = None
        if target is not None:
            self.selected["speciality"] = target
            sp["placeholder"]["visible"] = False
            sp["single_value"] = self._mk(
                "div", parent=sp["value_container"],
                cls="css-1dimb5e-singleValue", text=target.label)
            self.stage_events.append(f"speciality:auto:{target.code}")

    def _refresh_plus(self):
        if not self.config.plus_requires_all_stages:
            self.plus["visible"] = True
            return
        ready = (self.selected["procedure"] is not None
                 and self.selected["speciality"] is not None
                 and self.selected["reason"] is not None)
        self.plus["visible"] = bool(ready)

    # -- events ---------------------------------------------------------
    def dispatch_mousedown(self, node: Dict[str, Any]):
        """React-Select's Option listens here."""
        self.counters["mousedown"] += 1
        if node.get("attrs", {}).get("role") == "option":
            field_name = self._field_of(node)
            if field_name and self._menu_visible(field_name):
                code = node["attrs"].get("data-code")
                for opt in self.options_for(field_name,
                                            self._parts(field_name)["input"]["value"]):
                    if opt.code == code:
                        self.commit(field_name, opt)
                        return

    def dispatch_click(self, node: Dict[str, Any]):
        """A plain click event.

        React-Select's Option ignores this entirely (it listens on mousedown),
        but an ordinary React ``onClick`` handler - which is what the Add
        control is - responds to it normally.
        """
        if node is self.plus:
            self._click_plus()
            return
        field_name = self._field_of(node)
        if field_name and node is self._parts(field_name)["input"]:
            parts = self._parts(field_name)
            parts["open"] = True
            parts["open_at"] = time.perf_counter()
            parts["focused_index"] = 0
            self._render_menu(field_name)

    def js_click(self, node: Dict[str, Any]):
        """``element.click()`` from JavaScript: fires ONLY a click event.

        This is the trap.  React-Select never sees a mousedown, so no option is
        ever committed - yet the typed text remains in the input, so any check
        that reads ``input.value`` is fooled into reporting success.
        """
        self.counters["js_clicks"] += 1
        self.dispatch_click(node)

    def handle_keys(self, node: Dict[str, Any], keys: str):
        field_name = self._field_of(node)
        if field_name is None or node is not self._parts(field_name)["input"]:
            if node is self.quantity:
                self._type_quantity(keys)
            return
        parts = self._parts(field_name)
        i = 0
        while i < len(keys):
            ch = keys[i]
            if ch == Keys.CONTROL and i + 1 < len(keys) and keys[i + 1] == "a":
                parts["_select_all"] = True
                i += 2
                continue
            if ch == Keys.BACKSPACE:
                if parts.pop("_select_all", False):
                    parts["input"]["value"] = ""
                else:
                    parts["input"]["value"] = parts["input"]["value"][:-1]
                parts["open"] = True
                parts["open_at"] = time.perf_counter()
                parts["focused_index"] = 0
                self._render_menu(field_name)
                i += 1
                continue
            if ch == Keys.ARROW_DOWN or ch == Keys.ARROW_UP:
                opts = self.options_for(field_name, parts["input"]["value"])
                if opts:
                    step = 1 if ch == Keys.ARROW_DOWN else -1
                    parts["focused_index"] = (parts["focused_index"] + step) % len(opts)
                self._render_menu(field_name)
                i += 1
                continue
            if ch == Keys.ENTER:
                self._handle_enter(field_name)
                i += 1
                continue
            if ch == Keys.ESCAPE:
                parts["open"] = False
                self._render_menu(field_name)
                i += 1
                continue
            if ch == Keys.TAB:
                i += 1
                continue
            # a printable character
            if parts.pop("_select_all", False):
                parts["input"]["value"] = ""
            parts["input"]["value"] += ch
            parts["open"] = True
            parts["open_at"] = time.perf_counter()
            parts["focused_index"] = 0
            self._maybe_go_stale(field_name)
            self._render_menu(field_name)
            i += 1

    def _maybe_go_stale(self, field_name: str):
        """Re-render the combobox input itself, invalidating held refs."""
        if self._stale_budget <= 0 or field_name != "procedure":
            return
        self._stale_budget -= 1
        parts = self._parts(field_name)
        old = parts["input"]
        wrap = old["parent"]
        fresh = self._mk("input", parent=wrap, value=old["value"],
                         attrs=dict(old["attrs"]))
        self._detach(old)
        parts["input"] = fresh

    def _handle_enter(self, field_name: str):
        parts = self._parts(field_name)
        if self.config.enter_rejected and field_name == "procedure":
            self.stage_events.append("procedure:enter-rejected")
            return
        if not self._menu_visible(field_name):
            return
        opts = self.options_for(field_name, parts["input"]["value"])
        if not opts:
            return
        idx = min(parts["focused_index"], len(opts) - 1)
        self.commit(field_name, opts[idx])

    def _type_quantity(self, keys: str):
        if self.quantity["attrs"].get("readonly"):
            return                      # the portal silently refuses
        for ch in keys:
            if ch == Keys.BACKSPACE:
                self.quantity["value"] = self.quantity["value"][:-1]
            elif ch in (Keys.CONTROL, "a"):
                continue
            elif ch.isdigit():
                self.quantity["value"] += ch
        self.quantity_value = self.quantity["value"]

    def _click_plus(self):
        if not self.plus.get("visible", False):
            raise ElementNotInteractableException("Add control is not visible")
        proc = self.selected["procedure"]
        if proc is None:
            raise WebDriverException("Add pressed with no procedure selected")
        self.plus_clicks += 1
        self.plus_clicks_by_code[proc.code] = \
            self.plus_clicks_by_code.get(proc.code, 0) + 1
        self.rows.append({"code": proc.code, "qty": "1",
                          "speciality": (self.selected["speciality"].code
                                         if self.selected["speciality"] else "")})
        self._render_rows()
        for field_name in self.config.resets_after_add:
            self._clear_field(field_name)
        self._maybe_remount_procedure()

    def _clear_field(self, field_name: str):
        """The portal drops a committed value after Add."""
        parts = self._parts(field_name)
        self.selected[field_name] = None
        if parts.get("single_value") is not None:
            self._detach(parts["single_value"])
            parts["single_value"] = None
        parts["placeholder"]["visible"] = True
        if field_name == "procedure":
            self.plus["visible"] = False
        self.stage_events.append(f"{field_name}:cleared-after-add")

    # -- the live unit-19 condition -------------------------------------
    def _maybe_remount_procedure(self):
        """React remounts the procedure control after N Adds.

        Models what the operator hit at GP001 unit 19.  The OLD input is NOT
        removed from the document - React leaves it mounted but hidden - so
        every ``#react-select-5-input`` selector still matches it.  A new,
        genuinely interactable input is mounted alongside it with a DIFFERENT
        id.  A resolver that returns the first match, or that trusts a cached
        strategy, will keep handing back the hidden clone forever.
        """
        n = self.config.procedure_remount_after_units
        if not n or self.plus_clicks != n or self._procedure_remounted:
            return
        self._procedure_remounted = True
        parts = self._parts("procedure")
        old_input = parts["input"]
        # hide, do NOT detach: this is the hidden clone
        old_input["visible"] = False
        old_input["rect"] = (0, 0)
        self.stage_events.append("procedure:remount")
        if not self.config.procedure_remount_recovers:
            parts["input"] = old_input       # nothing interactable remains
            return
        inst = self.config.procedure_remount_instance
        new_input = self._mk(
            "input", parent=old_input["parent"],
            attrs={"id": f"react-select-{inst}-input",
                   "role": "combobox",
                   "aria-autocomplete": "list",
                   "aria-expanded": "false",
                   "aria-controls": f"react-select-{inst}-listbox",
                   "aria-owns": f"react-select-{inst}-listbox",
                   "autocomplete": "off",
                   "type": "text"})
        parts["input"] = new_input
        parts["instance"] = inst

    def _render_rows(self):
        for child in list(self.tbody["children"]):
            self._detach(child)
        for i, row in enumerate(self.rows):
            tr = self._mk("tr", parent=self.tbody, attrs={"id": f"row-{i}"},
                          text=f" {row['code']} desc {row['qty']} 0.00")
            for cell in ("", row["code"], "desc", row["qty"], "0.00"):
                self._mk("td", parent=tr, text=cell)

    # -- Selenium driver surface ----------------------------------------
    def find_elements(self, by, value, root=None):
        self.counters["find_elements"] += 1
        value = str(value)
        matches: List[Dict[str, Any]] = []

        def walk(node):
            if node.get("detached"):
                return
            matches.append(node)
            for ch in list(node.get("children", [])):
                walk(ch)

        walk(root or self.root)
        by_s = str(by).lower()
        if "css" in by_s:
            out = [n for n in matches if self._css_match(n, value)]
        else:
            out = [n for n in matches if self._xpath_match(n, value)]
        out = [n for n in out if self._in_document(n)]
        return [RSElement(self, n) for n in out]

    def _in_document(self, node) -> bool:
        """Is the node IN THE DOCUMENT - not: is it visible.

        Selenium's find_elements returns hidden elements; it is the caller's
        job to check is_displayed().  Filtering hidden nodes here would hide
        exactly the defect under test (a hidden React clone that every
        selector still matches).

        React-Select genuinely UNMOUNTS its options when the menu closes, so
        those really are absent from the document.
        """
        field_name = self._field_of(node)
        if field_name and node.get("attrs", {}).get("role") == "option":
            return self._menu_visible(field_name)
        return True

    def _css_match(self, node, sel: str) -> bool:
        sel = sel.strip()
        m = re.match(r"^(\w+)?#([\w-]+)$", sel)
        if m:
            tag, nid = m.groups()
            return (node["attrs"].get("id") == nid
                    and (not tag or node["tag"] == tag))
        m = re.match(r"^(\w+)?\.([\w-]+)$", sel)
        if m:
            tag, cls = m.groups()
            return (cls in str(node.get("class", "")).split()
                    and (not tag or node["tag"] == tag))
        m = re.match(r'^(\w+)?\[([\w-]+)=["\']?([^"\'\]]+)["\']?\]$', sel)
        if m:
            tag, attr, val = m.groups()
            return (node["attrs"].get(attr) == val
                    and (not tag or node["tag"] == tag))
        return False

    def _following_input_after_label(self, xp: str):
        """Resolve //label[contains(...,'x')]/following::input[1] faithfully."""
        m = re.search(r"translate\(\s*\.\s*,\s*'([^']+)'\s*,\s*'([^']+)'\s*\)\s*,\s*'([^']+)'", xp)
        if not m or "following::" not in xp:
            return None
        needle = m.group(3).lower()
        order = []

        def walk(node):
            if node.get("detached"):
                return
            order.append(node)
            for ch in list(node.get("children", [])):
                walk(ch)

        walk(self.root)
        want_tag = "input"
        mm = re.search(r"following::(\w+)\[1\]", xp)
        if mm:
            want_tag = mm.group(1)
        for i, node in enumerate(order):
            if node["tag"] != "label":
                continue
            if needle not in str(node.get("text", "")).lower():
                continue
            for cand in order[i + 1:]:
                if cand["tag"] == want_tag and not cand.get("detached"):
                    return cand
        return None

    def _label_container_input_ids(self, xp: str):
        m = re.search(r"translate\(\s*\.\s*,\s*'([^']+)'\s*,\s*'([^']+)'\s*\)\s*,\s*'([^']+)'", xp)
        if not m:
            return set()
        needle = m.group(3).lower()
        order = []

        def walk(node):
            if node.get("detached"):
                return
            order.append(node)
            for ch in list(node.get("children", [])):
                walk(ch)

        walk(self.root)
        for i, node in enumerate(order):
            if node["tag"] != "label":
                continue
            if needle not in str(node.get("text", "")).lower():
                continue
            for cand in order[i + 1:]:
                if cand["tag"] == "div" and "-container" in str(cand.get("class", "")):
                    ids = set()

                    def collect(n):
                        if n.get("detached"):
                            return
                        if n["tag"] == "input":
                            ids.add(n["nid"])
                        for ch in list(n.get("children", [])):
                            collect(ch)

                    collect(cand)
                    return ids
        return set()

    def _xpath_match(self, node, xp: str) -> bool:
        """Good enough for the locator shapes this project actually uses."""
        low_all = xp.lower()
        if "//tbody//tr" in low_all:
            # structural: only a <tr> is a row.  Checking @class first would
            # let the <table class="table"> itself match.
            return node["tag"] == "tr"
        if "treatment-grid" in low_all:
            return False
        if "-container" in xp and "following::div" in xp and "translate(" in xp:
            # //label[...'procedure']/following::div[contains(@class,'-container')][1]//input
            # -> EVERY input inside that control's own container, so the
            #    caller can pick the interactable one.  Scoped to [1] so it
            #    can never reach the speciality or reason control.
            return node["nid"] in self._label_container_input_ids(xp)
        if "following::" in xp and "translate(" in xp:
            target = self._following_input_after_label(xp)
            return target is not None and target["nid"] == node["nid"]
        low = xp.lower()
        attrs = node["attrs"]
        nid = str(attrs.get("id", ""))
        cls = str(node.get("class", ""))
        role = str(attrs.get("role", ""))

        for want in re.findall(r"@id,\s*'([^']+)'", xp):
            if want.lower() in nid.lower():
                return True
        for want in re.findall(r"@id='([^']+)'", xp):
            if want == nid:
                return True
        for want in re.findall(r"@class,\s*'([^']+)'", xp):
            if want.lower() in cls.lower():
                return True
        for want in re.findall(r"@role,\s*'([^']+)'", xp):
            if want.lower() in role.lower():
                return True
        for want in re.findall(r"@role='([^']+)'", xp):
            if want == role:
                return True
        if "//input | //select" in xp or "//input|//select" in xp.replace(" ", ""):
            return node["tag"] in ("input", "select")
        if xp.strip() in (".//td", ".//TD"):
            return node["tag"] == "td"
        if "//tbody//tr" in low and node["tag"] == "tr":
            return True
        if "treatment-grid" in low:
            return False
        if "mat-option" in low or "ng-option" in low or "ng-dropdown-panel" in low:
            return False
        if "@formcontrolname" in low:
            return False
        if "plan-header" in low or "treatment plan" in low:
            return "plan-header" in cls
        if low.startswith("//input[@type='number']") and node["tag"] == "input":
            return attrs.get("type") == "number"
        return False

    # -- execute_script --------------------------------------------------
    def execute_script(self, script: str, *args):
        self.counters["execute_script"] += 1
        if "CGHS_PROBE_V1" in script:
            return self._probe(args[0] if args else {})
        if "arguments[0].click()" in script:
            el = args[0]
            self.js_click(el._live() if isinstance(el, RSElement) else el)
            return True
        if "scrollIntoView" in script:
            return True
        if "singleValue" in script:
            # mirrors _Controller._committed_value: walk up to the container
            el = args[0]
            node = el._live() if isinstance(el, RSElement) else el
            cur = node
            for _ in range(6):
                if cur is None:
                    break
                field_name = self._field_of(cur)
                if field_name:
                    sv = self._parts(field_name).get("single_value")
                    if sv is not None and not sv.get("detached"):
                        return str(sv.get("text", "")).strip()
                    return ""
                cur = cur.get("parent")
            return ""
        if script.strip().startswith("return document.readyState"):
            return "complete"
        return None

    def _probe(self, spec):
        from cghs.dom import ProbeUnsupported  # noqa: F401  (import kept local)
        fields = (spec or {}).get("fields", [])
        out: Dict[str, Any] = {"ok": True, "v": 1}

        def ctl_for(field_name):
            """Mirror PROBE_JS.valueOf() EXACTLY against the element the
            registry actually resolves - which is the combobox <input>.

            This is the heart of the live defect.  React-Select keeps only the
            search text in that input and clears it on commit, so:
              * typed-but-not-selected -> value == "GP001"  (looks selected!)
              * genuinely selected      -> value == ""      (looks unselected!)
            A faithful double must reproduce that inversion, not paper over it.
            """
            parts = self._parts(field_name)
            inp = parts["input"]
            if inp.get("detached"):
                return {"present": False, "visible": False, "value": "",
                        "disabled": False, "readonly": False, "tag": ""}
            value = str(inp.get("value", ""))
            if not value:
                value = str(inp.get("text", "")).strip()
            sv = parts.get("single_value")
            committed = ""
            if sv is not None and not sv.get("detached"):
                committed = str(sv.get("text", "")).strip()
            return {"present": True, "visible": True, "value": value,
                    "selected": committed, "combobox": True,
                    "disabled": False, "readonly": False, "tag": "input"}

        if "signature" in fields:
            out["signature"] = {"url": self.current_url, "title": self.title,
                                "treatment_plan": True}
        if "patient" in fields:
            out["patient"] = {"name": "TEST PATIENT", "ip": "IP-1", "bill": "B-1"}
        if "ctx" in fields:
            inputs = sum(1 for n in self.nodes.values()
                         if not n.get("detached") and n["tag"] in ("input", "select"))
            out["ctx"] = {
                "inputs": inputs,
                # mirror PROBE_JS: each signature is "is there a VISIBLE
                # control of this kind", recomputed live so a remounted /
                # hidden procedure control is reported honestly.
                "procedure": any(
                    e.is_displayed() for e in
                    self.find_elements("css selector", "#react-select-5-input")
                    + self.find_elements("css selector",
                                         "#react-select-%d-input"
                                         % self.config.procedure_remount_instance)),
                "speciality": self._parts("speciality")["input"].get("visible", True),
                "quantity": self.quantity.get("visible", True),
                "reason": self._parts("reason")["input"].get("visible", True),
                "plus": self.plus.get("visible", False),
                "marker": True,
            }
        if "plan" in fields:
            out["plan"] = {"present": True, "visible": True, "text": "Treatment Plan"}
        if "procedure" in fields:
            out["procedure"] = ctl_for("procedure")
        if "speciality" in fields:
            out["speciality"] = ctl_for("speciality")
        if "reason" in fields:
            out["reason"] = ctl_for("reason")
        if "quantity" in fields:
            out["quantity"] = {
                "present": True, "visible": True,
                "value": self.quantity["value"],
                "selected": "", "combobox": False,
                "disabled": False,
                "readonly": bool(self.quantity["attrs"].get("readonly")),
                "tag": "input"}
        if "plus" in fields:
            out["plus"] = {"present": True, "visible": self.plus["visible"],
                           "value": "", "selected": "", "combobox": False,
                           "disabled": not self.plus["visible"],
                           "readonly": False, "tag": "img"}
        if "options" in fields:
            token = (spec or {}).get("token") or ""
            if self.config.menu_rerender_after_read and self._menu_visible("procedure"):
                self._rerender_budget = getattr(self, "_rerender_budget", 1)
            # PROBE_JS queries DROPDOWN_OPTIONS globally, so report every
            # option that is currently rendered and visible, whichever
            # combobox owns it.
            texts = []
            for field_name in INSTANCE:
                if not self._menu_visible(field_name):
                    continue
                typed = self._parts(field_name)["input"]["value"]
                texts.extend(o.label for o in self.options_for(field_name, typed))
            out["options"] = {
                "count": len(texts),
                "exact": any(token.upper() in t.upper() for t in texts),
                "texts": texts[:12]}
        if "rows" in fields:
            # same shape PROBE_JS emits: i / code / qty per row
            items = [{"i": i, "code": r["code"], "qty": r["qty"]}
                     for i, r in enumerate(self.rows)]
            out["rows"] = {"count": len(items), "items": items,
                           "hash": str(len(items))}
        if "mutation" in fields:
            out["mutation"] = {"detected": False}
        return out

    # -- misc driver surface ---------------------------------------------
    @property
    def window_handles(self):
        return ["w-plan"]

    @property
    def current_window_handle(self):
        return "w-plan"

    @property
    def current_url(self):
        return "https://portal.example/treatment-plan"

    @property
    def title(self):
        return "Treatment Plan"

    @property
    def page_source(self):
        return "<html>Treatment Plan</html>"

    @property
    def switch_to(self):
        return _SwitchTo(self)

    def dom_calls(self) -> int:
        return (self.counters["find_elements"] + self.counters["execute_script"]
                + self.counters["element_reads"])

    # -- assertions for tests ---------------------------------------------
    def procedure_is_selected(self) -> bool:
        """The ONLY honest definition: React committed a value."""
        return self.selected["procedure"] is not None

    def selected_code(self, field_name="procedure") -> str:
        opt = self.selected[field_name]
        return opt.code if opt else ""

    def typed_text(self, field_name="procedure") -> str:
        return self._parts(field_name)["input"]["value"]


class _SwitchTo:
    def __init__(self, dom):
        self.dom = dom

    def default_content(self):
        return None

    def frame(self, element):
        return None

    def window(self, handle):
        return None


def build_react_portal(**kwargs) -> ReactSelectDOM:
    return ReactSelectDOM(config=ReactSelectConfig(**kwargs))
