"""Post-Plus Procedure control resolution (task section 17, tests A-L).

The live Windows failure this file pins down:

    CN002  -> procedure selected -> speciality verified -> quantity
              committed -> Plus dispatched -> Plus confirmed -> COMPLETED
    C001   -> TYPE_PROCEDURE -> CONTROL-UNAVAILABLE
           -> one controlled recovery
           -> Treatment Plan context is VALID
           -> Procedure control is still not interactable
           -> STOP shared portal control, remaining items PENDING

The cascade protection behaved correctly; what failed is upstream of it.
After the Plus, React re-renders the Procedure field: the spent react-select
container stays in the document (hidden) and the replacement is mounted
immediately AFTER it, with a NEW instance number.  Every registered strategy
was anchored to a POSITION or to an INSTANCE:

    #react-select-5-input                      -> the spent input
    aria-controls*='react-select-5'            -> the spent input
    following::div[...-container...][1]//input -> the spent CONTAINER
    following::input[1]                        -> the spent input
    //input[contains(@id,'procedure')]         -> react-select-9-input
                                                  does not contain the word

so the live control was never a candidate at all.  Every selector still
matched the hidden clone, which is why the engine reported "present but not
interactable" rather than "absent" - and why one recovery could not help:
re-running the identical position-anchored lookup produced the identical
dead element.

Each test asserts observable portal/session state, never a return value
alone.
"""

from __future__ import annotations

import pytest
from selenium.common.exceptions import (
    ElementNotInteractableException,
    NoSuchElementException,
    StaleElementReferenceException,
)

from cghs.dom import AmbiguousControlException
from cghs.locators import AMBIGUOUS_STRATEGIES, LOCATORS
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
    session.set_bill("POSTPLUS")
    session.ensure_context()
    return session


def _portal(catalogue=None, **kwargs):
    """A portal that clears the procedure after every Add, like the real one."""
    kwargs.setdefault("resets_after_add", ("procedure",))
    config = ReactSelectConfig(**kwargs)
    dom = (ReactSelectDOM(config=config, catalogue=catalogue)
           if catalogue is not None else ReactSelectDOM(config=config))
    return dom, _session(dom)


def _catalogue(codes):
    spec = {"LB": "Laboratory", "RI": "Radiology",
            "CN": "Consultation", "BL": "Blood"}
    return [Option(c, f"{c} - procedure {c}", spec.get(c[:2], "Consultation"))
            for c in codes]


def _rerendered(**kwargs):
    """A portal already in the post-Plus state: spent container retained and
    hidden, replacement mounted after it under a new instance number."""
    kwargs.setdefault("procedure_remount_after_units", 1)
    kwargs.setdefault("procedure_remount_new_container", True)
    dom, session = _portal(**kwargs)
    dom.plus_clicks = kwargs["procedure_remount_after_units"]
    dom._maybe_remount_procedure()
    return dom, session


def _batch(session, codes, qty=1):
    runner = BatchRunner(session, session.logger)
    items = [{"code": c, "qty": qty.get(c, 1) if isinstance(qty, dict) else qty}
             for c in codes]
    return runner.run([{"name": "TEST PATIENT", "items": items}]).patients[0]


def _live_input_id(dom):
    return dom._parts("procedure")["input"]["attrs"]["id"]


def _resolve(session):
    el, _ = session.resolver.locate("PROCEDURE_INPUT")
    return el


def _drive_neighbours(session):
    """Resolve Speciality and Reason once, as the engine does on every item.

    The resolver recognises a neighbouring control from the identity it
    established while DRIVING that control, which costs no DOM round trip.
    A bare locate() in a test skips that, so these tests reproduce the real
    call order instead of a shortcut the engine never takes.
    """
    session.resolver.locate("SPECIALITY_INPUT")
    session.resolver.locate("REASON_DROPDOWN")


# ---------------------------------------------------------------------------
# A. CN002 completes, then C001 resolves the CURRENT procedure control
# ---------------------------------------------------------------------------

def test_A_c001_after_cn002_resolves_the_current_procedure_control():
    """The exact reproduction boundary from task section 4."""
    dom, session = _portal(catalogue=_catalogue(["CN002", "C001"]),
                           procedure_remount_after_units=6,
                           procedure_remount_new_container=True)
    patient = _batch(session, ["CN002", "C001"], qty={"CN002": 6, "C001": 2})

    assert patient["status"] == "COMPLETED", patient
    assert patient["failed"] == []
    assert patient["pending"] == []
    # the portal really holds both: 6 + 2 units committed
    assert dom.plus_clicks == 8
    assert len(dom.rows) == 8
    assert {r["code"] for r in dom.rows} == {"CN002", "C001"}


# ---------------------------------------------------------------------------
# B. the same, for an ordinary next code
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("second", ["LB012", "RI001", "BL004"])
def test_B_any_next_code_resolves_the_current_procedure_control(second):
    dom, session = _portal(catalogue=_catalogue(["CN002", second]),
                           procedure_remount_after_units=1,
                           procedure_remount_new_container=True)
    patient = _batch(session, ["CN002", second])

    assert patient["status"] == "COMPLETED", patient
    assert patient["failed"] == []
    assert {r["code"] for r in dom.rows} == {"CN002", second}


# ---------------------------------------------------------------------------
# C. the instance number changes; the old one must not be required
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("instance", [6, 8, 9, 14, 23])
def test_C_resolution_is_react_select_instance_independent(instance):
    dom, session = _rerendered(procedure_remount_instance=instance)
    live = _live_input_id(dom)
    assert live == f"react-select-{instance}-input"

    el = _resolve(session)
    assert el._live()["attrs"]["id"] == live


def test_C2_no_registered_strategy_hardcodes_the_live_instance_number():
    """A fix that just bumped 5 -> 9 would satisfy the test above by luck.

    At least one strategy must reach the control through structure alone, so
    resolution still works for an instance number nobody wrote down.
    """
    dom, session = _rerendered(procedure_remount_instance=31)
    el = _resolve(session)
    assert el._live()["attrs"]["id"] == "react-select-31-input"

    hardcoded = [v for _, v in LOCATORS["PROCEDURE_INPUT"] if "react-select-31" in v]
    assert hardcoded == []


# ---------------------------------------------------------------------------
# D. a hidden clone must never be selected
# ---------------------------------------------------------------------------

def test_D_hidden_clone_is_never_selected():
    dom, session = _rerendered()
    spent = dom.nodes[[n for n, v in dom.nodes.items()
                       if v.get("attrs", {}).get("id") == "react-select-5-input"][0]]
    assert spent.get("visible") is False, "the spent clone must still be present"

    el = _resolve(session)
    assert el._live()["attrs"]["id"] != "react-select-5-input"
    assert el._live().get("visible") is not False


def test_D2_hidden_clone_present_from_the_start_is_never_selected():
    dom, session = _portal(hidden_duplicate_procedure_input=True)
    el = _resolve(session)
    assert el._live().get("visible") is not False
    assert el._live().get("rect", (1, 1)) != (0, 0)


# ---------------------------------------------------------------------------
# E. displayed is not the same as drivable
# ---------------------------------------------------------------------------

def test_E_zero_area_replacement_is_rejected_as_a_usable_control():
    """is_displayed() is True and is_enabled() is True, but the box has no
    area: Selenium cannot type into it.  Accepting it would raise
    ElementNotInteractable AFTER the control had been clicked."""
    dom, session = _rerendered(procedure_remount_zero_area=True)
    _drive_neighbours(session)
    replacement = dom._parts("procedure")["input"]
    assert replacement.get("rect") == (0, 0)

    with pytest.raises(ElementNotInteractableException):
        session.resolver.locate("PROCEDURE_INPUT")


def test_E2_a_usable_control_behind_a_zero_area_clone_is_still_found():
    """Rejecting the dead one must not mean giving up: keep looking."""
    dom, session = _rerendered(procedure_remount_zero_area=True)
    dead = dom._parts("procedure")["input"]
    good = dom._mk("input", parent=dead["parent"],
                   attrs={"id": "react-select-10-input", "role": "combobox",
                          "aria-controls": "react-select-10-listbox",
                          "aria-owns": "react-select-10-listbox"})
    dom._parts("procedure")["input"] = good

    assert _resolve(session)._live()["attrs"]["id"] == "react-select-10-input"


# ---------------------------------------------------------------------------
# F. never resolve to a neighbouring combobox
# ---------------------------------------------------------------------------

def test_F_procedure_never_resolves_to_speciality_or_reason():
    dom, session = _rerendered()
    procedure = _resolve(session)
    speciality, _ = session.resolver.locate("SPECIALITY_INPUT")
    reason, _ = session.resolver.locate("REASON_DROPDOWN")

    assert len({procedure.id, speciality.id, reason.id}) == 3
    assert procedure._live()["attrs"]["id"] == _live_input_id(dom)


def test_F2_with_the_procedure_control_gone_a_neighbour_is_not_a_substitute():
    """The regression this fix had to survive.

    With the procedure control gone, the broad instance-independent
    strategies still match - the Reason combobox is also "an input after the
    Procedure label".  Returning it would type a procedure code into the
    Enhancement Reason field.
    """
    dom, session = _portal(procedure_remount_after_units=1,
                           procedure_remount_recovers=False)
    _drive_neighbours(session)
    dom.plus_clicks = 1
    dom._maybe_remount_procedure()

    with pytest.raises(ElementNotInteractableException):
        session.resolver.locate("PROCEDURE_INPUT")


def test_F3_two_equally_valid_live_candidates_are_reported_not_guessed():
    """Section 11: never silently return candidates[0]."""
    dom, session = _rerendered()
    live = dom._parts("procedure")["input"]
    twin = dom._mk("input", parent=live["parent"],
                   attrs={"id": "react-select-12-input", "role": "combobox",
                          "aria-controls": "react-select-12-listbox",
                          "aria-owns": "react-select-12-listbox"})
    assert twin["nid"] != live["nid"]

    with pytest.raises(AmbiguousControlException):
        session.resolver.locate("PROCEDURE_INPUT")


def test_F4_ambiguity_is_an_element_fault_not_a_context_fault():
    assert issubclass(AmbiguousControlException, ElementNotInteractableException)


# ---------------------------------------------------------------------------
# G. a stale element is re-acquired, not reused
# ---------------------------------------------------------------------------

def test_G_stale_procedure_element_is_reacquired():
    dom, session = _portal()
    first = _resolve(session)
    first_id = first.id

    # React throws the element away and mounts a replacement
    dom.plus_clicks = 1
    dom.config.procedure_remount_after_units = 1
    dom.config.procedure_remount_new_container = True
    dom._maybe_remount_procedure()
    dom._detach(dom.nodes[first_id])

    with pytest.raises(StaleElementReferenceException):
        first.is_displayed()

    again = _resolve(session)
    assert again.id != first_id
    assert again._live()["attrs"]["id"] == _live_input_id(dom)


def test_G2_a_poisoned_cached_strategy_cannot_outlive_the_element():
    """Section 10: the cache stores a STRATEGY, and that strategy has to be
    able to rediscover the control after a re-render."""
    dom, session = _portal()
    _resolve(session)
    cached_before = dict(session.resolver._strategy_cache)
    assert "PROCEDURE_INPUT" in cached_before

    dom.plus_clicks = 1
    dom.config.procedure_remount_after_units = 1
    dom.config.procedure_remount_new_container = True
    dom._maybe_remount_procedure()

    assert _resolve(session)._live()["attrs"]["id"] == _live_input_id(dom)


# ---------------------------------------------------------------------------
# H. genuinely absent is a CONTROL failure, not a FRAME failure
# ---------------------------------------------------------------------------

def test_H_absent_procedure_control_is_a_control_failure():
    dom, session = _portal()
    _drive_neighbours(session)
    parts = dom._parts("procedure")
    dom._detach(parts["input"])
    dom._detach(dom.containers["procedure"])

    with pytest.raises((NoSuchElementException, ElementNotInteractableException)):
        session.resolver.locate("PROCEDURE_INPUT")


def test_H2_item_failure_names_the_control_never_the_context():
    dom, session = _portal(procedure_remount_after_units=1,
                           procedure_remount_recovers=False)
    result = TreatmentPlanOrchestrator(session).process_item(
        {"code": "GP001", "qty": 30})

    assert result.success is False
    assert result.diagnostic == Diagnostic.CONTROL_UNAVAILABLE.value
    assert result.diagnostic != Diagnostic.CONTEXT_LOST.value


# ---------------------------------------------------------------------------
# I. the frame cache survives a control fault
# ---------------------------------------------------------------------------

def test_I_frame_cache_is_not_dumped_for_a_control_fault():
    dom, session = _portal(procedure_remount_after_units=1,
                           procedure_remount_recovers=False)
    invalidations = []
    real = session.frames.invalidate

    def spy(reason=""):
        invalidations.append(reason)
        return real(reason)

    session.frames.invalidate = spy

    TreatmentPlanOrchestrator(session).process_item({"code": "GP001", "qty": 30})

    assert invalidations == []
    assert session.counters.frame_discoveries <= 1, \
        "no frame re-discovery storm for a control fault"


# ---------------------------------------------------------------------------
# J / K. recovery stays at exactly one attempt
# ---------------------------------------------------------------------------

def test_J_failed_recovery_leaves_the_rest_of_the_queue_pending():
    dom, session = _portal(catalogue=_catalogue(["CN002", "C001", "LB012", "RI001"]),
                           procedure_remount_after_units=1,
                           procedure_remount_recovers=False)
    patient = _batch(session, ["CN002", "C001", "LB012", "RI001"])

    assert patient["status"] == "STOPPED_SHARED_PORTAL_CONTEXT_FAILURE"
    assert patient["completed"] == 1
    assert patient["failed"] == ["C001"], "exactly one failure, not three"
    assert patient["pending"] == ["LB012", "RI001"], \
        "untried codes are owed to the operator, not blamed"
    assert patient["shared_context_failure"]
    assert session.counters.shared_control_failures == 1


def test_K_successful_recovery_continues_normally():
    dom, session = _portal(catalogue=_catalogue(["CN002", "C001", "LB012"]),
                           procedure_remount_after_units=1,
                           procedure_remount_new_container=True)
    patient = _batch(session, ["CN002", "C001", "LB012"])

    assert patient["status"] == "COMPLETED"
    assert patient["failed"] == []
    assert patient["pending"] == []
    assert patient["completed"] == 3
    assert len(dom.rows) == 3


# ---------------------------------------------------------------------------
# L. the locked-quantity guarantee is untouched
# ---------------------------------------------------------------------------

def test_L_gp001_locked_qty30_still_commits_exactly_thirty_units():
    dom, session = _portal(catalogue=_catalogue(["GP001"]))
    result = TreatmentPlanOrchestrator(session).process_item(
        {"code": "GP001", "qty": 30})

    assert result.success is True
    assert dom.plus_clicks == 30, "exactly 30 Plus clicks - no duplicates"
    assert len(dom.rows) == 30
    assert all(r["code"] == "GP001" for r in dom.rows)
    assert session.counters.fixed_sleep_ms == 0


def test_L2_gp001_survives_a_rerender_midway_without_duplicating_a_plus():
    dom, session = _portal(catalogue=_catalogue(["GP001"]),
                           procedure_remount_after_units=19,
                           procedure_remount_new_container=True)
    result = TreatmentPlanOrchestrator(session).process_item(
        {"code": "GP001", "qty": 30})

    assert result.success is True
    assert dom.plus_clicks == 30
    assert len(dom.rows) == 30


# ---------------------------------------------------------------------------
# section 7: one authoritative definition of the procedure control
# ---------------------------------------------------------------------------

def test_probe_and_resolver_share_one_candidate_universe():
    """The compact probe used to be built from XPath strategies only, so it
    answered questions about a strictly smaller candidate set than the
    resolver searched - two competing definitions of the same control."""
    from cghs.dom import _PROBE_QUERIES

    for key, strategies in LOCATORS.items():
        probe = _PROBE_QUERIES[key]
        assert len(probe) == len(strategies), key
        assert [v for _, v in strategies] == [q[1] for q in probe], key
        assert {q[0] for q in probe} <= {"css", "xpath"}, key

    assert any(q[0] == "css" for q in _PROBE_QUERIES["PROCEDURE_INPUT"]), \
        "CSS strategies must reach the probe, not be silently dropped"


def test_arbitrated_strategies_are_declared_and_registered():
    registered = {v for _, v in LOCATORS["PROCEDURE_INPUT"]}
    assert AMBIGUOUS_STRATEGIES
    assert AMBIGUOUS_STRATEGIES <= registered, \
        "an arbitrated strategy that is not registered arbitrates nothing"

    # every label-relative strategy is arbitrated: positional or not, it can
    # land on a neighbouring control once the procedure control is gone
    label_relative = {v for v in registered if "following::" in v}
    assert label_relative == AMBIGUOUS_STRATEGIES

    # ...and at least one of them must be free of a positional predicate,
    # otherwise the registry is still pinned to "the first one after the
    # label" and a re-render can still hide the live control from it
    assert any("[1]" not in v for v in AMBIGUOUS_STRATEGIES)


# ---------------------------------------------------------------------------
# section 18: arbitration must not tax the happy path
# ---------------------------------------------------------------------------

def test_neighbour_recognition_costs_no_dom_round_trips():
    """Once a neighbour has been driven, recognising it again is a set
    lookup - the browser is not asked a second time."""
    dom, session = _portal()
    _drive_neighbours(session)
    before = dict(dom.counters)
    _resolve(session)
    after = dict(dom.counters)

    recorded = session.resolver._resolved_identity
    assert "SPECIALITY_INPUT" in recorded and "REASON_DROPDOWN" in recorded
    assert after["find_elements"] - before["find_elements"] <= 2, \
        "resolving the procedure control re-queried the neighbours"


def test_remembered_identities_never_outlive_the_dom_they_describe():
    dom, session = _portal()
    _drive_neighbours(session)
    assert session.resolver._resolved_identity

    session.resolver.invalidate()
    assert session.resolver._resolved_identity == {}
