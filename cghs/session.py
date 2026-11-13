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

import json
import os
import re
import socket
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
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

# ---------------------------------------------------------------------------
# CHROME ENDPOINT CONFIGURATION - the single source of truth
#
# The launcher (START_CGHS_CHROME.cmd) and this module MUST agree on the port,
# otherwise the operator starts Chrome on one port and the application waits
# on another.  Both read the same environment variable, and both fall back to
# the same default.
# ---------------------------------------------------------------------------
DEFAULT_DEBUGGER_HOST = "127.0.0.1"
DEFAULT_DEBUGGER_PORT = 9222
DEFAULT_DEBUGGER_ADDRESS = f"{DEFAULT_DEBUGGER_HOST}:{DEFAULT_DEBUGGER_PORT}"

#: Environment variable shared with the Chrome launcher.
CHROME_PORT_ENV = "CGHS_CHROME_PORT"

#: Overrides the location of the shared configuration file (tests, and
#: unusual installations).
CHROME_CONFIG_ENV = "CGHS_CHROME_CONFIG"

#: Folder shared with the Chrome launcher: it holds the dedicated automation
#: profile and the shared endpoint configuration.
_CONFIG_DIR_NAME = "CGHS-Enhancement-Bot"
_CONFIG_FILE_NAME = "chrome_config.json"

#: Only the operator's own machine may be addressed.  Attaching to a remote
#: host would mean driving someone else's browser over the network.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

#: Hard cap on how much the endpoint probe will read.  A /json/version
#: reply is a few hundred bytes; this only bounds a hostile or broken
#: service that streams without end.
_MAX_PROBE_RESPONSE_BYTES = 262144

#: How long the pre-flight endpoint probe may take.  This is a LOCAL socket on
#: the operator's own machine, so a healthy endpoint answers in low single-digit
#: milliseconds; the budget only bounds the failure case.
_ENDPOINT_PROBE_TIMEOUT = 1.0

#: The honest connection ladder.  Each rung is a DIFFERENT fact.  A verified
#: debugging endpoint says nothing about whether Selenium attached, whether the
#: portal tab exists, or whether a patient's Treatment Plan is open - so the UI
#: must never collapse these into one "connected" claim.
CONNECTION_STAGES = (
    "CHROME PROCESS AVAILABLE",
    "CDP ENDPOINT VERIFIED",
    "SELENIUM ATTACHED",
    "PORTAL TAB FOUND",
    "PATIENT VERIFIED",
    "TREATMENT PLAN READY",
)


def chrome_config_path() -> Path:
    """Where the launcher and the application agree on the endpoint.

    Windows:  %LOCALAPPDATA%\\CGHS-Enhancement-Bot\\chrome_config.json
    Other:    ~/.local/share/CGHS-Enhancement-Bot/chrome_config.json

    An environment variable cannot carry this setting on its own: ``setx``
    writes the user profile but does NOT update any shell that is already
    open, so a launcher that only exported a variable would leave the
    already-running application on the old port.  A file is read fresh on
    every attach, so both sides always agree.
    """
    override = (os.environ.get(CHROME_CONFIG_ENV) or "").strip()
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / _CONFIG_DIR_NAME / _CONFIG_FILE_NAME
    return (Path.home() / ".local" / "share"
            / _CONFIG_DIR_NAME / _CONFIG_FILE_NAME)


def load_chrome_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """Read the shared configuration.

    Returns ``{"host": ..., "port": ..., "source": "file"|"env"|"default"}``.

    A corrupt or malformed file raises ``ValueError`` naming the file.  It
    must never silently fall back to 9222: that is exactly how the launcher
    and the application end up on different ports.
    """
    target = Path(path) if path is not None else chrome_config_path()
    host = DEFAULT_DEBUGGER_HOST
    port = DEFAULT_DEBUGGER_PORT
    source = "default"

    env_port = (os.environ.get(CHROME_PORT_ENV) or "").strip()
    if env_port:
        port = _coerce_port(env_port, f"{CHROME_PORT_ENV}={env_port!r}")
        source = "env"

    if target.exists():
        try:
            # utf-8-sig, NOT utf-8: Windows PowerShell 5.1 writes a UTF-8 BOM
            # for "-Encoding UTF8", and a leading BOM makes json.loads fail
            # with an opaque "Expecting value" error.  utf-8-sig strips a BOM
            # when present and is identical to utf-8 when it is not, so both
            # the launcher's output and a hand-edited file are accepted.
            raw = target.read_text(encoding="utf-8-sig")
        except OSError as exc:
            raise ValueError(f"could not read {target}: {exc}")
        if not raw.strip():
            raise ValueError(
                f"{target} is empty. Delete it and run START_CGHS_CHROME.cmd "
                f"again to recreate it, or write "
                f'{{"host": "127.0.0.1", "port": 9222}} into it.')
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise ValueError(
                f"{target} is not valid JSON ({exc}). Fix or delete the file; "
                f"the launcher will recreate it.")
        if not isinstance(data, dict):
            raise ValueError(
                f"{target} must contain a JSON object such as "
                f'{{"host": "127.0.0.1", "port": 9222}}')
        # The file is authoritative: the launcher writes it, so it reflects
        # the port Chrome was ACTUALLY started on.
        if "port" in data:
            port = _coerce_port(data.get("port"), f"port in {target}")
            source = "file"
        if data.get("host"):
            host = str(data["host"]).strip()
            source = "file"

    return {"host": host, "port": port, "source": source, "path": str(target)}


def _coerce_port(value: Any, where: str) -> int:
    """A TCP port, or a ValueError that says what was wrong."""
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{where} is not a port number")
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f"{where} is not a whole port number")
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise ValueError(
            f"{where} is not a whole number; expected a TCP port 1-65535")
    if not 1 <= port <= 65535:
        raise ValueError(
            f"{where} is outside the valid TCP port range 1-65535")
    return port


def resolve_debugger_port(env: Optional[Dict[str, str]] = None) -> int:
    """The debugging port, from the one shared configuration source.

    Order: the shared file (written by the launcher) > CGHS_CHROME_PORT >
    the default.  The file wins because ``setx`` cannot update a shell that
    is already open, so an environment variable may be stale while the file
    always reflects the port Chrome was started on.

    Raises ``ValueError`` on anything invalid rather than falling back - a
    silent fallback is what puts the launcher and the application on
    different ports.
    """
    if env is not None:
        raw = (env.get(CHROME_PORT_ENV) or "").strip()
        if not raw:
            return DEFAULT_DEBUGGER_PORT
        return _coerce_port(raw, f"{CHROME_PORT_ENV}={raw!r}")
    return int(load_chrome_config()["port"])


def resolve_debugger_address(env: Optional[Dict[str, str]] = None) -> str:
    """The full ``host:port`` the application will attach to."""
    if env is not None:
        return f"{DEFAULT_DEBUGGER_HOST}:{resolve_debugger_port(env)}"
    config = load_chrome_config()
    address = f"{config['host']}:{config['port']}"
    validate_debugger_address(address)
    return address


def validate_debugger_address(address: str) -> Tuple[str, int]:
    """Split and validate a debugging address, loopback only."""
    host, _, port_text = str(address).rpartition(":")
    host = (host or DEFAULT_DEBUGGER_HOST).strip().strip("[]").lower()
    try:
        port = int(port_text)
    except (TypeError, ValueError):
        raise ValueError(f"{address!r} is not a host:port debugging address")
    if not 1 <= port <= 65535:
        raise ValueError(
            f"{address!r} has a port outside the valid range 1-65535")
    if host not in _LOOPBACK_HOSTS:
        raise ValueError(
            f"{address!r} is not a loopback address - this application only "
            f"attaches to a browser on the operator's own machine")
    return host, port


def probe_debugger_endpoint(address: Optional[str] = None,
                            timeout: float = _ENDPOINT_PROBE_TIMEOUT):
    """Is a real Chrome DevTools endpoint actually usable at ``address``?

    Returns ``(True, browser_version)`` or ``(False, reason)``.

    WHY THIS EXISTS (round 17).  ``attach`` used to hand a dead address
    straight to ``webdriver.Chrome()``.  Selenium then resolves a driver
    binary and runs its own connection and timeout logic before failing with
    a generic message - and ``attach`` repeated that up to ``attempts``
    times with backoff.  One local socket round trip answers the only
    question that matters first, and costs nothing when Chrome IS up.

    STRICT VALIDATION (round 18 corrective).  The first version scanned the
    raw response for a ``"Browser"`` substring, so an HTTP 500 error page or
    a truncated body that happened to contain that word was accepted.  Every
    layer is now checked in order: loopback host, valid port, successful HTTP
    status, complete body, parsable JSON object, Chrome/Chromium identity,
    and DevTools WebSocket metadata.  Each failure names the actual problem,
    because "could not connect" sends the operator looking in the wrong
    place.
    """
    if address is None:
        address = resolve_debugger_address()
    try:
        host, port = validate_debugger_address(address)
    except ValueError as exc:
        return False, str(exc)

    deadline = time.monotonic() + max(0.1, float(timeout))
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.sendall(
                b"GET /json/version HTTP/1.1\r\n"
                b"Host: " + host.encode("ascii", "ignore") + b"\r\n"
                b"Accept: application/json\r\n"
                b"Connection: close\r\n\r\n")
            chunks = []
            total = 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                sock.settimeout(remaining)
                try:
                    chunk = sock.recv(8192)
                except socket.timeout:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > _MAX_PROBE_RESPONSE_BYTES:
                    break
    except OSError:
        return False, (
            f"nothing is listening on {address} - start Chrome in remote "
            f"debugging mode on port {port} before running the automation")

    raw = b"".join(chunks)
    if not raw:
        return False, (
            f"{address} accepted a connection but returned nothing - "
            f"check that this is Chrome's remote debugging port and not "
            f"another process holding it")

    # ---- HTTP status ----------------------------------------------------
    head, sep, body_bytes = raw.partition(b"\r\n\r\n")
    if not sep:
        return False, (
            f"{address} returned a truncated HTTP response with no complete "
            f"header section")
    header_text = head.decode("latin-1", "replace")
    status_line = header_text.split("\r\n", 1)[0]
    status_match = re.match(r"HTTP/\d(?:\.\d)?\s+(\d{3})", status_line)
    if not status_match:
        return False, (
            f"{address} did not answer with HTTP - it is not a DevTools "
            f"endpoint ({status_line.strip()!r})")
    status = int(status_match.group(1))
    if not 200 <= status < 300:
        return False, (
            f"{address} answered /json/version with HTTP status {status} - "
            f"the endpoint is not healthy")

    # ---- completeness ---------------------------------------------------
    declared = re.search(r"(?im)^Content-Length:\s*(\d+)\s*$", header_text)
    if declared and len(body_bytes) < int(declared.group(1)):
        return False, (
            f"{address} returned a truncated body "
            f"({len(body_bytes)} of {declared.group(1)} bytes) - the "
            f"response is incomplete")

    # ---- JSON -----------------------------------------------------------
    try:
        payload = json.loads(body_bytes.decode("utf-8", "replace"))
    except ValueError as exc:
        return False, (
            f"{address} did not return valid JSON from /json/version "
            f"({exc}) - another process is holding the port")
    if not isinstance(payload, dict):
        return False, (
            f"{address} returned JSON that is not a DevTools version object")

    # ---- Chrome identity -------------------------------------------------
    browser = str(payload.get("Browser") or "").strip()
    if not browser:
        return False, (
            f"{address} answered, but not as a Chrome DevTools endpoint "
            f"(no Browser field in /json/version) - another process is "
            f"holding the port")
    if not re.match(r"(?i)\s*(chrome|chromium|headlesschrome|edg)", browser):
        return False, (
            f"{address} is a DevTools endpoint but reports {browser!r}, "
            f"which is not Chrome/Chromium")

    # ---- DevTools metadata ----------------------------------------------
    socket_url = str(payload.get("webSocketDebuggerUrl") or "").strip()
    if not socket_url.startswith(("ws://", "wss://")):
        return False, (
            f"{address} reports {browser!r} but exposes no DevTools "
            f"WebSocket URL - the endpoint cannot accept a CDP session")

    return True, browser


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


#: Attach failures that will NEVER succeed on a retry.  Retrying these just
#: multiplies the operator's wait by `attempts` and then reports the same
#: fault.  Matched on the driver's own message because Selenium raises the
#: same exception type for recoverable and unrecoverable causes alike.
_PERMANENT_ATTACH_SIGNATURES = (
    "only supports chrome version",
    "this version of chromedriver",
    "unable to find chromedriver",
    "cannot find chrome binary",
    "no chrome binary",
    "unable to obtain",
    "executable needs to be in path",
    "unsupported chrome version",
)


def is_transient_attach_error(exc: BaseException) -> bool:
    """Is retrying this attach failure worth the operator's time?

    Conservative: anything we do not positively recognise as permanent is
    treated as transient, so an unfamiliar fault still gets its retries.
    """
    message = f"{exc}".lower()
    return not any(sig in message for sig in _PERMANENT_ATTACH_SIGNATURES)


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
               debugger_address: Optional[str] = None,
               attempts: int = 3, backoff: float = 0.6, **kwargs) -> "PortalSession":
        """Attach ONCE to the operator's already-authenticated Chrome.

        No login / MFA / cookie / profile automation is performed - the operator
        authenticates manually and this process only attaches to the existing
        debugging endpoint.
        """
        import selenium.webdriver as webdriver
        from selenium.webdriver.chrome.options import Options

        options = Options()
        if debugger_address is None:
            # One configuration source, shared with the Chrome launcher, so
            # the operator cannot start Chrome on one port while the
            # application waits on another.
            debugger_address = resolve_debugger_address()
        options.add_experimental_option("debuggerAddress", debugger_address)
        last_error: Optional[Exception] = None
        # Stage timings, so a slow start can be ATTRIBUTED instead of guessed
        # at.  These only read the clock around work that already happens -
        # no extra probe, no extra round trip, no added latency.
        attach_t0 = time.monotonic()

        # Pre-flight: answer "is the endpoint even there?" with one local
        # socket round trip instead of through Selenium's slow failure path.
        discovery_t0 = time.monotonic()
        endpoint_ok, endpoint_detail = probe_debugger_endpoint(debugger_address)
        discovery_ms = (time.monotonic() - discovery_t0) * 1000.0
        if not endpoint_ok:
            logger.error(f"[SESSION] endpoint check failed in {discovery_ms:.0f}ms: "
                         f"{endpoint_detail}")
            raise WebDriverException(
                f"Could not attach Chrome Debug at {debugger_address}: "
                f"{endpoint_detail}")
        logger.info(f"[SESSION] debugging endpoint alive in {discovery_ms:.0f}ms "
                    f"({endpoint_detail})")

        for attempt in range(1, attempts + 1):
            try:
                connect_t0 = time.monotonic()
                driver = webdriver.Chrome(options=options)
                connect_ms = (time.monotonic() - connect_t0) * 1000.0
                logger.info(f"[SESSION] CDP attached on {debugger_address} (attempt {attempt})")
                wrapper_t0 = time.monotonic()
                session = cls(driver, logger, **kwargs)
                # Honest label: this times Python wrapper construction only.
                # Nothing about the portal has been validated at this point -
                # no tab discovery, no patient check, no Treatment Plan.
                wrapper_ms = (time.monotonic() - wrapper_t0) * 1000.0
                # time spent locating a WORKING endpoint = everything before
                # the connect that finally succeeded (failed attempts+backoff)
                session.counters.endpoint_discovery_ms = max(
                    0.0, (connect_t0 - attach_t0) * 1000.0)
                session.counters.cdp_connect_ms = connect_ms
                session.counters.session_validate_ms = wrapper_ms
                logger.info(
                    f"[SESSION-TIMING] endpoint_discovery="
                    f"{session.counters.endpoint_discovery_ms:.0f}ms "
                    f"cdp_connect={connect_ms:.0f}ms "
                    f"wrapper_init={wrapper_ms:.0f}ms "
                    f"(attempt {attempt}/{attempts})")
                return session
            except Exception as exc:                        # noqa: BLE001
                last_error = exc
                logger.warn(f"[SESSION] CDP attach attempt {attempt}/{attempts} failed: {exc}")
                if not is_transient_attach_error(exc):
                    logger.error(
                        "[SESSION] this failure is permanent (driver/browser "
                        "mismatch or missing binary) - not retrying")
                    break
                if attempt < attempts:
                    time.sleep(backoff)
        logger.warn(
            f"[SESSION-TIMING] endpoint unreachable after "
            f"{(time.monotonic() - attach_t0) * 1000.0:.0f}ms "
            f"across {attempts} attempt(s)")
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
        # Only a REAL discovery is timed; the cached fast path must not be
        # reported as "context readiness" or the number becomes meaningless.
        discovering = force or not self._context_ready
        ready_t0 = time.monotonic() if discovering else None
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
        if ready_t0 is not None:
            self.counters.context_ready_ms = (time.monotonic() - ready_t0) * 1000.0
            self.logger.info(
                f"[SESSION-TIMING] context_ready={self.counters.context_ready_ms:.0f}ms "
                f"start_to_first_operation={self.elapsed_ms():.0f}ms")
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
        """Detach, and stop the local chromedriver SERVICE.

        ``quit()`` is deliberately NOT called: on a ``debuggerAddress``
        session that would close the operator's own Chrome window.  But the
        previous implementation only logged, so every batch left a
        ``chromedriver.exe`` running - the orphan-process leak.  Stopping the
        service terminates that child process and leaves the browser alone.

        Idempotent and never raises: shutdown must not be able to fail.
        """
        if getattr(self, "_closed", False):
            return
        self._closed = True

        service = getattr(getattr(self, "driver", None), "service", None)
        if service is None:
            service = getattr(self.raw_driver, "service", None)
        if service is not None:
            try:
                service.stop()
                self.logger.info("[SESSION] chromedriver service stopped")
            except Exception as exc:                        # noqa: BLE001
                self.logger.warn(f"[SESSION] chromedriver service "
                                 f"did not stop cleanly: {exc}")
        self.logger.info("[SESSION] detached (operator Chrome left running)")
