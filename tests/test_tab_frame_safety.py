"""Patient tab, frame and identity safety.

The forbidden shortcut in this project is ``window_handles[0]``: it silently
picks "whatever tab Chrome happens to list first", which can be another
patient.  These tests prove the resolver uses evidence and stops when the
evidence is ambiguous.
"""

from __future__ import annotations

import pathlib

import pytest

from cghs.dom import PortalContextLost
from cghs.tabs import AmbiguousPatientTab, NoPatientTab, PatientNotSwitched
from cghs.txstate import TxState
from tests.support.fake_portal import Latency, build_portal
from tests.support.harness import item, make_orchestrator, make_session

REPO = pathlib.Path(__file__).resolve().parents[1]

DECOY = {"handle": "TAB-DECOY", "url": "https://portal.example/other",
         "title": "Other Page", "treatment_plan": False, "controls": False,
         "patient": "SOMEONE ELSE", "ip": "IP-ZZZ", "bill": "BILL-ZZZ"}

SECOND_PLAN = {"handle": "TAB-SECOND", "url": "https://portal.example/plan2",
               "title": "Treatment Plan", "treatment_plan": True, "controls": True,
               "patient": "ANOTHER PATIENT", "ip": "IP-YYY", "bill": "BILL-YYY"}


def test_window_handles_index_zero_is_never_executed():
    """Static guarantee - no statement may index window_handles positionally.

    Comments and docstrings are allowed to name the forbidden pattern (the
    code documents why it is forbidden); executable subscripts are not.
    """
    import ast

    offenders = []
    for path in sorted((REPO / "cghs").rglob("*.py")) + [REPO / "app.py"]:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Subscript):
                continue
            target = node.value
            name = getattr(target, "attr", None) or getattr(target, "id", None)
            if name == "window_handles":
                offenders.append(f"{path.relative_to(REPO)}:{node.lineno}")
    assert offenders == []


def test_decoy_tabs_do_not_confuse_the_resolver():
    portal = build_portal(["LB012"], latency=Latency.fast(), extra_tabs=[DECOY])
    portal.current_handle = "TAB-DECOY"
    session = make_session(portal)
    evidence = session.ensure_context()
    assert evidence.handle == "TAB-MAIN"
    assert evidence.patient_name


def test_two_valid_treatment_plan_tabs_stop_the_run():
    portal = build_portal(["LB012"], latency=Latency.fast(), extra_tabs=[SECOND_PLAN])
    session = make_session(portal)
    with pytest.raises(AmbiguousPatientTab) as excinfo:
        session.ensure_context()
    message = str(excinfo.value)
    assert "AMBIGUOUS PATIENT TAB" in message
    # the operator gets the evidence needed to choose
    assert "TAB-MAIN" in message and "TAB-SECOND" in message
    assert portal.plus_clicks == []


def test_ambiguous_tabs_abort_the_item_without_any_mutation():
    portal = build_portal(["LB012"], latency=Latency.fast(), extra_tabs=[SECOND_PLAN])
    session, orch = make_orchestrator(portal)
    result = orch.process_item(item("LB012", 1))
    assert result.state == TxState.FAILED_BEFORE_DISPATCH.value
    assert result.diagnostic == "TX-AMBIGUOUS-PATIENT-TAB"
    assert portal.plus_clicks == []


def test_no_treatment_plan_tab_at_all_is_reported_precisely():
    portal = build_portal(["LB012"], latency=Latency.fast())
    portal.config.treatment_plan_present = False
    session = make_session(portal)
    with pytest.raises((NoPatientTab, PortalContextLost)):
        session.ensure_context()


def test_an_empty_treatment_plan_table_is_still_a_valid_tab():
    """Virtualized grids start empty; that must not disqualify the tab."""
    portal = build_portal(["LB012"], latency=Latency.fast())
    portal.config.faults.virtualized_table = True
    session, orch = make_orchestrator(portal)
    result = orch.process_item(item("LB012", 1))
    assert result.state == TxState.COMPLETED.value


def test_cached_tab_is_revalidated_not_blindly_reused():
    portal = build_portal(["LB012", "CN002"], latency=Latency.fast())
    session, orch = make_orchestrator(portal)
    orch.process_item(item("LB012", 1))
    scans_after_first = portal.counters["window_handles"]

    orch.process_item(item("CN002", 1))
    assert portal.counters["window_handles"] == scans_after_first   # no rescan
    assert session.counters.tab_cache_hits >= 1


def test_identity_change_mid_bill_is_detected_before_trusting_rows():
    portal = build_portal(["LB012"], latency=Latency.fast())
    session, orch = make_orchestrator(portal)
    session.ensure_context()
    assert session.verify_identity_unchanged()[0] is True

    portal.switch_patient("SOMEONE ELSE", ip_case="IP-NEW", bill_number="BILL-NEW")
    ok, reason = session.verify_identity_unchanged()
    assert ok is False
    assert "changed" in reason


def test_unverifiable_identity_turns_an_unknown_commit_into_context_lost():
    portal = build_portal(["LB012"], latency=Latency.fast())
    portal.config.faults.commit_never = {"LB012"}
    session, orch = make_orchestrator(portal, commit_timeout=0.25, reconcile_grace=0.2)

    original = portal._handle_plus

    def swap_patient_after_click():
        result = original()
        portal.switch_patient("SOMEONE ELSE", ip_case="IP-NEW", bill_number="BILL-NEW")
        return result

    portal._handle_plus = swap_patient_after_click
    result = orch.process_item(item("LB012", 1))

    assert result.success is False
    assert result.state == TxState.RECONCILIATION_REQUIRED.value
    assert result.transactions[-1].diagnostic.value == "TX-PORTAL-CONTEXT-LOST"


def test_frame_discovery_finds_controls_inside_an_iframe():
    portal = build_portal(["LB012"], latency=Latency.fast())
    portal.config.controls_in_frame = 1          # controls live in iframe #1
    session, orch = make_orchestrator(portal)
    result = orch.process_item(item("LB012", 1))
    assert result.state == TxState.COMPLETED.value
    assert portal.current_frame == 1
    assert session.counters.frame_discoveries == 1


def test_patient_not_switched_is_a_distinct_stop_condition():
    portal = build_portal(["LB012"], latency=Latency.fast())
    portal.switch_patient("PATIENT A", ip_case="IP-A", bill_number="BILL-A")
    session = make_session(portal)
    session.set_bill("bill-a", expected_patient="PATIENT A")
    session.ensure_context()

    session.set_bill("bill-b", expected_patient="PATIENT B")
    with pytest.raises(PatientNotSwitched) as excinfo:
        session.ensure_context()
    assert "nothing was written" in str(excinfo.value)


# ---------------------------------------------------------------------------
# ROUND 15 - BUG #1: the identity gate must be FAIL-CLOSED.
#
# Reproduced before the fix (see the report, section "defects reproduced"):
# verify_identity_unchanged() approved the context in three situations where
# the patient could NOT actually be re-proved.  The gate guards a MUTATION, so
# an unprovable context must stop the run, never continue it.
#
# These tests drive the real PortalSession method with a scripted driver; no
# production function is mocked out.
# ---------------------------------------------------------------------------

from selenium.common.exceptions import WebDriverException      # noqa: E402

from cghs.dom import ProbeUnsupported                          # noqa: E402
from cghs.session import PortalIdentity, PortalSession         # noqa: E402
from cghs.telemetry import EnterpriseLogger                    # noqa: E402

#: a context that WAS verified against real patient evidence
VERIFIED_WITH_EVIDENCE = PortalIdentity(
    identity_key="IP-A|BILL-A|PATIENT A", patient_name="PATIENT A",
    ip_case="IP-A", bill_number="BILL-A",
    url="https://portal.example/plan", title="Treatment Plan")

#: a portal that genuinely never exposed patient evidence at all
VERIFIED_WITHOUT_EVIDENCE = PortalIdentity(
    identity_key="||", url="https://portal.example/plan", title="Treatment Plan")


class _ScriptedDriver:
    """Stands in for PortalDriver with a scriptable probe outcome."""

    def __init__(self, probe_result=None, probe_raises=None, url="", title=""):
        self._probe_result = probe_result
        self._probe_raises = probe_raises
        self.current_url = url
        self.title = title
        self.probe_calls = 0

    def probe(self, fields, token=None):
        self.probe_calls += 1
        if self._probe_raises is not None:
            raise self._probe_raises
        return self._probe_result


def _session_with(identity, driver):
    session = PortalSession.__new__(PortalSession)     # no CDP attach in a test
    session.identity = identity
    session.driver = driver
    session.logger = EnterpriseLogger()
    return session


def _probe_ok(name="", ip="", bill="", url="https://portal.example/plan",
              title="Treatment Plan"):
    return {"ok": True,
            "patient": {"name": name, "ip": ip, "bill": bill},
            "signature": {"url": url, "title": title}}


def test_A_identity_gate_passes_when_the_same_patient_is_re_read():
    session = _session_with(VERIFIED_WITH_EVIDENCE,
                            _ScriptedDriver(_probe_ok("PATIENT A", "IP-A", "BILL-A")))
    ok, reason = session.verify_identity_unchanged()
    assert ok is True, reason
    assert "matched" in reason


def test_B_identity_gate_fails_when_the_patient_changed():
    session = _session_with(VERIFIED_WITH_EVIDENCE,
                            _ScriptedDriver(_probe_ok("PATIENT B", "IP-B", "BILL-B")))
    ok, reason = session.verify_identity_unchanged()
    assert ok is False
    assert "changed" in reason


def test_C_identity_gate_fails_when_patient_evidence_vanished_but_url_is_same():
    """The portal answers, the URL/title are identical - and the patient is gone.

    Pre-fix this returned ``(True, 'patient evidence matched')``: the loop
    required BOTH sides to be non-empty, so when every field came back empty
    not one comparison ran and the function reported a match it had never made.
    """
    session = _session_with(VERIFIED_WITH_EVIDENCE, _ScriptedDriver(_probe_ok()))
    ok, reason = session.verify_identity_unchanged()
    assert ok is False, reason
    assert "could not be re-read" in reason or "no patient evidence" in reason
    assert "matched" not in reason


def test_C2_identity_gate_still_passes_on_a_partially_readable_identity():
    """Weaker evidence is DISCLOSED, not silently upgraded or rejected.

    IP and bill are unreadable but the name is re-read and matches, so
    deterministic patient evidence does exist.  The directive requires a fail
    only when identity evidence *cannot be re-read*; the reason string must
    name exactly which fields carried the proof.
    """
    session = _session_with(VERIFIED_WITH_EVIDENCE,
                            _ScriptedDriver(_probe_ok(name="PATIENT A")))
    ok, reason = session.verify_identity_unchanged()
    assert ok is True, reason
    assert "patient_name" in reason
    assert "ip_case" in reason and "bill_number" in reason   # named as unreadable


def test_D_identity_gate_fails_when_the_probe_dies_and_evidence_was_required():
    """URL + title are NOT patient identity.

    Pre-fix the ProbeUnsupported branch returned True whenever the URL and
    title were unchanged, even though the context had been verified against
    real patient evidence that could no longer be read at all.
    """
    session = _session_with(
        VERIFIED_WITH_EVIDENCE,
        _ScriptedDriver(probe_raises=ProbeUnsupported("probe returned None"),
                        url="https://portal.example/plan", title="Treatment Plan"))
    ok, reason = session.verify_identity_unchanged()
    assert ok is False, reason
    assert "url" not in reason.lower() or "patient" in reason.lower()
    assert "matched" not in reason


def test_E_identity_gate_fails_when_the_browser_is_unreachable():
    session = _session_with(VERIFIED_WITH_EVIDENCE,
                            _ScriptedDriver(probe_raises=WebDriverException("disconnected")))
    ok, reason = session.verify_identity_unchanged()
    assert ok is False
    assert "unreachable" in reason


def test_E2_identity_gate_fails_when_the_fallback_cannot_read_url_either():
    class _Dead(_ScriptedDriver):
        @property
        def current_url(self):
            raise WebDriverException("disconnected")

        @current_url.setter
        def current_url(self, value):
            pass

    session = _session_with(VERIFIED_WITHOUT_EVIDENCE,
                            _Dead(probe_raises=ProbeUnsupported("no probe")))
    ok, reason = session.verify_identity_unchanged()
    assert ok is False
    assert "unreachable" in reason


def test_F_a_portal_that_never_exposed_patient_evidence_keeps_its_fallback():
    """Gate F: the genuinely safe fallback must survive the fix.

    Nothing stronger was ever available for this context, so a stable page
    signature is the best evidence that exists and the batch must not be
    bricked.  The reason says so explicitly.
    """
    session = _session_with(VERIFIED_WITHOUT_EVIDENCE, _ScriptedDriver(_probe_ok()))
    ok, reason = session.verify_identity_unchanged()
    assert ok is True, reason
    assert "no patient evidence" in reason


def test_F2_probe_unsupported_without_prior_evidence_keeps_the_signature_fallback():
    session = _session_with(
        VERIFIED_WITHOUT_EVIDENCE,
        _ScriptedDriver(probe_raises=ProbeUnsupported("no probe"),
                        url="https://portal.example/plan", title="Treatment Plan"))
    ok, reason = session.verify_identity_unchanged()
    assert ok is True, reason


def test_F3_signature_fallback_still_fails_on_a_changed_url_or_title():
    session = _session_with(
        VERIFIED_WITHOUT_EVIDENCE,
        _ScriptedDriver(probe_raises=ProbeUnsupported("no probe"),
                        url="https://portal.example/SOMEWHERE-ELSE",
                        title="Treatment Plan"))
    ok, reason = session.verify_identity_unchanged()
    assert ok is False
    assert "url changed" in reason


def test_the_identity_gate_never_invents_a_dom_source():
    """No new selector, cookie, token or login path may appear in the fix.

    Comments and docstrings are stripped first: ``session.py`` legitimately
    *documents* that no cookie/MFA/profile automation is performed, and a raw
    substring search would read that promise as a violation of itself.
    """
    import io
    import tokenize

    source = (REPO / "cghs" / "session.py").read_text(encoding="utf-8")
    code = []
    previous = tokenize.INDENT
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            continue
        if token.type == tokenize.STRING and previous in (
                tokenize.INDENT, tokenize.DEDENT, tokenize.NEWLINE, tokenize.NL):
            continue                      # a docstring, not an expression
        if token.type not in (tokenize.NL, tokenize.NEWLINE):
            previous = token.type
        code.append(token.string)
    executable = "\n".join(code)

    for forbidden in ("cookie", "localStorage", "sessionStorage",
                      "password", "find_element"):
        assert forbidden not in executable, forbidden
