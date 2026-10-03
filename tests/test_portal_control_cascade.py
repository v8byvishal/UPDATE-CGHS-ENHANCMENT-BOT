"""The GP001 unit-19 cascade (task section 10, tests A-H).

The live failure, in the operator's own log order:

    element not interactable
    [FRAME-CACHE] invalidated
    [BROWSER] GP001 ... element not interactable
    FAILED_BEFORE_DISPATCH / TX-PORTAL-CONTEXT-LOST

...followed by the same four lines for all 24 remaining codes, each one
preceded by ``[CONTEXT] verified Treatment Plan`` - the engine proving, 25
times over, that the context it had just declared lost was in fact fine.

Three separate defects produced that:

1. ``ElementNotInteractableException`` is a ``WebDriverException`` subclass,
   so it fell into the clause that invalidates the frame cache and reports
   ``TX-PORTAL-CONTEXT-LOST``.  An element fault was reported as a context
   fault.
2. ``SmartDOMResolver.locate()`` ended with "return the first match, hidden
   or not, and cache the strategy that produced it".  After React remounted
   the control, that first match was the hidden clone - forever.
3. ``BatchRunner`` had no notion of a shared-control failure, so it attempted
   all 24 remaining codes against the identical broken control.

Every test below asserts observable portal/session state, never a return
value alone.
"""

from __future__ import annotations

import pytest
from selenium.common.exceptions import (
    ElementNotInteractableException,
    NoSuchElementException,
)

from cghs.orchestrator import BatchRunner, TreatmentPlanOrchestrator
from cghs.session import PortalSession
from cghs.telemetry import EnterpriseLogger
from cghs.txstate import Diagnostic
from tests.support.react_select_dom import (
    Option,
    ReactSelectConfig,
    ReactSelectDOM,
    build_react_portal,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _session(dom):
    session = PortalSession(dom, EnterpriseLogger(None))
    session.set_bill("CASCADE")
    session.ensure_context()
    return session


def _live_portal(**kwargs):
    """A portal that behaves like the real one: it clears the procedure
    after every Add, which is why the engine re-selects it for every unit
    and why a control fault can first appear at unit 19."""
    kwargs.setdefault("resets_after_add", ("procedure",))
    dom = build_react_portal(**kwargs)
    return dom, _session(dom)


class _FrameSpy:
    """Counts frame-cache invalidations without changing behaviour."""

    def __init__(self, session):
        self.count = 0
        self._real = session.frames.invalidate

        def spy(reason=""):
            self.count += 1
            return self._real(reason)

        session.frames.invalidate = spy


def _batch(dom, session, codes, gp001_qty=30):
    runner = BatchRunner(session, session.logger)
    items = [{"code": c, "qty": gp001_qty if c == "GP001" else 1} for c in codes]
    result = runner.run([{"name": "TEST PATIENT", "items": items}])
    return result.patients[0]


def _catalogue(codes):
    spec = {"LB": "Laboratory", "RI": "Radiology",
            "CN": "Consultation", "BL": "Blood"}
    return [Option(c, f"{c} - procedure {c}", spec.get(c[:2], "Consultation"))
            for c in codes]


# ---------------------------------------------------------------------------
# A. CACHE_FAILURE_AT_UNIT_19
# ---------------------------------------------------------------------------

def test_A_units_1_to_18_succeed_then_unit_19_hits_the_control_fault():
    """The exact live boundary, with the control gone for good."""
    dom, session = _live_portal(procedure_remount_after_units=18,
                                procedure_remount_recovers=False)
    spy = _FrameSpy(session)
    result = TreatmentPlanOrchestrator(session).process_item(
        {"code": "GP001", "qty": 30})

    # 18 units really were committed before the fault
    assert dom.plus_clicks == 18
    assert len(dom.rows) == 18
    assert result.success is False
    # ...and the failure names the CONTROL, not the context
    assert result.diagnostic == Diagnostic.CONTROL_UNAVAILABLE.value
    assert result.diagnostic != Diagnostic.CONTEXT_LOST.value
    assert result.shared_control_failure is True
    # the frame was never invalidated, because it was never invalid
    assert spy.count == 0


def test_A2_a_control_fault_is_never_reported_as_a_lost_context():
    dom, session = _live_portal(procedure_remount_after_units=2,
                                procedure_remount_recovers=False)
    result = TreatmentPlanOrchestrator(session).process_item(
        {"code": "GP001", "qty": 5})
    assert result.diagnostic == "TX-PROCEDURE-CONTROL-UNAVAILABLE"
    assert "CONTEXT-LOST" not in result.diagnostic
    assert "BROWSER" not in result.diagnostic


def test_A3_recovery_runs_once_not_once_per_remaining_unit():
    """One fault, one recovery attempt - not 12 more."""
    dom, session = _live_portal(procedure_remount_after_units=18,
                                procedure_remount_recovers=False)
    spy = _FrameSpy(session)
    before = session.counters.frame_discoveries
    TreatmentPlanOrchestrator(session).process_item({"code": "GP001", "qty": 30})
    assert session.counters.frame_discoveries - before == 0
    assert spy.count == 0


# ---------------------------------------------------------------------------
# B. RECOVERY_FINDS_WRONG_INPUT
# ---------------------------------------------------------------------------

def test_B_a_hidden_clone_is_never_chosen_over_the_visible_control():
    dom, session = _live_portal(hidden_duplicate_procedure_input=True)
    orch = TreatmentPlanOrchestrator(session)

    el = orch.proc_sel._acquire_interactable("PROCEDURE_INPUT")

    assert el.is_displayed() is True
    assert el.id != dom.hidden_clone["nid"]          # not the clone
    assert el.id == dom._parts("procedure")["input"]["nid"]   # the live one


def test_B2_the_whole_item_still_completes_with_a_clone_present():
    dom, session = _live_portal(hidden_duplicate_procedure_input=True)
    result = TreatmentPlanOrchestrator(session).process_item(
        {"code": "GP001", "qty": 3})
    assert result.success is True
    assert dom.plus_clicks == 3
    # (the portal clears the procedure after each Add, so assert the rows -
    #  they are the durable proof that the right control was driven)
    assert [r["code"] for r in dom.rows] == ["GP001"] * 3


def test_B3_resolver_refuses_to_hand_back_a_non_interactable_control():
    """The removed footgun: 'return els[0], hidden or not, and cache it'."""
    dom, session = _live_portal(procedure_remount_after_units=1,
                                procedure_remount_recovers=False)
    TreatmentPlanOrchestrator(session).process_item({"code": "GP001", "qty": 1})
    # the live input is now hidden but still matches every selector
    with pytest.raises((ElementNotInteractableException, NoSuchElementException)):
        session.resolver.locate("PROCEDURE_INPUT")


# ---------------------------------------------------------------------------
# C / G. React re-render changes the input's identity
# ---------------------------------------------------------------------------

def test_C_input_replaced_after_unit_18_is_reacquired_and_the_run_continues():
    dom, session = _live_portal(procedure_remount_after_units=18,
                                procedure_remount_recovers=True)
    spy = _FrameSpy(session)
    before_id = dom._parts("procedure")["input"]["nid"]

    result = TreatmentPlanOrchestrator(session).process_item(
        {"code": "GP001", "qty": 30})

    assert "procedure:remount" in dom.stage_events       # it really remounted
    assert dom._parts("procedure")["input"]["nid"] != before_id   # new identity
    assert result.success is True
    assert dom.plus_clicks == 30                         # all 30 units landed
    assert len(dom.rows) == 30
    assert spy.count == 0                                # no frame churn


def test_G_the_same_code_after_a_rerender_uses_the_current_control():
    dom, session = _live_portal(procedure_remount_after_units=2,
                                procedure_remount_recovers=True)
    orch = TreatmentPlanOrchestrator(session)
    first = orch.process_item({"code": "GP001", "qty": 4})
    assert first.success is True

    live_input = dom._parts("procedure")["input"]
    assert live_input["attrs"]["id"] == "react-select-9-input"   # renumbered
    # every row is still the right code - no wrong control was driven
    assert [r["code"] for r in dom.rows] == ["GP001"] * 4


# ---------------------------------------------------------------------------
# D. FRAME_STILL_VALID_PROCEDURE_INVALID
# ---------------------------------------------------------------------------

def test_D_a_valid_frame_is_not_invalidated_by_a_control_fault():
    dom, session = _live_portal(procedure_remount_after_units=3,
                                procedure_remount_recovers=False)
    spy = _FrameSpy(session)
    before_discoveries = session.counters.frame_discoveries

    result = TreatmentPlanOrchestrator(session).process_item(
        {"code": "GP001", "qty": 10})

    assert result.success is False
    assert spy.count == 0, "the frame cache was dumped for an element fault"
    assert session.counters.frame_discoveries == before_discoveries
    # and the frame really is still usable
    assert session.ensure_context() is not None


# ---------------------------------------------------------------------------
# E. SHARED_FAILURE_STOP
# ---------------------------------------------------------------------------

CODES_AFTER = ["LB001", "LB012", "LB055", "LB068", "RI001", "RI002", "RI005",
               "CN003", "CN004", "GP002", "GP003", "LB002", "LB003", "RI003",
               "CC003", "CC004", "CI002", "CI003", "BL001", "BL002", "LB004",
               "RI004"]
CODES_BEFORE = ["CN002", "CC001", "CC002", "CC008", "CC011", "CC012", "CI001"]


def test_E_one_shared_failure_stops_the_batch_and_preserves_the_rest():
    codes = CODES_BEFORE + ["GP001"] + CODES_AFTER
    dom = ReactSelectDOM(
        config=ReactSelectConfig(procedure_remount_after_units=18,
                                 resets_after_add=("procedure",),
                                 procedure_remount_recovers=False),
        catalogue=_catalogue(codes))
    session = _session(dom)
    patient = _batch(dom, session, codes)

    assert patient["status"] == "STOPPED_SHARED_PORTAL_CONTEXT_FAILURE"
    assert patient["completed"] == len(CODES_BEFORE)
    # ONE failure, not 23
    assert patient["failed"] == ["GP001"]
    # the untried codes are owed to the operator, not blamed
    assert patient["pending"] == CODES_AFTER
    assert len(patient["pending"]) == 22
    assert patient["shared_context_failure"]
    assert session.counters.shared_control_failures == 1


def test_E2_the_cascade_diagnostics_are_gone():
    """Pre-fix this produced 25x TX-PORTAL-CONTEXT-LOST + 25 cache dumps."""
    codes = CODES_BEFORE + ["GP001"] + CODES_AFTER
    dom = ReactSelectDOM(
        config=ReactSelectConfig(procedure_remount_after_units=18,
                                 resets_after_add=("procedure",),
                                 procedure_remount_recovers=False),
        catalogue=_catalogue(codes))
    session = _session(dom)
    spy = _FrameSpy(session)
    _batch(dom, session, codes)
    assert spy.count == 0
    assert session.counters.frame_discoveries <= 1


def test_E3_a_recoverable_control_lets_the_whole_batch_finish():
    codes = CODES_BEFORE + ["GP001"] + CODES_AFTER
    dom = ReactSelectDOM(
        config=ReactSelectConfig(procedure_remount_after_units=18,
                                 resets_after_add=("procedure",),
                                 procedure_remount_recovers=True),
        catalogue=_catalogue(codes))
    session = _session(dom)
    patient = _batch(dom, session, codes)

    assert patient["status"] == "COMPLETED"
    assert patient["failed"] == []
    assert patient["pending"] == []
    assert patient["completed"] == len(codes)
    # 30 locked GP001 units + one Add for every other code
    assert dom.plus_clicks == 30 + len(CODES_BEFORE) + len(CODES_AFTER)


# ---------------------------------------------------------------------------
# F. MULTIPLE_PROCEDURE_INPUTS
# ---------------------------------------------------------------------------

def test_F_exactly_one_visible_interactable_control_is_selected():
    dom, session = _live_portal(hidden_duplicate_procedure_input=True)
    matches = dom.find_elements("css selector", "#react-select-5-input")
    assert len(matches) == 2, "the test needs two matching inputs"
    assert sum(1 for m in matches if m.is_displayed()) == 1

    chosen = TreatmentPlanOrchestrator(session).proc_sel._acquire_interactable(
        "PROCEDURE_INPUT")
    assert chosen.is_displayed() and chosen.is_enabled()


def test_F2_the_speciality_control_is_never_mistaken_for_the_procedure():
    dom, session = _live_portal(procedure_remount_after_units=1,
                                procedure_remount_recovers=True)
    orch = TreatmentPlanOrchestrator(session)
    orch.process_item({"code": "GP001", "qty": 3})

    proc_input = dom._parts("procedure")["input"]["nid"]
    spec_input = dom._parts("speciality")["input"]["nid"]
    assert proc_input != spec_input
    chosen = orch.proc_sel._acquire_interactable("PROCEDURE_INPUT")
    assert chosen.id == proc_input       # not the speciality combobox


# ---------------------------------------------------------------------------
# H. STALE_ELEMENT
# ---------------------------------------------------------------------------

def test_H_a_stale_element_is_reacquired_without_destroying_the_session():
    dom, session = _live_portal(stale_after_type=True)
    spy = _FrameSpy(session)
    result = TreatmentPlanOrchestrator(session).process_item(
        {"code": "GP001", "qty": 2})

    assert result.success is True
    assert dom.plus_clicks == 2
    assert spy.count == 0                        # session left intact
    assert session.counters.frame_discoveries <= 1


# ---------------------------------------------------------------------------
# section 11 - performance of the recovery path
# ---------------------------------------------------------------------------

def test_recovery_adds_no_fixed_sleep():
    dom, session = _live_portal(procedure_remount_after_units=18,
                                procedure_remount_recovers=True)
    TreatmentPlanOrchestrator(session).process_item({"code": "GP001", "qty": 30})
    assert session.counters.fixed_sleep_ms == 0


def test_stopping_early_costs_far_less_than_grinding_the_queue():
    """The point of the guard: the doomed items are not even attempted."""
    codes = CODES_BEFORE + ["GP001"] + CODES_AFTER
    dom = ReactSelectDOM(
        config=ReactSelectConfig(procedure_remount_after_units=18,
                                 resets_after_add=("procedure",),
                                 procedure_remount_recovers=False),
        catalogue=_catalogue(codes))
    session = _session(dom)
    _batch(dom, session, codes)
    # 22 codes never touched the DOM at all
    assert dom.counters["send_keys"] > 0
    assert session.counters.fixed_sleep_ms == 0


# ---------------------------------------------------------------------------
# section 12 - nothing about business rules moved
# ---------------------------------------------------------------------------

def test_locked_quantity_remains_duplicate_safe_through_a_remount():
    dom, session = _live_portal(procedure_remount_after_units=18,
                                procedure_remount_recovers=True)
    result = TreatmentPlanOrchestrator(session).process_item(
        {"code": "GP001", "qty": 30})

    assert dom.plus_clicks == 30                 # never 31
    assert dom.plus_clicks_by_code["GP001"] == 30
    assert len(dom.rows) == 30
    ids = [tx.identity.transaction_id for tx in result.transactions]
    assert len(ids) == len(set(ids)) == 30
    for tx in result.transactions:
        assert tx.ledger.dispatch_count(tx.identity.transaction_id) == 1


# ---------------------------------------------------------------------------
# section 9 - frame validation needs more than "it has some inputs"
# ---------------------------------------------------------------------------

def test_frame_validation_rejects_a_frame_with_only_generic_inputs():
    """A login box in an iframe must NOT be accepted as the Treatment Plan.

    The baseline validated a browsing context with `bool(ctx["inputs"])`, so
    any frame containing any input passed - and binding to the wrong frame
    then surfaces later as an inexplicable control failure.
    """
    dom, session = _live_portal()
    frames = session.frames
    real_probe = dom._probe

    def generic_only(spec):
        state = real_probe(spec)
        if "ctx" in (spec or {}).get("fields", []):
            # plenty of inputs, no Treatment Plan signature whatsoever
            state["ctx"] = {"inputs": 9, "procedure": False, "speciality": False,
                            "quantity": False, "reason": False, "plus": False,
                            "marker": False}
        return state

    dom._probe = generic_only
    assert frames._cheap_validate() is False

    dom._probe = real_probe
    assert frames._cheap_validate() is True


def test_frame_validation_needs_more_than_one_signature():
    dom, session = _live_portal()
    frames = session.frames
    real_probe = dom._probe

    def one_signature(spec):
        state = real_probe(spec)
        if "ctx" in (spec or {}).get("fields", []):
            state["ctx"] = {"inputs": 4, "procedure": False, "speciality": False,
                            "quantity": False, "reason": False, "plus": False,
                            "marker": True}        # only the heading
        return state

    dom._probe = one_signature
    assert frames._cheap_validate() is False
