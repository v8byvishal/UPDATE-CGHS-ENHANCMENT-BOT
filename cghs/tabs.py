"""Patient tab (window handle) discovery with evidence, caching and a hard stop.

Rules implemented (section 9 of the acceptance contract):

* ``window_handles[0]`` is NEVER used.
* Discovery order: cached valid handle -> current active handle -> full scan.
* A candidate is described by compact evidence (URL, title, Treatment Plan
  signature, patient name, IP/case, bill number, control presence).
* Two equally valid patient tabs => STOP and require an explicit operator
  choice.  Guessing is a patient-safety defect, not a convenience.
* An empty / virtualized Treatment Plan table does NOT make a tab invalid.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from selenium.common.exceptions import WebDriverException

from .dom import PortalDriver, ProbeUnsupported
from .telemetry import EnterpriseLogger, PerfCounters


@dataclass
class TabEvidence:
    handle: str = ""
    url: str = ""
    title: str = ""
    treatment_plan: bool = False
    controls: bool = False
    patient_name: str = ""
    ip_case: str = ""
    bill_number: str = ""
    speciality: str = ""
    rows: int = 0
    error: str = ""

    @property
    def eligible(self) -> bool:
        """A usable Treatment Plan tab.

        An empty table is fine (virtualized grids start empty); the required
        evidence is the Treatment Plan signature plus the editable controls.
        """
        return bool(self.treatment_plan and self.controls)

    def identity_key(self) -> str:
        return "|".join([
            (self.ip_case or "").upper(),
            (self.bill_number or "").upper(),
            (self.patient_name or "").upper().strip(),
        ])

    def as_dict(self) -> Dict[str, Any]:
        data = dict(self.__dict__)
        data["eligible"] = self.eligible
        return data


class PatientNotSwitched(Exception):
    """The queue moved to a new patient but the portal did not.

    Writing bill B's codes into a Treatment Plan that is still showing
    patient A is the worst possible outcome of this tool, so it is a hard
    STOP that only the operator can clear.
    """


class AmbiguousPatientTab(Exception):
    """More than one equally valid patient tab - operator must choose."""

    def __init__(self, candidates: List[TabEvidence]):
        self.candidates = candidates
        detail = " || ".join(
            f"[{c.handle}] {c.title!r} patient={c.patient_name!r} ip={c.ip_case!r} "
            f"bill={c.bill_number!r} url={c.url!r}"
            for c in candidates)
        super().__init__(
            f"AMBIGUOUS PATIENT TAB - {len(candidates)} equally valid Treatment Plan "
            f"tabs found. Operator choice required. Candidates: {detail}")


class NoPatientTab(Exception):
    """No browsing context exposes a usable Treatment Plan."""


class PatientTabResolver:
    def __init__(self, driver: PortalDriver, logger: EnterpriseLogger,
                 counters: Optional[PerfCounters] = None):
        self.driver = driver
        self.logger = logger
        self.counters = counters or driver.counters
        self.handle: Optional[str] = None
        self.evidence: Optional[TabEvidence] = None
        self.operator_choice: Optional[str] = None
        self.last_candidates: List[TabEvidence] = []

    # ---- operator interaction ----------------------------------------
    def choose(self, handle: str):
        """Record an explicit operator choice after an ambiguity stop."""
        self.operator_choice = handle
        self.handle = None
        self.evidence = None

    def invalidate(self, reason: str = ""):
        if self.handle:
            self.logger.info(f"[TAB-CACHE] invalidated ({reason or 'unspecified'})")
        self.handle = None
        self.evidence = None

    # ---- evidence ------------------------------------------------------
    def _collect(self, handle: str) -> TabEvidence:
        ev = TabEvidence(handle=handle)
        try:
            state = self.driver.probe(["signature", "patient", "ctx", "rows", "speciality"])
        except ProbeUnsupported:
            return self._collect_fallback(handle)
        except WebDriverException as exc:
            ev.error = str(exc)
            return ev
        sig = state.get("signature") or {}
        pat = state.get("patient") or {}
        ctx = state.get("ctx") or {}
        rows = state.get("rows") or {}
        spec = state.get("speciality") or {}
        ev.url = sig.get("url", "")
        ev.title = sig.get("title", "")
        ev.treatment_plan = bool(sig.get("treatment_plan"))
        ev.controls = bool(ctx.get("procedure")) or bool(ctx.get("inputs"))
        ev.patient_name = (pat.get("name") or "").strip()
        ev.ip_case = (pat.get("ip") or "").strip()
        ev.bill_number = (pat.get("bill") or "").strip()
        ev.rows = int(rows.get("count") or 0)
        ev.speciality = (spec.get("value") or "").strip()
        return ev

    def _collect_fallback(self, handle: str) -> TabEvidence:
        """Documented fallback when the compact probe cannot run."""
        from selenium.webdriver.common.by import By
        ev = TabEvidence(handle=handle)
        try:
            ev.url = self.driver.current_url
            ev.title = self.driver.title
            header = self.driver.find_elements(
                By.XPATH,
                "//*[contains(translate(., 'TREATMENT PLAN', 'treatment plan'), 'treatment plan')]")
            ev.treatment_plan = len(header) > 0
            ev.controls = len(self.driver.find_elements(By.XPATH, "//input | //select")) > 0
        except WebDriverException as exc:
            ev.error = str(exc)
        return ev

    # ---- resolution ----------------------------------------------------
    def resolve(self, expected_identity: Optional[str] = None,
                force: bool = False) -> TabEvidence:
        """Return the verified patient tab, switching to it if needed."""
        if not force and self.handle and self.evidence is not None:
            if self._revalidate(expected_identity):
                self.counters.tab_cache_hits += 1
                return self.evidence
        self.counters.tab_cache_misses += 1

        if self.operator_choice:
            handle = self.operator_choice
            self._switch(handle)
            ev = self._collect(handle)
            if not ev.eligible:
                raise NoPatientTab(f"operator-chosen tab {handle} is not a Treatment Plan tab")
            self.handle, self.evidence = handle, ev
            self.logger.info(f"[TAB] operator-chosen handle {handle} verified")
            return ev

        # 1) current active handle first - cheapest possible path
        try:
            current = self.driver.current_window_handle
        except WebDriverException:
            current = None
        if current:
            ev = self._collect(current)
            if ev.eligible and self._identity_ok(ev, expected_identity):
                others = self._scan_others(current, expected_identity)
                if others:
                    candidates = [ev] + others
                    self.last_candidates = candidates
                    raise AmbiguousPatientTab(candidates)
                self.handle, self.evidence = current, ev
                self.logger.info(
                    f"[TAB] active tab verified patient={ev.patient_name!r} "
                    f"ip={ev.ip_case!r} bill={ev.bill_number!r}")
                return ev

        # 2) full scan - explicitly counted, never the default path
        candidates = self._scan_all(expected_identity)
        if not candidates:
            raise NoPatientTab(
                "No browsing context exposed a Treatment Plan with editable controls")
        if len(candidates) > 1:
            self.last_candidates = candidates
            raise AmbiguousPatientTab(candidates)
        chosen = candidates[0]
        self._switch(chosen.handle)
        self.handle, self.evidence = chosen.handle, chosen
        self.logger.info(f"[TAB] discovered handle {chosen.handle} patient={chosen.patient_name!r}")
        return chosen

    def _identity_ok(self, ev: TabEvidence, expected_identity: Optional[str]) -> bool:
        if not expected_identity:
            return True
        key = ev.identity_key()
        return expected_identity.upper() in key

    def _revalidate(self, expected_identity: Optional[str]) -> bool:
        try:
            if self.driver.current_window_handle != self.handle:
                self._switch(self.handle)
        except WebDriverException:
            self.invalidate("handle no longer valid")
            return False
        ev = self._collect(self.handle or "")
        if ev.eligible and self._identity_ok(ev, expected_identity):
            self.evidence = ev
            return True
        self.invalidate("cached tab failed re-validation")
        return False

    def _switch(self, handle: str):
        self.driver.switch_to.window(handle)

    def _handles(self) -> List[str]:
        return list(self.driver.window_handles)   # counted as a full tab scan

    def _scan_others(self, current: str, expected_identity: Optional[str]) -> List[TabEvidence]:
        """Check whether another tab is *equally* valid (ambiguity detection)."""
        others = []
        try:
            handles = self._handles()
        except WebDriverException:
            return others
        if len(handles) <= 1:
            return others
        for handle in handles:
            if handle == current:
                continue
            try:
                self._switch(handle)
            except WebDriverException:
                continue
            ev = self._collect(handle)
            if ev.eligible and self._identity_ok(ev, expected_identity):
                others.append(ev)
        try:
            self._switch(current)
        except WebDriverException:
            pass
        return others

    def _scan_all(self, expected_identity: Optional[str]) -> List[TabEvidence]:
        out: List[TabEvidence] = []
        try:
            handles = self._handles()
        except WebDriverException as exc:
            raise NoPatientTab(f"window handles unavailable: {exc}") from exc
        for handle in handles:
            try:
                self._switch(handle)
            except WebDriverException:
                continue
            ev = self._collect(handle)
            if ev.eligible and self._identity_ok(ev, expected_identity):
                out.append(ev)
        return out
