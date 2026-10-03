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
