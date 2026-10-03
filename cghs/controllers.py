"""Portal controllers - one responsibility each, all condition-driven.

Canonical owner of ProcedureSelector / SpecialitySynchronizer /
SpecialityClearController / QuantityController / ProcedureNameController /
AmountController / EnhancementReasonController / PlusButtonController /
RowCodeQtyVerifier (all moved out of ``app.py``).

What changed versus the baseline
--------------------------------
FASTER
  * no unconditional ``wait_for_idle()`` (15 s generic gate) before every
    micro-operation - each controller waits for ITS OWN readiness condition;
  * dropdown / speciality / quantity / reason / row polling is a single compact
    probe per poll instead of several ``find_elements`` sweeps;
  * adaptive polling (10 ms -> 120 ms) instead of fixed ``sleep(0.08)``.

SAFER (no gate was removed)
  * procedure/row code matching is EXACT (word boundary + enumerated alias)
    instead of substring ``in``;
  * the procedure element is REACQUIRED before final verification, so ENTER is
    never sent to a stale element;
  * the Plus click is guarded by a one-shot ledger token, and every fallback
    click requires fresh proof that no mutation happened yet.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from selenium.common.exceptions import (
    ElementClickInterceptedException,
    ElementNotInteractableException,
    InvalidElementStateException,
    MoveTargetOutOfBoundsException,
    NoSuchElementException,
    StaleElementReferenceException,
    WebDriverException,
)
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from .dom import (
    MUTATION_DISCONNECT_JS,
    MUTATION_INSTALL_JS,
    PortalContextLost,
    ProbeUnsupported,
    row_matches_code,
)
from .locators import portal_input_value, portal_row_aliases
from .txstate import Diagnostic, DispatchProof, DuplicateDispatchBlocked, PlusTransaction

PLACEHOLDERS = {"", "select", "select speciality", "--select--", "none", "null"}

_SET_VALUE_JS = """
var el = arguments[0]; var val = arguments[1];
el.focus();
var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
setter.call(el, val);
el.dispatchEvent(new Event('input', {bubbles:true}));
el.dispatchEvent(new Event('change', {bubbles:true}));
el.dispatchEvent(new Event('blur', {bubbles:true}));
return true;
"""

_SCROLL_JS = "arguments[0].scrollIntoView({block: 'center'}); return true;"


def is_placeholder(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in PLACEHOLDERS


def exact_code_in_text(text: str, code: str) -> bool:
    """Word-boundary match of ``code`` (or an enumerated portal alias) in text."""
    if not text:
        return False
    haystack = text.upper()
    for alias in portal_row_aliases(code):
        if re.search(r"(?<![A-Z0-9])" + re.escape(alias) + r"(?![A-Z0-9])", haystack):
            return True
    return False


def row_qty_matches(qty_text: str, expected_qty: int) -> bool:
    """Baseline-compatible quantity comparison (blank cell == 1)."""
    text = (qty_text or "").strip()
    if text == "" and expected_qty == 1:
        return True
    return text == str(expected_qty)


def row_qty_value(qty_text: str) -> int:
    text = (qty_text or "").strip()
    return int(text) if text.isdigit() else 1


# ---------------------------------------------------------------------------
# Shared base
# ---------------------------------------------------------------------------

class _Controller:
    def __init__(self, session):
        self.session = session
        self.driver = session.driver
        self.resolver = session.resolver
        self.sync = session.sync
        self.logger = session.logger
        self.counters = session.counters

    # -- small helpers -------------------------------------------------
    def _probe(self, fields, token=None):
        return self.driver.probe(fields, token=token)

    def _scroll(self, el):
        try:
            self.driver.execute_script(_SCROLL_JS, el)
        except WebDriverException:
            pass

    def _click(self, el):
        try:
            el.click()
        except Exception:
            self.driver.execute_script("arguments[0].click(); return true;", el)

    def _clear_and_type(self, el, text: str):
        """Portal-native clear + type (unchanged from the proven baseline)."""
        el.send_keys(Keys.CONTROL + "a")
        el.send_keys(Keys.BACKSPACE)
        el.send_keys(text)

    def _set_value_native(self, el, value: str):
        self.driver.execute_script(_SET_VALUE_JS, el, str(value))

    def _element_state(self, el) -> Dict[str, Any]:
        """Selenium fallback for a single control's state."""
        self.counters.element_reads += 3
        cls = (el.get_attribute("class") or "").lower()
        return {
            "present": True,
            "visible": el.is_displayed(),
            "value": el.get_attribute("value") or el.text or "",
            "disabled": (el.get_attribute("disabled") is not None
                         or el.get_attribute("aria-disabled") == "true"
                         or "disabled" in cls
                         or not el.is_enabled()),
            "readonly": el.get_attribute("readonly") is not None,
            "tag": el.tag_name,
        }

    def control_state(self, field: str, locator_key: str) -> Dict[str, Any]:
        """One compact probe, with a documented Selenium fallback."""
        try:
            state = self._probe([field]).get(field) or {}
            if state.get("present"):
                return state
            # Probe says absent - confirm with Selenium before trusting it.
        except ProbeUnsupported:
            self.logger.trace(f"[PROBE-FALLBACK] {field}")
        except WebDriverException as exc:
            raise PortalContextLost(str(exc)) from exc
        try:
            el, _ = self.resolver.locate(locator_key)
        except NoSuchElementException:
            return {"present": False, "visible": False, "value": "", "disabled": False,
                    "readonly": False, "tag": ""}
        return self._element_state(el)


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------

@dataclass
class RowSnapshot:
    count: int
    items: List[Dict[str, Any]]
    hash: str = ""

    def matching(self, code: str) -> List[Dict[str, Any]]:
        return [r for r in self.items if row_matches_code(r.get("code", ""), code)]

    def matching_quantity(self, code: str) -> int:
        return sum(row_qty_value(r.get("qty", "")) for r in self.matching(code))


class TableReader(_Controller):
    """Single source of truth for reading the Treatment Plan table.

    Rows AND the mutation flag are fetched in ONE probe, so the post-Plus wait
    costs a single browser round trip per poll instead of two.
    """

    def __init__(self, session):
        super().__init__(session)
        self.last_mutation = False

    def snapshot(self) -> RowSnapshot:
        try:
            state = self._probe(["rows", "mutation"])
            rows = state.get("rows") or {}
            self.last_mutation = bool((state.get("mutation") or {}).get("detected"))
            return RowSnapshot(count=int(rows.get("count") or 0),
                               items=list(rows.get("items") or []),
                               hash=rows.get("hash", ""))
        except ProbeUnsupported:
            self.logger.trace("[PROBE-FALLBACK] rows")
            return self._snapshot_fallback()
        except WebDriverException as exc:
            raise PortalContextLost(str(exc)) from exc

    def _snapshot_fallback(self) -> RowSnapshot:
        rows = self.resolver.locate_all("TABLE_ROWS")
        items = []
        for idx, row in enumerate(rows):
            code, qty = self.extract_row(row)
            items.append({"i": idx, "code": code, "qty": qty})
        return RowSnapshot(count=len(items), items=items)

    def extract_row(self, row) -> Tuple[str, str]:
        """Baseline ``RowCodeQtyVerifier._extract_row_text`` semantics, preserved."""
        try:
            self.counters.find_elements_calls += 1
            cells = row.find_elements(By.XPATH, ".//td")
            if cells:
                code_cell = ""
                qty_cell = ""
                for cell in cells:
                    self.counters.element_reads += 1
                    txt = cell.text.strip()
                    if re.search(r'\b(LB|RI|CI|CN|RP|GP|CC|C)\d{2,3}\b|DRGU100|CNSU100|DRUG100',
                                 txt, re.IGNORECASE):
                        code_cell = txt
                    if re.match(r'^\d{1,3}$', txt.strip()) and len(txt.strip()) < 4:
                        qty_cell = txt
                if len(cells) >= 4:
                    cand = (cells[-3].text.strip() if len(cells) >= 5 else cells[-2].text.strip())
                    if re.match(r'^\d+$', cand):
                        qty_cell = cand
                return code_cell, qty_cell
            self.counters.element_reads += 1
            txt = row.text
            codes = re.findall(r'\b([A-Z]{1,2}\d{3}|DRGU100|CNSU100)\b', txt, re.IGNORECASE)
            nums = re.findall(r'\b\d{1,3}\b', txt)
            return (codes[0] if codes else ""), (nums[-1] if nums else "")
        except StaleElementReferenceException:
            return "", ""
        except Exception as exc:  # pragma: no cover - defensive
            self.logger.warn(f"Row extract error: {exc}")
            return "", ""


# ---------------------------------------------------------------------------
# Procedure
# ---------------------------------------------------------------------------

class ProcedureSelector(_Controller):
    def execute(self, code: str, timeout: float = 6.0) -> bool:
        portal_target = portal_input_value(code)
        self.logger.info(f"[STATE: TYPE_PROCEDURE] {code} -> portal input {portal_target!r}")

        self.sync.wait_until(
            lambda: self.control_state("procedure", "PROCEDURE_INPUT").get("visible"),
            timeout=4.0, desc="procedure field ready")

        el, _ = self.resolver.locate("PROCEDURE_INPUT")
        self._scroll(el)
        self._click(el)
        try:
            self._clear_and_type(el, portal_target)
        except StaleElementReferenceException:
            self.counters.stale_recoveries += 1
            self.resolver.invalidate("PROCEDURE_INPUT")
            el, _ = self.resolver.locate("PROCEDURE_INPUT")
            self._click(el)
            self._clear_and_type(el, portal_target)

        matched = self._await_exact_option(code, portal_target, timeout)
        self._scroll(matched)
        self._click(matched)
        self.sync.dismiss_overlays()

        # ---- REACQUIRE before verification: the panel re-renders the input --
        return self._verify_selection(code, portal_target)

    def _await_exact_option(self, code: str, portal_target: str, timeout: float):
        """Wait for the dropdown, then pick the EXACT option - never the first."""
        token = portal_target.upper()

        def ready():
            try:
                opts = self._probe(["options"], token=token).get("options") or {}
            except ProbeUnsupported:
                return bool(self.resolver.locate_all("DROPDOWN_OPTIONS"))
            return (opts.get("count") or 0) > 0

        try:
            self.sync.wait_until(ready, timeout=timeout, desc=f"dropdown options for {code}")
        except TimeoutError:
            raise NoSuchElementException(f"Dropdown never opened for [{code}]")

        options = self.resolver.locate_all("DROPDOWN_OPTIONS")
        if not options:
            self.counters.find_elements_calls += 1
            options = self.driver.find_elements(
                By.XPATH,
                "//mat-option | //ng-option | //*[contains(@class, 'option') or contains(@role, 'option')]")
        exact = None
        texts = []
        for opt in options:
            try:
                self.counters.element_reads += 1
                txt = (opt.text or "").strip()
            except StaleElementReferenceException:
                continue
            if not txt:
                continue
            texts.append(txt)
            if exact is None and (exact_code_in_text(txt, code)
                                  or txt.strip().upper() == token):
                exact = opt
        if exact is None:
            raise NoSuchElementException(
                f"Exact code match [{code}] not found among {len(texts)} options: {texts[:8]}")
        return exact

    def _verify_selection(self, code: str, portal_target: str) -> bool:
        def verified():
            state = self.control_state("procedure", "PROCEDURE_INPUT")
            value = state.get("value") or ""
            return exact_code_in_text(value, code) or portal_target.upper() in value.upper()

        try:
            self.sync.wait_until(verified, timeout=2.0, desc="procedure value committed")
            self.logger.info(f"[STATE: PROCEDURE_VERIFIED] {code}")
            return True
        except TimeoutError:
            pass

        # Recovery: ENTER on a FRESHLY reacquired element (never a stale one).
        self.counters.stale_recoveries += 1
        self.resolver.invalidate("PROCEDURE_INPUT")
        try:
            fresh, _ = self.resolver.locate("PROCEDURE_INPUT")
            fresh.send_keys(Keys.ENTER)
        except (NoSuchElementException, StaleElementReferenceException, WebDriverException) as exc:
            raise NoSuchElementException(
                f"Procedure [{code}] could not be verified and the field could not be "
                f"reacquired: {exc}")
        try:
            self.sync.wait_until(verified, timeout=1.5, desc="procedure value committed (retry)")
        except TimeoutError:
            raise ValueError(f"Procedure selection for [{code}] could not be verified - NO PLUS")
        self.logger.info(f"[STATE: PROCEDURE_VERIFIED] {code} (after reacquire)")
        return True


# ---------------------------------------------------------------------------
# Speciality
# ---------------------------------------------------------------------------

class SpecialitySynchronizer(_Controller):
    def execute(self, timeout: float = 5.0) -> str:
        self.logger.trace("[STATE: WAIT_SPECIALITY]")

        def value():
            state = self.control_state("speciality", "SPECIALITY_INPUT")
            raw = (state.get("value") or "").strip()
            if state.get("tag") == "select" and is_placeholder(raw):
                raw = self._selected_option_text()
            return None if is_placeholder(raw) else raw

        try:
            resolved = self.sync.wait_until(value, timeout=timeout, desc="speciality synchronised")
        except TimeoutError:
            raise TimeoutError("Speciality failed to auto-update.")
        self.logger.info(f"[STATE: VERIFY_SPECIALITY] {resolved}")
        return resolved

    def _selected_option_text(self) -> str:
        try:
            from selenium.webdriver.support.ui import Select
            el, _ = self.resolver.locate("SPECIALITY_INPUT")
            return Select(el).first_selected_option.text
        except Exception:
            return ""

    def current_value(self) -> str:
        state = self.control_state("speciality", "SPECIALITY_INPUT")
        raw = (state.get("value") or "").strip()
        if state.get("tag") == "select" and is_placeholder(raw):
            raw = self._selected_option_text()
        return raw


class SpecialityClearController(_Controller):
    def execute(self, next_code: Optional[str] = None) -> bool:
        current = SpecialitySynchronizer(self.session).current_value()
        if is_placeholder(current):
            return False
        self.logger.info(f"[SPECIALITY CLEAR] {current!r} -> clearing before {next_code}")
        try:
            el, _ = self.resolver.locate("SPECIALITY_CLEAR")
        except NoSuchElementException:
            return self._clear_via_js()
        self._scroll(el)
        try:
            self._click(el)
        except WebDriverException:
            return self._clear_via_js()
        return True

    def _clear_via_js(self) -> bool:
        script = (
            'var el = document.evaluate('
            '"//label[contains(translate(., \'SPECIALITY\',\'speciality\'),\'speciality\')]'
            '/following::*[self::input or self::select][1]",'
            'document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;'
            'if (el) { el.value = ""; el.dispatchEvent(new Event("input", {bubbles:true}));'
            'el.dispatchEvent(new Event("change", {bubbles:true})); return true; } return false;')
        try:
            return bool(self.driver.execute_script(script))
        except WebDriverException as exc:
            self.logger.warn(f"[SPECIALITY CLEAR] failed: {exc}")
            return False


# ---------------------------------------------------------------------------
# Quantity / amount / procedure name
# ---------------------------------------------------------------------------

class QuantityController(_Controller):
    def is_locked(self) -> bool:
        state = self.control_state("quantity", "QUANTITY_INPUT")
        if not state.get("present"):
            self.logger.warn("[LOCKED-QTY] quantity control not present - treating as editable")
            return False
        locked = bool(state.get("disabled") or state.get("readonly"))
        if locked:
            self.logger.info(f"[LOCKED-QTY] locked value={state.get('value')!r}")
        return locked

    def current_value(self) -> str:
        return (self.control_state("quantity", "QUANTITY_INPUT").get("value") or "").strip()

    def execute(self, qty: int) -> bool:
        self.logger.info(f"[STATE: WAIT_QUANTITY] Qty:{qty}")
        self.sync.wait_until(
            lambda: (lambda s: s.get("visible") and not s.get("disabled"))(
                self.control_state("quantity", "QUANTITY_INPUT")),
            timeout=2.5, desc="quantity editable")

        el, _ = self.resolver.locate("QUANTITY_INPUT")
        self._scroll(el)
        self._click(el)
        try:
            el.send_keys(Keys.CONTROL + "a")
            el.send_keys(Keys.BACKSPACE)
            self._set_value_native(el, qty)
            el.send_keys(Keys.TAB)
        except StaleElementReferenceException:
            self.counters.stale_recoveries += 1
            self.resolver.invalidate("QUANTITY_INPUT")
            el, _ = self.resolver.locate("QUANTITY_INPUT")
            self._set_value_native(el, qty)

        # MANDATORY verification - never skipped for speed.
        try:
            self.sync.wait_until(lambda: self.current_value() == str(qty),
                                 timeout=2.0, desc="quantity committed")
        except TimeoutError:
            current = self.current_value()
            self.logger.error(f"[QUANTITY_MISMATCH] Expected {qty} got {current!r}")
            raise ValueError(f"Quantity commit failed: expected {qty} got {current!r}")
        self.logger.info(f"[STATE: QUANTITY_COMMITTED] {qty}")
        return True


class ProcedureNameController(_Controller):
    def execute(self, name: str) -> bool:
        try:
            el, _ = self.resolver.locate("PROCEDURE_NAME_INPUT")
        except NoSuchElementException:
            self.counters.find_elements_calls += 1
            els = self.driver.find_elements(By.XPATH, "//input")
            if not els:
                return True
            el = els[0]
        self._scroll(el)
        self._click(el)
        el.send_keys(Keys.CONTROL + "a")
        el.send_keys(Keys.BACKSPACE)
        self._set_value_native(el, name)
        try:
            el.send_keys(Keys.TAB)
        except WebDriverException:
            pass
        return True


class AmountController(_Controller):
    def execute(self, amount: float) -> bool:
        el, _ = self.resolver.locate("AMOUNT_INPUT")
        self._scroll(el)
        self._click(el)
        el.send_keys(Keys.CONTROL + "a")
        el.send_keys(Keys.BACKSPACE)
        amount_text = f"{float(amount):.2f}"
        self._set_value_native(el, amount_text)
        try:
            el.send_keys(Keys.TAB)
        except WebDriverException:
            pass
        try:
            self.sync.wait_until(
                lambda: amount_text in (self._element_state(el).get("value") or ""),
                timeout=1.0, desc="amount echoed")
        except (TimeoutError, StaleElementReferenceException):
            # Not fatal: the post-Plus row verification is the authoritative gate.
            self.logger.warn(f"[AMOUNT] portal did not echo {amount_text} - row verification will decide")
        return True


# ---------------------------------------------------------------------------
# Reason
# ---------------------------------------------------------------------------

@dataclass
class ReasonOutcome:
    present: bool
    selected: bool
    value: str = ""


class EnhancementReasonController(_Controller):
    TARGETS = ("OTHERS", "OTHER")

    def execute(self, timeout: float = 3.0) -> ReasonOutcome:
        state = self.control_state("reason", "REASON_DROPDOWN")
        if not state.get("present"):
            # Verified portal behaviour: some flows have no reason control at all.
            self.logger.info("[STATE: WAIT_REASON] reason control absent - optional, continuing")
            return ReasonOutcome(present=False, selected=False)

        if state.get("tag") == "select":
            return self._select_native()
        return self._select_overlay(timeout)

    def _select_native(self) -> ReasonOutcome:
        from selenium.webdriver.support.ui import Select
        el, _ = self.resolver.locate("REASON_DROPDOWN")
        select = Select(el)
        for label in ("Others", "Other"):
            try:
                select.select_by_visible_text(label)
                return ReasonOutcome(present=True, selected=True, value=label)
            except Exception:
                continue
        raise NoSuchElementException("Reason control present but 'Others' is not selectable - NO PLUS")

    def _select_overlay(self, timeout: float) -> ReasonOutcome:
        el, _ = self.resolver.locate("REASON_DROPDOWN")
        self._scroll(el)
        self._click(el)

        def options_ready():
            try:
                opts = self._probe(["options"]).get("options") or {}
                return (opts.get("count") or 0) > 0
            except ProbeUnsupported:
                return bool(self.resolver.locate_all("DROPDOWN_OPTIONS"))

        try:
            self.sync.wait_until(options_ready, timeout=timeout, desc="reason options visible")
        except TimeoutError:
            raise NoSuchElementException("Reason options never opened - NO PLUS")

        target = None
        options = self.resolver.locate_all("DROPDOWN_OPTIONS")
        if not options:
            self.counters.find_elements_calls += 1
            options = self.driver.find_elements(
                By.XPATH,
                "//mat-option | //ng-option | //*[contains(@class, 'option') or contains(@role, 'option')]")
        for opt in options:
            try:
                self.counters.element_reads += 1
                text = (opt.text or "").strip().upper()
            except StaleElementReferenceException:
                continue
            if any(t in text for t in self.TARGETS):
                target = opt
                break
        if target is None:
            raise NoSuchElementException("'Others' reason option not found - NO PLUS")

        self._scroll(target)
        self._click(target)
        self.sync.dismiss_overlays()

        def verified():
            state = self.control_state("reason", "REASON_DROPDOWN")
            value = (state.get("value") or "").upper()
            if any(t in value for t in self.TARGETS):
                return value
            # Secondary DOM evidence: the overlay closed and a real value is set.
            try:
                opts = self._probe(["options"]).get("options") or {}
                closed = (opts.get("count") or 0) == 0
            except ProbeUnsupported:
                closed = not self.resolver.locate_all("DROPDOWN_OPTIONS")
            if closed and value and not is_placeholder(value):
                return value
            return None

        try:
            value = self.sync.wait_until(verified, timeout=2.0, desc="reason verified")
        except TimeoutError:
            raise ValueError("Reason 'Others' could not be verified - NO PLUS")
        self.logger.info(f"[STATE: VERIFY_REASON] {value}")
        return ReasonOutcome(present=True, selected=True, value=value)


# ---------------------------------------------------------------------------
# Mutation observer
# ---------------------------------------------------------------------------

class MutationWatch(_Controller):
    def install(self) -> bool:
        try:
            self.driver.execute_script(MUTATION_INSTALL_JS)
            self.logger.trace("[TRACE] MutationObserver installed")
            return True
        except WebDriverException as exc:
            self.logger.trace(f"[TRACE] MutationObserver install failed: {exc}")
            return False

    def detected(self) -> bool:
        try:
            state = self._probe(["mutation"]).get("mutation") or {}
            return bool(state.get("detected"))
        except ProbeUnsupported:
            try:
                return bool(self.driver.execute_script(
                    "return window.__cghsMutationDetected || false;"))
            except WebDriverException:
                return False
        except WebDriverException:
            return False

    def disconnect(self):
        try:
            self.driver.execute_script(MUTATION_DISCONNECT_JS)
        except WebDriverException:
            pass


# ---------------------------------------------------------------------------
# Plus dispatch - the authoritative single mutation point
# ---------------------------------------------------------------------------

#: Exceptions that PROVE the click command was refused before the portal could
#: act on it.  Selenium raises these after the browser evaluated the request
#: (or the client refused to build it at all), so the element was not clicked.
#: Combined with a silent settle window they support a PROVEN_NOT_DISPATCHED
#: verdict - i.e. a retry is allowed.
CLICK_REJECTED_EXCEPTIONS = (
    ElementClickInterceptedException,
    ElementNotInteractableException,
    InvalidElementStateException,
    MoveTargetOutOfBoundsException,
    NoSuchElementException,
    StaleElementReferenceException,
    AttributeError,            # client-side: the command was never serialised
    TypeError,                 # client-side: the command was never serialised
)


def click_outcome_is_provable(exc: BaseException) -> bool:
    """Can the absence of a mutation be TRUSTED after this exception?

    Transport-level failures (renderer timeouts, dropped CDP sockets, generic
    WebDriverException) are deliberately excluded: the command may already be
    queued in the browser, so "no row yet" proves nothing and the transaction
    must freeze instead of retrying.
    """
    if isinstance(exc, CLICK_REJECTED_EXCEPTIONS):
        return True
    return False


class PlusButtonController(_Controller):
    """Dispatch exactly one physical Plus per transaction.

    A fallback click strategy is only attempted when the portal proves that no
    mutation has happened yet; otherwise the transaction is frozen for
    reconciliation.
    """

    #: step between re-reads while proving the absence of a mutation
    _settle_step = 0.04

    def __init__(self, session, table: TableReader, watch: MutationWatch,
                 settle_s: float = 0.35):
        super().__init__(session)
        self.table = table
        self.watch = watch
        #: bounded window that makes "no mutation happened" an evidenced claim
        self.settle_s = settle_s

    def locate_button(self):
        try:
            self.counters.find_elements_calls += 1
            for img in self.driver.find_elements(By.CSS_SELECTOR, "img.m9FzljqXbDJyFhzambbf"):
                self.counters.element_reads += 1
                if img.is_displayed():
                    return img
        except WebDriverException:
            pass
        try:
            el, _ = self.resolver.locate("PLUS_BUTTON")
            return el
        except NoSuchElementException:
            return None

    def dispatch(self, tx: PlusTransaction, code: str, before: RowSnapshot) -> bool:
        """Returns True when the browser click returned (NOT that it committed)."""
        button = self.locate_button()
        token_taken = False
        try:
            tx.begin_dispatch()
            token_taken = True
        except DuplicateDispatchBlocked as exc:
            # Last-resort invariant: something tried to click Plus twice.
            self.logger.warn(f"[PLUS-BLOCKED] {exc}")
            tx.mark_duplicate(f"ledger blocked a second physical Plus: {exc}")
            return False

        if button is None:
            tx.abort_before_click("Plus button not found", Diagnostic.PLUS_NOT_FOUND)
            return False

        if self.session.is_cancelled():
            tx.abort_before_click("cancelled before Plus dispatch",
                                  Diagnostic.CANCELLED_PRE_DISPATCH, cancelled=True)
            return False

        try:
            self.driver.execute_script(_SCROLL_JS, button)
        except WebDriverException as exc:
            tx.abort_before_click(f"scrollIntoView failed before click: {exc}",
                                  Diagnostic.CLICK_EXCEPTION_PRE_DISPATCH)
            return False

        attempts = (
            ("js", lambda b: self.driver.execute_script("arguments[0].click(); return true;", b)),
            ("native", lambda b: b.click()),
            ("actions", lambda b: ActionChains(self.driver.driver).move_to_element(b).click().perform()),
        )
        last_error = ""
        for index, (how, action) in enumerate(attempts):
            try:
                action(button)
            except Exception as exc:                      # noqa: BLE001 - classified below
                last_error = f"{type(exc).__name__}: {exc}"
                self.logger.warn(f"[PLUS-{how.upper()}-FAIL] {tx.identity.transaction_id} {last_error}")
                if not click_outcome_is_provable(exc):
                    # Transport-level failure: the browser may still apply the
                    # click.  Absence of a row proves nothing here.
                    self.logger.error(
                        f"[PLUS-AMBIGUOUS] {tx.identity.transaction_id} "
                        f"{type(exc).__name__} cannot prove the click was refused")
                    tx.mark_ambiguous_click(
                        f"click failed at transport level, outcome unknown: {last_error}")
                    return False
                proof = self._absence_proof(code, before)
                if proof is not DispatchProof.PROVEN_NOT_DISPATCHED:
                    # We cannot prove the portal did not see the click -> FREEZE.
                    tx.mark_ambiguous_click(
                        f"click raised and mutation state is ambiguous: {last_error}")
                    return False
                if index + 1 < len(attempts):
                    continue
                tx.abort_before_click(
                    f"all click strategies failed with proven absence: {last_error}",
                    Diagnostic.CLICK_EXCEPTION_PRE_DISPATCH)
                return False
            tx.confirm_browser_click(how)
            self.logger.info(f"[PLUS-DISPATCHED] {tx.identity.transaction_id} via {how}")
            return True
        return False  # pragma: no cover - loop always returns

    def _absence_proof(self, code: str, before: RowSnapshot) -> DispatchProof:
        """Can we PROVE the portal was not mutated by the click that raised?

        SAFETY NOTE - why this waits.  An exception raised by the click call
        does NOT prove the browser never delivered the click: the portal
        commits the new row asynchronously, so an immediate "table unchanged"
        read is indistinguishable from "the row has not landed YET".  Treating
        that as proof of absence is exactly how a duplicate Plus happens.

        The proof therefore requires the mutation observer to stay silent for
        a bounded settle window (``self.settle_s``, derived from the measured
        commit latency of this portal/profile).  Any sign of life inside the
        window -> UNKNOWN -> freeze for reconciliation, never a retry.
        """
        deadline = time.monotonic() + max(0.0, self.settle_s)
        while True:
            try:
                now = self.table.snapshot()
                if self.table.last_mutation or self.watch.detected():
                    return DispatchProof.UNKNOWN
            except (PortalContextLost, WebDriverException):
                return DispatchProof.UNKNOWN
            if now.count != before.count:
                return DispatchProof.UNKNOWN
            if now.matching_quantity(code) != before.matching_quantity(code):
                return DispatchProof.UNKNOWN
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            self.sync.sleep(min(self._settle_step, remaining), reason="dispatch-absence-settle")
        self.logger.info(
            f"[PLUS-ABSENCE-PROVEN] no portal mutation within {self.settle_s:.2f}s settle window")
        return DispatchProof.PROVEN_NOT_DISPATCHED


# ---------------------------------------------------------------------------
# Post-Plus verification + reconciliation
# ---------------------------------------------------------------------------

@dataclass
class VerificationResult:
    committed: bool
    row: Optional[Dict[str, Any]] = None
    diagnostic: Diagnostic = Diagnostic.COMMIT_TIMEOUT_UNKNOWN
    detail: str = ""


class CommitVerifier(_Controller):
    """Targeted verification first, full-table reconciliation as the fallback."""

    def __init__(self, session, table: TableReader, watch: MutationWatch):
        super().__init__(session)
        self.table = table
        self.watch = watch

    def wait_for_commit(self, code: str, expected_qty: int, before: RowSnapshot,
                        timeout: float = 6.0) -> VerificationResult:
        deadline = time.time() + timeout
        from .telemetry import AdaptivePoller
        poller = AdaptivePoller(self.counters)
        last = None
        while True:
            result = self.check_once(code, expected_qty, before)
            if result.committed:
                return result
            last = result
            if time.time() >= deadline:
                break
            poller.wait()
        return last or VerificationResult(False, detail="no verification performed")

    def check_once(self, code: str, expected_qty: int, before: RowSnapshot) -> VerificationResult:
        try:
            now = self.table.snapshot()
        except PortalContextLost as exc:
            return VerificationResult(False, diagnostic=Diagnostic.CONTEXT_LOST, detail=str(exc))

        # --- targeted: a NEW row at the tail matching this exact code --------
        if now.count > before.count:
            for row in now.items[before.count:]:
                if row_matches_code(row.get("code", ""), code):
                    if row_qty_matches(row.get("qty", ""), expected_qty):
                        return VerificationResult(True, row=row,
                                                  diagnostic=Diagnostic.COMMIT_VERIFIED)
                    return VerificationResult(False, row=row,
                                              diagnostic=Diagnostic.ROW_QTY_MISMATCH,
                                              detail=f"row qty {row.get('qty')!r} != {expected_qty}")

        # --- full-table fallback: EXACT matching quantity delta ---------------
        delta = now.matching_quantity(code) - before.matching_quantity(code)
        if delta == expected_qty:
            rows = now.matching(code)
            return VerificationResult(True, row=rows[-1] if rows else None,
                                      diagnostic=Diagnostic.COMMIT_VERIFIED_LATE,
                                      detail=f"matching quantity delta {delta}")
        if delta > expected_qty:
            # Over-commit is a defect, never a success.
            return VerificationResult(False, diagnostic=Diagnostic.ROW_QTY_MISMATCH,
                                      detail=f"portal added {delta} units, expected {expected_qty}")
        if now.count != before.count and delta == 0:
            return VerificationResult(False, diagnostic=Diagnostic.ROW_CODE_MISMATCH,
                                      detail="table mutated but no matching row for this code")
        return VerificationResult(False, diagnostic=Diagnostic.COMMIT_TIMEOUT_UNKNOWN,
                                  detail="target row not present yet")

    def reconcile(self, tx: PlusTransaction, code: str, expected_qty: int,
                  before: RowSnapshot, grace: float = 1.5) -> VerificationResult:
        """Freeze, re-read portal state, and classify EXACTLY.

        Never converts an unknown outcome into success.
        """
        self.counters.reconciliations += 1
        self.logger.warn(f"[RECONCILE] {tx.identity.transaction_id} re-reading portal state")

        identity_ok, identity_detail = self.session.verify_identity_unchanged()
        if not identity_ok:
            return VerificationResult(False, diagnostic=Diagnostic.CONTEXT_LOST,
                                      detail=f"patient/case identity could not be reconfirmed: {identity_detail}")

        result = self.wait_for_commit(code, expected_qty, before, timeout=grace)
        if result.committed:
            result.diagnostic = Diagnostic.COMMIT_VERIFIED_LATE
            return result

        try:
            now = self.table.snapshot()
        except PortalContextLost as exc:
            return VerificationResult(False, diagnostic=Diagnostic.CONTEXT_LOST, detail=str(exc))

        absent = (now.count == before.count
                  and now.matching_quantity(code) == before.matching_quantity(code))
        if absent:
            result.detail = (f"exact absence proven (rows {now.count}, matching qty "
                             f"{now.matching_quantity(code)})")
            result.diagnostic = (Diagnostic.CLICK_EXCEPTION_PRE_DISPATCH
                                 if tx.dispatch_proof is DispatchProof.PROVEN_NOT_DISPATCHED
                                 else Diagnostic.COMMIT_TIMEOUT_UNKNOWN)
            return result
        result.detail = (result.detail or "") + (
            f" | portal rows {before.count}->{now.count}, matching qty "
            f"{before.matching_quantity(code)}->{now.matching_quantity(code)}")
        # Keep the specific classification when we have one; only an genuinely
        # unclassifiable outcome stays COMMIT_TIMEOUT_UNKNOWN.
        if result.diagnostic in (Diagnostic.COMMIT_VERIFIED, Diagnostic.COMMIT_VERIFIED_LATE):
            result.diagnostic = Diagnostic.COMMIT_TIMEOUT_UNKNOWN
        return result
