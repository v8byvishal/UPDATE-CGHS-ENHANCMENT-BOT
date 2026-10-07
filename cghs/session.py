"""Persistent, verified portal session.

Implements 6.1 (persistent batch session) and 6.2 (cache verified context).

The production path is::

    one Python process
      -> one WebDriver/CDP attach
        -> one patient-tab verification
          -> one Treatment Plan discovery
            -> one frame binding
              -> N deterministic, verified item transactions

Everything cached here is *verified* context.  Mutable portal state (row
contents, control values) is deliberately NOT cached across a mutation.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

from selenium.common.exceptions import WebDriverException

from .dom import (
    FrameContextCache,
    PortalContextLost,
    PortalDriver,
    PortalSynchronizer,
    ProbeUnsupported,
    SmartDOMResolver,
)
from .tabs import (
    AmbiguousPatientTab,
    PatientNotSwitched,
    PatientTabResolver,
    TabEvidence,
)
from .telemetry import EnterpriseLogger, PerfCounters
from .txstate import DispatchLedger, TransactionJournal

DEFAULT_DEBUGGER_ADDRESS = "127.0.0.1:9222"


@dataclass
class PortalIdentity:
    """Immutable evidence that we are on the right patient's Treatment Plan."""

    identity_key: str = ""
    patient_name: str = ""
    ip_case: str = ""
    bill_number: str = ""
    url: str = ""
    title: str = ""

    @property
    def has_patient_evidence(self) -> bool:
        return bool(self.ip_case or self.bill_number or self.patient_name)

    def as_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


class PortalSession:
    """One attach, one verified context, many verified actions."""

    def __init__(self, driver: Any, logger: EnterpriseLogger,
                 journal: Optional[TransactionJournal] = None,
                 run_id: Optional[str] = None,
                 cancel_check: Optional[Callable[[], bool]] = None,
                 trace: bool = False):
        self.raw_driver = driver
        self.logger = logger
        self.logger.trace_enabled = trace or logger.trace_enabled
        self.counters = PerfCounters()
        self.driver = PortalDriver(driver, self.counters, logger)
        self.resolver = SmartDOMResolver(self.driver, logger, self.counters)
        self.sync = PortalSynchronizer(self.driver, logger, self.counters)
        self.frames = FrameContextCache(self.driver, logger, self.counters)
        self.tabs = PatientTabResolver(self.driver, logger, self.counters)
        self.ledger = DispatchLedger(self.counters)
        self.journal = journal if journal is not None else TransactionJournal(enabled=False)
        self.run_id = run_id or f"RUN_{uuid.uuid4().hex[:12]}"
        self.session_id = f"SES_{uuid.uuid4().hex[:12]}"
        self.bill_id: Optional[str] = None
        self.expected_patient: str = ""
        self.identity = PortalIdentity()
        self._previous_identity: Optional[PortalIdentity] = None
        self._previous_patient: str = ""
        self.treatment_plan_discoveries = 0
        self.driver_attaches = 1
        self.started_at = time.time()
        self._cancel_check = cancel_check or (lambda: False)
        self._cancelled = False
        self._context_ready = False

    # ---- lifecycle ----------------------------------------------------
    @classmethod
    def attach(cls, logger: EnterpriseLogger,
               debugger_address: str = DEFAULT_DEBUGGER_ADDRESS,
               attempts: int = 3, backoff: float = 0.6, **kwargs) -> "PortalSession":
        """Attach ONCE to the operator's already-authenticated Chrome.

        No login / MFA / cookie / profile automation is performed - the operator
        authenticates manually and this process only attaches to the existing
        debugging endpoint.
        """
        import selenium.webdriver as webdriver
        from selenium.webdriver.chrome.options import Options

        options = Options()
        options.add_experimental_option("debuggerAddress", debugger_address)
        last_error: Optional[Exception] = None
        for attempt in range(1, attempts + 1):
            try:
                driver = webdriver.Chrome(options=options)
                logger.info(f"[SESSION] CDP attached on {debugger_address} (attempt {attempt})")
                return cls(driver, logger, **kwargs)
            except Exception as exc:                        # noqa: BLE001
                last_error = exc
                logger.warn(f"[SESSION] CDP attach attempt {attempt}/{attempts} failed: {exc}")
                if attempt < attempts:
                    time.sleep(backoff)
        raise WebDriverException(
            f"Could not attach Chrome Debug at {debugger_address}: {last_error}")

    # ---- cancellation --------------------------------------------------
    def cancel(self):
        self._cancelled = True

    def is_cancelled(self) -> bool:
        return self._cancelled or bool(self._cancel_check())

    # ---- bill isolation -------------------------------------------------
    def set_bill(self, bill_id: str, expected_patient: Optional[str] = None):
        """Hard isolation boundary between bills - no state may leak across.

        Every cache that could carry the previous patient's state (dispatch
        ledger, locator strategies, verified context) is dropped, and the
        identity observed for the PREVIOUS bill is retained so the next
        ``ensure_context`` can prove the portal actually moved on.
        """
        self._previous_patient = self.expected_patient
        self._previous_identity = self.identity
        self.bill_id = bill_id
        self.expected_patient = (expected_patient or "").strip()
        self.ledger = DispatchLedger(self.counters)
        self.resolver.invalidate()
        self._context_ready = False          # the plan is re-verified per bill
        self.identity = PortalIdentity()
        self.logger.info(
            f"[BILL ISOLATION] new bill {bill_id}"
            + (f" patient={self.expected_patient!r}" if self.expected_patient else "")
            + "; dispatch ledger + locator cache + verified context cleared")

    def _assert_portal_moved_to_this_bill(self, evidence: TabEvidence):
        """STOP when the queue changed patient but the portal did not.

        Fires only on PROOF - two differently named queued patients resolving
        to one identical portal identity - so a portal that exposes no patient
        evidence can never produce a false stop.
        """
        previous = self._previous_identity
        prev_patient = (self._previous_patient or "").strip()
        if previous is None or not previous.has_patient_evidence:
            return
        if not prev_patient or not self.expected_patient:
            return
        if prev_patient.upper() == self.expected_patient.upper():
            return
        current_key = evidence.identity_key()
        if current_key and current_key == previous.identity_key:
            raise PatientNotSwitched(
                "STOP - the queue advanced to "
                f"{self.expected_patient!r} but the Treatment Plan still shows "
                f"{previous.patient_name!r} (ip={previous.ip_case!r} "
                f"bill={previous.bill_number!r}). Open the correct patient's "
                "Treatment Plan and re-run; nothing was written for this bill.")

    # ---- context --------------------------------------------------------
    def invalidate_context(self, reason: str):
        self._context_ready = False
        self.frames.invalidate(reason)
        self.resolver.invalidate()

    def ensure_context(self, expected_identity: Optional[str] = None,
                       force: bool = False) -> TabEvidence:
        """Cached tab + frame binding.  Full discovery only when invalid."""
        evidence = self.tabs.resolve(expected_identity=expected_identity, force=force)
        if not self.frames.ensure(force=force):
            self.frames.invalidate("no browsing context exposed the controls")
            raise PortalContextLost(
                "Treatment Plan controls are not reachable in any frame of the selected tab")
        if not self._context_ready:
            self.treatment_plan_discoveries += 1
            self.identity = PortalIdentity(
                identity_key=evidence.identity_key(),
                patient_name=evidence.patient_name,
                ip_case=evidence.ip_case,
                bill_number=evidence.bill_number,
                url=evidence.url,
                title=evidence.title,
            )
            self._assert_portal_moved_to_this_bill(evidence)
            self.logger.info(
                f"[CONTEXT] verified Treatment Plan: patient={evidence.patient_name!r} "
                f"ip={evidence.ip_case!r} bill={evidence.bill_number!r} rows={evidence.rows}")
            self._context_ready = True
        return evidence

    # ---- identity reconciliation ----------------------------------------
    #: Identity fields, strongest first.  These are the ONLY things that count
    #: as proof of patient identity; a URL or a tab title is page routing, not
    #: a patient.
    _IDENTITY_FIELDS = ("ip_case", "bill_number", "patient_name")

    def _identity_fallback(self, why: str) -> Tuple[bool, str]:
        """Signature-only check, valid ONLY when nothing stronger ever existed.

        When the verified context carries patient evidence, this path is not
        reachable: URL and title would approve a page that no longer proves
        who the patient is, and the caller is about to trust row data for a
        mutation.  Such a context must fail closed instead.
        """
        if self.identity.has_patient_evidence:
            return False, (
                f"patient identity could not be re-read ({why}); this context "
                "was verified against patient evidence "
                f"(ip={self.identity.ip_case!r} bill={self.identity.bill_number!r} "
                f"name={self.identity.patient_name!r}) and a URL/title match is "
                "not evidence of a patient - refusing to approve it")
        try:
            url, title = self.driver.current_url, self.driver.title
        except WebDriverException as exc:
            return False, f"browser unreachable: {exc}"
        if self.identity.url and url != self.identity.url:
            return False, f"url changed {self.identity.url!r} -> {url!r}"
        if self.identity.title and title != self.identity.title:
            return False, f"title changed {self.identity.title!r} -> {title!r}"
        return True, ("signature fallback matched (this portal never exposed "
                      "patient evidence, so no stronger proof exists)")

    def verify_identity_unchanged(self) -> Tuple[bool, str]:
        """Re-prove we are still on the same patient before trusting row data.

        FAIL-CLOSED.  The gate guards a portal MUTATION, so "we could not
        check" must stop the run exactly like "it changed" does.

        Three fail-open paths were reproduced here and are now closed:

        * the probe answered but every patient field came back EMPTY.  The
          comparison loop required both sides to be non-empty, so not one
          comparison ran and the function reported ``patient evidence matched``
          without having matched any;
        * the probe raised :class:`ProbeUnsupported` and the URL/title were
          unchanged, so a context that HAD been verified against real patient
          evidence was approved on page routing alone;
        * both of the above returned ``True`` with a reason that claimed more
          than had been established.

        The rule now: when the verified context carried patient evidence, at
        least one identity field must be re-read and match, and no re-read
        field may contradict.  Which fields carried the proof - and which were
        unreadable - is named in the reason, so weaker evidence is disclosed
        rather than silently accepted.

        No new identity source is introduced: this reads the same
        ``["signature", "patient"]`` probe the portal already serves.  No
        cookie, token, profile or login automation is involved.
        """
        try:
            state = self.driver.probe(["signature", "patient"])
        except ProbeUnsupported as exc:
            return self._identity_fallback(f"the portal cannot serve the identity probe: {exc}")
        except WebDriverException as exc:
            return False, f"browser unreachable: {exc}"

        patient = state.get("patient") or {}
        signature = state.get("signature") or {}
        current = PortalIdentity(
            patient_name=(patient.get("name") or "").strip(),
            ip_case=(patient.get("ip") or "").strip(),
            bill_number=(patient.get("bill") or "").strip(),
            url=signature.get("url", ""),
            title=signature.get("title", ""),
        )
        if self.identity.has_patient_evidence:
            matched, unreadable = [], []
            for field in self._IDENTITY_FIELDS:
                expected = getattr(self.identity, field)
                actual = getattr(current, field)
                if not expected:
                    continue                       # never part of this proof
                if not actual:
                    unreadable.append(field)
                    continue
                if expected.upper() != actual.upper():
                    return False, f"{field} changed {expected!r} -> {actual!r}"
                matched.append(field)
            if not matched:
                return False, (
                    "patient identity could not be re-read (the portal answered "
                    f"but returned no value for {', '.join(unreadable) or 'any identity field'}); "
                    "refusing to approve an unverifiable patient context")
            detail = f"patient evidence matched on {', '.join(matched)}"
            if unreadable:
                detail += f" ({', '.join(unreadable)} unreadable this time)"
            return True, detail
        if self.identity.url and current.url and current.url != self.identity.url:
            return False, f"url changed {self.identity.url!r} -> {current.url!r}"
        return True, "page signature matched (portal exposes no patient evidence)"

    # ---- reporting --------------------------------------------------------
    def elapsed_ms(self) -> float:
        return (time.time() - self.started_at) * 1000.0

    def close(self):
        """Never quits the operator's browser - the session is only detached."""
        self.logger.info("[SESSION] detached (operator Chrome left running)")
