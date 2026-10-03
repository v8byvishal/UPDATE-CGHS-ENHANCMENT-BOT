"""DOM access layer: compact probes, cached locator resolution, targeted waits.

Canonical owner of ``SmartDOMResolver`` and ``PortalSynchronizer`` (both moved
out of ``app.py``), plus the new frame-context cache.

Performance contract implemented here
-------------------------------------
6.4  Session-scoped *strategy* cache (never a cached ``WebElement``).
6.5  One compact ``execute_script`` probe instead of N Python->browser hops,
     with a documented Selenium fallback for portals whose DOM differs.
6.6  Event/condition driven waiting with adaptive polling - no fixed sleeps.
6.7  ``wait_for_idle`` is no longer an unconditional 15s global gate.
6.3  Frame context is cached and only cheaply re-validated.

Safety contract preserved
-------------------------
*  Verification is never skipped to go faster.
*  Row matching is EXACT (word-boundary token match + enumerated portal
   aliases), which is strictly stricter than the baseline substring match.
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    WebDriverException,
)
from selenium.webdriver.common.by import By

from .locators import LOCATORS, portal_row_aliases
from .telemetry import AdaptivePoller, EnterpriseLogger, PerfCounters


_DISCONNECT_MARKERS = (
    "chrome not reachable",
    "target window already closed",
    "disconnected",
    "no such window",
    "invalid session id",
    "unable to connect to renderer",
    "session deleted",
    "web view not found",
    "connection refused",
    "failed to establish",
)


def is_browser_disconnect(exc: BaseException) -> bool:
    """True when the exception means the BROWSER is gone, not the element.

    A disconnect must never be absorbed by an element-level recovery path:
    retrying a locator against a dead session burns the whole retry budget
    and reports a misleading diagnostic to the operator.
    """
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in _DISCONNECT_MARKERS)


class ProbeUnsupported(Exception):
    """Raised when the compact browser probe cannot serve this portal DOM.

    Callers MUST fall back to the Selenium path - never to an assumption.
    """


class PortalContextLost(Exception):
    """Frame / tab / driver context is no longer valid."""


# ---------------------------------------------------------------------------
# Compact single-probe reads (6.5)
# ---------------------------------------------------------------------------

#: Marker kept as the first token so test doubles can recognise the probe and
#: so it is greppable in Chrome devtools during a live run.
PROBE_MARKER = "/*CGHS_PROBE_V1*/"

PROBE_JS = PROBE_MARKER + r"""
var spec = arguments[0] || {};
try {
  function xpAll(q, root){
    var out = [];
    try {
      var s = document.evaluate(q, root || document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
      for (var i = 0; i < s.snapshotLength; i++) { out.push(s.snapshotItem(i)); }
    } catch (e) {}
    return out;
  }
  function xp1(q){ var a = xpAll(q); return a.length ? a[0] : null; }
  function vis(e){
    if (!e) { return false; }
    try {
      if (e.offsetWidth || e.offsetHeight) { return true; }
      var r = e.getBoundingClientRect();
      return !!(r.width || r.height);
    } catch (err) { return false; }
  }
  function firstVisible(queries){
    for (var i = 0; i < queries.length; i++) {
      var els = xpAll(queries[i]);
      for (var j = 0; j < els.length; j++) { if (vis(els[j])) { return els[j]; } }
    }
    for (var k = 0; k < queries.length; k++) {
      var e2 = xpAll(queries[k]);
      if (e2.length) { return e2[0]; }
    }
    return null;
  }
  function valueOf(e){
    if (!e) { return ""; }
    if (e.value !== undefined && e.value !== null && String(e.value) !== "") { return String(e.value); }
    return String(e.innerText || e.textContent || "").trim();
  }
  function ctl(e){
    if (!e) { return {present: false, visible: false, value: "", disabled: false, readonly: false, tag: ""}; }
    var cls = String(e.className || "").toLowerCase();
    return {
      present: true,
      visible: vis(e),
      value: valueOf(e),
      disabled: !!(e.disabled || e.getAttribute("aria-disabled") === "true" || cls.indexOf("disabled") >= 0),
      readonly: !!(e.readOnly || e.getAttribute("readonly") !== null),
      tag: String(e.tagName || "").toLowerCase()
    };
  }

  var Q = spec.q || {};
  var out = {ok: true, v: 1};
  var want = spec.fields || [];
  function wants(name){ return want.indexOf(name) >= 0; }

  if (wants("busy")) {
    var stable = true;
    try {
      if (window.getAllAngularTestabilities) {
        var ts = window.getAllAngularTestabilities();
        for (var t = 0; t < ts.length; t++) { if (!ts[t].isStable()) { stable = false; break; } }
      }
    } catch (e) { stable = true; }
    var spinners = document.querySelectorAll(
      "div.spinner, div[class*='spinner'], div[class*='loader'], div[class*='ngx-overlay']");
    var busySpinner = false;
    for (var s = 0; s < spinners.length; s++) { if (vis(spinners[s])) { busySpinner = true; break; } }
    out.busy = (!stable) || busySpinner;
    out.angular_stable = stable;
  }

  if (wants("ctx")) {
    out.ctx = {
      inputs: document.querySelectorAll("input, select").length,
      procedure: !!firstVisible(Q.PROCEDURE_INPUT || []),
      marker: !!xp1("//*[contains(translate(., 'TREATMENT PLAN', 'treatment plan'), 'treatment plan')]")
    };
  }

  if (wants("procedure"))  { out.procedure  = ctl(firstVisible(Q.PROCEDURE_INPUT || [])); }
  if (wants("speciality")) { out.speciality = ctl(firstVisible(Q.SPECIALITY_INPUT || [])); }
  if (wants("quantity"))   { out.quantity   = ctl(firstVisible(Q.QUANTITY_INPUT || [])); }
  if (wants("reason"))     { out.reason     = ctl(firstVisible(Q.REASON_DROPDOWN || [])); }
  if (wants("plus")) {
    var pb = firstVisible(Q.PLUS_BUTTON || []);
    out.plus = ctl(pb);
    out.plus.clickable = !!(pb && vis(pb) && !out.plus.disabled);
  }

  if (wants("options")) {
    var token = String(spec.token || "").toUpperCase();
    var opts = [];
    var seen = [];
    var oq = Q.DROPDOWN_OPTIONS || [];
    for (var o = 0; o < oq.length; o++) {
      var list = xpAll(oq[o]);
      for (var p = 0; p < list.length; p++) {
        var node = list[p];
        if (seen.indexOf(node) >= 0) { continue; }
        seen.push(node);
        if (!vis(node)) { continue; }
        opts.push(String(node.innerText || node.textContent || "").trim());
      }
    }
    out.options = {count: opts.length, texts: opts.slice(0, 60)};
    if (token) {
      var exact = -1;
      for (var q2 = 0; q2 < opts.length; q2++) {
        if (opts[q2].toUpperCase().indexOf(token) >= 0) { exact = q2; break; }
      }
      out.options.token_index = exact;
    }
  }

  if (wants("rows")) {
    var rowNodes = [];
    var rq = Q.TABLE_ROWS || [];
    for (var r2 = 0; r2 < rq.length; r2++) {
      var rs = xpAll(rq[r2]);
      for (var r3 = 0; r3 < rs.length; r3++) {
        if (rowNodes.indexOf(rs[r3]) < 0) { rowNodes.push(rs[r3]); }
      }
    }
    var rows = [];
    var codeRe = /\b(?:LB|RI|CI|CN|RP|GP|CC|C)\d{2,3}\b|DRGU100|CNSU100|DRUG100/i;
    for (var n = 0; n < rowNodes.length; n++) {
      var cells = rowNodes[n].querySelectorAll("td");
      var codeCell = "", qtyCell = "";
      if (cells.length) {
        for (var c = 0; c < cells.length; c++) {
          var txt = String(cells[c].innerText || cells[c].textContent || "").trim();
          if (codeRe.test(txt)) { codeCell = txt; }
          if (/^\d{1,3}$/.test(txt)) { qtyCell = txt; }
        }
        if (cells.length >= 4) {
          var ci = cells.length >= 5 ? cells.length - 3 : cells.length - 2;
          var cand = String(cells[ci].innerText || cells[ci].textContent || "").trim();
          if (/^\d+$/.test(cand)) { qtyCell = cand; }
        }
      } else {
        var whole = String(rowNodes[n].innerText || rowNodes[n].textContent || "");
        var cm = whole.match(/\b([A-Z]{1,2}\d{3}|DRGU100|CNSU100)\b/i);
        var nm = whole.match(/\b\d{1,3}\b/g);
        codeCell = cm ? cm[0] : "";
        qtyCell = nm ? nm[nm.length - 1] : "";
      }
      rows.push({i: n, code: codeCell, qty: qtyCell});
    }
    var hash = "";
    for (var h = 0; h < Math.min(rows.length, 5); h++) { hash += rows[h].code + ":" + rows[h].qty + "|"; }
    out.rows = {count: rows.length, hash: hash, items: rows};
  }

  if (wants("signature")) {
    out.signature = {
      url: String(location.href || ""),
      title: String(document.title || ""),
      treatment_plan: !!xp1("//*[contains(translate(., 'TREATMENT PLAN', 'treatment plan'), 'treatment plan')]")
    };
  }

  if (wants("patient")) {
    var body = String(document.body ? (document.body.innerText || "") : "");
    function grab(re){ var m = body.match(re); return m ? String(m[1]).trim() : ""; }
    out.patient = {
      name: grab(/(?:Patient\s*Name|Name)\s*[:\-]\s*([A-Za-z.\s]{3,60})/i),
      ip: grab(/\b(?:IP\s*(?:No|Number|ID)?|IP)\s*[:\-]?\s*([A-Z]{2,6}IP\d{3,10})/i),
      bill: grab(/\b(?:Bill\s*(?:No|Number)?)\s*[:\-]?\s*([A-Z]{2,6}-[A-Z]{2,6}-\d{3,10})/i)
    };
  }

  if (wants("mutation")) {
    out.mutation = {
      detected: !!window.__cghsMutationDetected,
      count: window.__cghsMutationCount || 0
    };
  }
  return out;
} catch (fatal) {
  return {ok: false, error: String(fatal)};
}
"""

MUTATION_INSTALL_JS = r"""
window.__cghsMutationDetected = false;
window.__cghsMutationCount = 0;
if (window.__cghsObserver) { try { window.__cghsObserver.disconnect(); } catch (e) {} }
var target = null;
try {
  target = document.evaluate(
    "//table[contains(@class,'table')]//tbody | //div[contains(@class,'treatment-grid')]",
    document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
} catch (e) {}
if (!target) { target = document.body; }
window.__cghsObserver = new MutationObserver(function (mutations) {
  for (var i = 0; i < mutations.length; i++) {
    var m = mutations[i];
    if (m.addedNodes.length > 0 || m.removedNodes.length > 0) {
      window.__cghsMutationDetected = true;
      window.__cghsMutationCount += 1;
    }
  }
});
window.__cghsObserver.observe(target, {childList: true, subtree: true, attributes: false});
return true;
"""

MUTATION_DISCONNECT_JS = (
    "if (window.__cghsObserver) { try { window.__cghsObserver.disconnect(); } catch (e) {} "
    "window.__cghsObserver = null; } return true;"
)

#: XPath payloads handed to the browser probe so the JS and the Python fallback
#: can never drift apart - both read the single :data:`cghs.locators.LOCATORS`.
_PROBE_QUERIES: Dict[str, List[str]] = {
    key: [value for by, value in strategies if by == By.XPATH]
    for key, strategies in LOCATORS.items()
}

_CODE_TOKEN_RE = re.compile(r"\b(?:LB|RI|CI|CN|RP|GP|CC|C)\d{2,3}\b|DRGU100|CNSU100|DRUG100", re.IGNORECASE)


def row_matches_code(code_text: str, code: str) -> bool:
    """EXACT portal row match.

    The baseline used ``expected_code.upper() in code_text.upper()``, which makes
    ``C001`` match a ``CC001`` row.  Matching is now anchored on word boundaries
    and restricted to the enumerated portal aliases for that code.
    """
    if not code_text:
        return False
    haystack = code_text.upper()
    for alias in portal_row_aliases(code):
        if re.search(r"(?<![A-Z0-9])" + re.escape(alias) + r"(?![A-Z0-9])", haystack):
            return True
    return False


def row_quantity_value(qty_text: str) -> int:
    text = (qty_text or "").strip()
    if text.isdigit():
        return int(text)
    return 1 if text == "" else 0


# ---------------------------------------------------------------------------
# Instrumented driver facade
# ---------------------------------------------------------------------------

class PortalDriver:
    """Thin counting facade over a Selenium WebDriver.

    Keeps every browser round trip visible to :class:`PerfCounters` so the
    performance claims in the report are measured, not asserted.
    """

    def __init__(self, driver: Any, counters: PerfCounters, logger: EnterpriseLogger):
        self.driver = driver
        self.counters = counters
        self.logger = logger

    # ---- raw ---------------------------------------------------------
    def execute_script(self, script: str, *args):
        self.counters.execute_script_calls += 1
        return self.driver.execute_script(script, *args)

    def find_elements(self, by, value):
        self.counters.find_elements_calls += 1
        return self.driver.find_elements(by, value)

    @property
    def switch_to(self):
        return self.driver.switch_to

    @property
    def window_handles(self):
        self.counters.full_tab_scans += 1
        return self.driver.window_handles

    @property
    def current_window_handle(self):
        return self.driver.current_window_handle

    @property
    def current_url(self):
        return self.driver.current_url

    @property
    def title(self):
        return self.driver.title

    # ---- compact probe ----------------------------------------------
    def probe(self, fields: Sequence[str], token: Optional[str] = None) -> Dict[str, Any]:
        """One browser round trip for N properties.

        :raises ProbeUnsupported: when the portal DOM cannot serve the probe.
            The caller must then use the documented Selenium fallback.
        """
        spec = {"fields": list(fields), "q": _PROBE_QUERIES}
        if token is not None:
            spec["token"] = token
        self.counters.probe_calls += 1
        try:
            result = self.execute_script(PROBE_JS, spec)
        except StaleElementReferenceException:
            raise
        except WebDriverException as exc:
            self.counters.probe_fallbacks += 1
            raise ProbeUnsupported(str(exc)) from exc
        if not isinstance(result, dict) or not result.get("ok"):
            self.counters.probe_fallbacks += 1
            raise ProbeUnsupported(f"probe returned {result!r}")
        return result


# ---------------------------------------------------------------------------
# Locator resolution with session-scoped strategy cache (6.4)
# ---------------------------------------------------------------------------

class SmartDOMResolver:
    """Resolve portal controls, remembering which strategy worked.

    The cache stores the *strategy* (``(By, selector)``), never a ``WebElement``
    - a cached element would go stale across a portal mutation.
    """

    def __init__(self, driver: PortalDriver, logger: EnterpriseLogger,
                 counters: Optional[PerfCounters] = None):
        self.driver = driver
        self.logger = logger
        self.counters = counters or driver.counters
        self._strategy_cache: Dict[str, Tuple[Any, str]] = {}

    # ---- cache control ----------------------------------------------
    def invalidate(self, locator_key: Optional[str] = None):
        if locator_key is None:
            if self._strategy_cache:
                self.logger.trace("[LOCATOR-CACHE] full invalidate")
            self._strategy_cache.clear()
        else:
            self._strategy_cache.pop(locator_key, None)

    def cached_strategy(self, locator_key: str):
        return self._strategy_cache.get(locator_key)

    # ---- resolution --------------------------------------------------
    def _usable(self, el, require_enabled: bool) -> bool:
        try:
            self.counters.element_reads += 1
            if not el.is_displayed():
                return False
            if require_enabled:
                self.counters.element_reads += 1
                return el.is_enabled()
            return True
        except StaleElementReferenceException:
            return False
        except WebDriverException as exc:
            if is_browser_disconnect(exc):
                raise
            return False

    def _scan(self, strategies, ctx, require_enabled: bool):
        for strategy, val in strategies:
            try:
                if ctx is self.driver:
                    els = self.driver.find_elements(strategy, val)
                else:
                    self.counters.find_elements_calls += 1
                    els = ctx.find_elements(strategy, val)
            except Exception as exc:
                # A dead browser is not a failed selector: re-raise so the
                # caller reports BROWSER_DISCONNECTED instead of walking every
                # remaining strategy against a session that cannot answer.
                if is_browser_disconnect(exc):
                    raise
                continue
            for el in els:
                if self._usable(el, require_enabled):
                    return el, (strategy, val)
        return None, None

    def locate(self, locator_key: str, parent: Optional[Any] = None):
        if locator_key not in LOCATORS:
            raise ValueError(f"Locator key '{locator_key}' is not registered.")
        strategies = LOCATORS[locator_key]
        ctx = parent if parent is not None else self.driver

        # ---- fast path: last known good strategy ----------------------
        cached = self._strategy_cache.get(locator_key) if parent is None else None
        if cached is not None:
            el, found = self._scan([cached], ctx, True)
            if el is None:
                el, found = self._scan([cached], ctx, False)
            if el is not None:
                self.counters.locator_cache_hits += 1
                return el, found
            self.counters.locator_cache_misses += 1
            self._strategy_cache.pop(locator_key, None)
            self.logger.trace(f"[LOCATOR-CACHE] miss for {locator_key}, rediscovering")
        elif parent is None:
            self.counters.locator_cache_misses += 1

        # ---- full strategy walk (baseline semantics preserved) --------
        for require_enabled in (True, False):
            el, found = self._scan(strategies, ctx, require_enabled)
            if el is not None:
                if parent is None:
                    self._strategy_cache[locator_key] = found
                return el, found
        for strategy, val in strategies:
            try:
                els = (self.driver.find_elements(strategy, val) if ctx is self.driver
                       else ctx.find_elements(strategy, val))
            except Exception as exc:
                if is_browser_disconnect(exc):
                    raise
                continue
            if els:
                if parent is None:
                    self._strategy_cache[locator_key] = (strategy, val)
                return els[0], (strategy, val)
        raise NoSuchElementException(
            f"SmartDOMResolver could not locate '{locator_key}' using any strategy.")

    def locate_all(self, locator_key: str) -> List[Any]:
        """Visible elements for a key, DEDUPLICATED across overlapping strategies.

        The baseline concatenated the per-strategy results, so an element matched
        by two strategies was counted twice - which silently inflated portal row
        counts and could make the duplicate guard skip legitimate work.
        """
        if locator_key not in LOCATORS:
            return []
        found: List[Any] = []
        seen = set()
        for strategy, val in LOCATORS[locator_key]:
            try:
                els = self.driver.find_elements(strategy, val)
            except Exception:
                continue
            for el in els:
                key = self._identity(el)
                if key in seen:
                    continue
                if not self._usable(el, False):
                    continue
                seen.add(key)
                found.append(el)
        return found

    @staticmethod
    def _identity(el) -> Any:
        ident = getattr(el, "id", None)
        if ident is not None:
            return ("id", ident)
        return ("obj", id(el))


# ---------------------------------------------------------------------------
# Targeted readiness waiting (6.6 / 6.7)
# ---------------------------------------------------------------------------

class PortalSynchronizer:
    """Condition-driven waits.

    ``wait_for_idle`` is retained for API compatibility but is now a cheap,
    short, *optional* settle used only where a genuine global settle is needed.
    Per-operation readiness is expressed as a condition on the target control.
    """

    def __init__(self, driver: PortalDriver, logger: EnterpriseLogger,
                 counters: Optional[PerfCounters] = None, timeout: float = 15.0):
        self.driver = driver
        self.logger = logger
        self.counters = counters or driver.counters
        self.timeout = timeout
        #: budget for the optional global settle - deliberately NOT ``timeout``
        self.idle_budget = 1.5

    # ---- generic condition wait -------------------------------------
    def wait_until(self, condition: Callable[[], Any], timeout: float, desc: str):
        """Adaptive-poll a python-side condition.  Returns the truthy value."""
        poller = AdaptivePoller(self.counters)
        deadline = time.time() + timeout
        started = time.time()
        last_exc = None
        while True:
            try:
                value = condition()
                if value:
                    self.counters.explicit_wait_ms += (time.time() - started) * 1000.0
                    return value
            except StaleElementReferenceException as exc:
                last_exc = exc
            except ProbeUnsupported:
                raise
            except WebDriverException as exc:
                if is_browser_disconnect(exc):
                    # Polling a dead browser can never succeed: fail fast with
                    # the real cause instead of burning the whole timeout and
                    # reporting a misleading "control not found".
                    self.counters.explicit_wait_ms += (time.time() - started) * 1000.0
                    raise
                last_exc = exc
            if time.time() >= deadline:
                break
            poller.wait()
        self.counters.explicit_wait_ms += (time.time() - started) * 1000.0
        raise TimeoutError(f"condition '{desc}' not met within {timeout}s ({last_exc})")

    # ---- accounted waiting ------------------------------------------
    def sleep(self, seconds: float, reason: str, blind: bool = False):
        """A measured pause.

        ``blind=True`` is the only thing that counts towards the
        ``fixed_sleep_ms`` telemetry budget; evidence-bounded settles (which
        re-read the portal each iteration and exit early) are charged to
        ``explicit_wait_ms`` instead.
        """
        if seconds <= 0:
            return
        time.sleep(seconds)
        millis = seconds * 1000.0
        if blind:
            self.counters.fixed_sleep_ms += millis
            self.logger.trace(f"[SYNC] fixed sleep {millis:.0f}ms ({reason})")
        else:
            self.counters.explicit_wait_ms += millis

    # ---- cheap global settle ----------------------------------------
    def wait_for_idle(self, timeout: Optional[float] = None) -> bool:
        """ONE probe for page stability, bounded by :attr:`idle_budget`.

        Returns ``True`` when the page reported not-busy.  Never raises: a busy
        page is a reason to keep verifying, not a reason to abort.
        """
        budget = self.idle_budget if timeout is None else timeout
        started = time.time()
        poller = AdaptivePoller(self.counters)
        while True:
            try:
                state = self.driver.probe(["busy"])
                if not state.get("busy"):
                    return True
            except ProbeUnsupported:
                return self._wait_for_idle_fallback(budget - (time.time() - started))
            except WebDriverException:
                return False
            if time.time() - started >= budget:
                self.logger.trace("[SYNC] idle budget reached, proceeding with verification")
                return False
            poller.wait()

    def _wait_for_idle_fallback(self, remaining: float) -> bool:
        """Documented fallback when the compact probe cannot run."""
        self.logger.trace("[SYNC] probe unsupported - Selenium idle fallback")
        poller = AdaptivePoller(self.counters)
        deadline = time.time() + max(remaining, 0.0)
        while True:
            try:
                spinners = self.driver.find_elements(
                    By.XPATH,
                    "//div[contains(@class,'spinner') or contains(@class,'loader') "
                    "or contains(@class,'ngx-overlay')]")
                if not any(s.is_displayed() for s in spinners):
                    return True
            except WebDriverException:
                return False
            if time.time() >= deadline:
                return False
            poller.wait()

    def dismiss_overlays(self):
        script = (
            "var backs = document.querySelectorAll('.cdk-overlay-backdrop, .modal-backdrop');"
            "for (var i = 0; i < backs.length; i++) { backs[i].parentNode.removeChild(backs[i]); }"
            "return true;")
        try:
            self.driver.execute_script(script)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Frame context cache (6.3)
# ---------------------------------------------------------------------------

class FrameContextCache:
    """Reuse the verified frame; re-discover only when it is actually invalid.

    The baseline called ``switch_to.default_content()`` and re-scanned every
    iframe for EVERY item.  Here the cached context is validated with one cheap
    probe, and a full discovery happens only on a real context loss.
    """

    def __init__(self, driver: PortalDriver, logger: EnterpriseLogger,
                 counters: Optional[PerfCounters] = None):
        self.driver = driver
        self.logger = logger
        self.counters = counters or driver.counters
        self.frame_index: Optional[int] = None   # ``None`` == default content
        self.validated = False

    def invalidate(self, reason: str = ""):
        if self.validated:
            self.logger.info(f"[FRAME-CACHE] invalidated ({reason or 'unspecified'})")
        self.validated = False

    def ensure(self, force: bool = False) -> bool:
        if self.validated and not force and self._cheap_validate():
            self.counters.frame_cache_hits += 1
            return True
        self.counters.frame_cache_misses += 1
        return self._discover()

    def _cheap_validate(self) -> bool:
        try:
            state = self.driver.probe(["ctx"])
        except ProbeUnsupported:
            try:
                self.counters.find_elements_calls += 1
                return len(self.driver.driver.find_elements(By.XPATH, "//input | //select")) > 0
            except WebDriverException:
                return False
        except WebDriverException:
            return False
        ctx = state.get("ctx") or {}
        return bool(ctx.get("inputs"))

    def _discover(self) -> bool:
        """Full iframe discovery - the slow path, counted and logged."""
        self.counters.frame_discoveries += 1
        self.logger.info("[FRAME-DISCOVERY] scanning browsing contexts")
        try:
            self.driver.switch_to.default_content()
        except WebDriverException as exc:
            raise PortalContextLost(f"default_content failed: {exc}") from exc
        if self._cheap_validate():
            self.frame_index = None
            self.validated = True
            return True
        try:
            iframes = self.driver.find_elements(By.TAG_NAME, "iframe")
        except WebDriverException as exc:
            raise PortalContextLost(f"iframe scan failed: {exc}") from exc
        for idx, iframe in enumerate(iframes):
            try:
                self.driver.switch_to.frame(iframe)
            except WebDriverException:
                try:
                    self.driver.switch_to.default_content()
                except WebDriverException:
                    pass
                continue
            if self._cheap_validate():
                self.frame_index = idx
                self.validated = True
                self.logger.info(f"[FRAME-DISCOVERY] bound to iframe #{idx}")
                return True
            try:
                self.driver.switch_to.default_content()
            except WebDriverException:
                pass
        try:
            self.driver.switch_to.default_content()
        except WebDriverException:
            pass
        self.frame_index = None
        self.validated = False
        return False
