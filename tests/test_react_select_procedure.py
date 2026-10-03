"""React-Select procedure selection - hard tests (task sections 13 and 19).

Every test here is written against the ACTUAL committed React state
(``dom.procedure_is_selected()`` / ``dom.selected_code()``), never against the
return value of the function under test.  A function that returns ``True``
while React holds nothing is exactly the defect being fixed.

The acceptance state is ``PROCEDURE_SELECTED``.  ``PROCEDURE_TEXT_TYPED`` is
not an acceptable substitute, and on React-Select it is not even a weaker
form of it - the two are mutually exclusive, because React-Select clears the
search input at the instant it commits a value.
"""

from __future__ import annotations

import time

import pytest
from selenium.common.exceptions import NoSuchElementException

from cghs.controllers import (
    EnhancementReasonController,
    ProcedureSelector,
    SpecialitySynchronizer,
)
from cghs.locators import resolve_expected_speciality
from cghs.orchestrator import TreatmentPlanOrchestrator
from cghs.session import PortalSession
from cghs.telemetry import EnterpriseLogger
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
    session.set_bill("BILL-RS")
    session.ensure_context()
    return session


def _portal(**kwargs):
    dom = build_react_portal(**kwargs)
    return dom, _session(dom)


def _portal_with(catalogue, **kwargs):
    dom = ReactSelectDOM(config=ReactSelectConfig(**kwargs), catalogue=catalogue)
    return dom, _session(dom)


# ---------------------------------------------------------------------------
# The defect itself (section 19: a test that fails against pre-fix behaviour)
# ---------------------------------------------------------------------------

def test_REGRESSION_typed_text_is_never_accepted_as_a_selection():
    """THE defect.  Typing leaves the code in the input; React holds nothing.

    Pre-fix, ``_verify_selection`` read that input and reported
    PROCEDURE_VERIFIED, so the bot walked on to Plus with no procedure
    selected and the operator had to press Enter by hand.
    """
    dom, session = _portal()
    selector = ProcedureSelector(session)

    element, _ = selector.resolver.locate("PROCEDURE_INPUT")
    selector._click(element)
    selector._clear_and_type(element, "GP001")

    # The input really does contain the code ...
    assert dom.typed_text("procedure") == "GP001"
    # ... and React really has committed nothing.
    assert dom.procedure_is_selected() is False
    # The engine must agree with React, not with the input.
    assert selector._selection_committed("GP001", "GP001") is False
    assert selector._verify_selection("GP001", "GP001") is False


def test_REGRESSION_a_real_commit_is_recognised_even_though_the_input_is_empty():
    """The other half: React-Select empties the input ON commit.

    Pre-fix this read as "not verified" and a perfectly good selection was
    rejected.
    """
    dom, session = _portal()
    assert ProcedureSelector(session).execute("GP001") is True
    assert dom.procedure_is_selected() is True
    assert dom.typed_text("procedure") == ""          # cleared by React
    assert ProcedureSelector(session)._selection_committed("GP001", "GP001") is True


# ---------------------------------------------------------------------------
# A. TYPE_ONLY
# ---------------------------------------------------------------------------

def test_A_type_only_is_not_a_selection():
    dom, session = _portal()
    selector = ProcedureSelector(session)
    element, _ = selector.resolver.locate("PROCEDURE_INPUT")
    selector._clear_and_type(element, "GP001")

    assert dom.procedure_is_selected() is False
    assert dom.selected_code() == ""


# ---------------------------------------------------------------------------
# B. ENTER_SELECTION
# ---------------------------------------------------------------------------

def test_B_typed_code_plus_programmatic_enter_selects():
    dom, session = _portal()
    assert ProcedureSelector(session).execute("GP001") is True

    assert dom.procedure_is_selected() is True
    assert dom.selected_code() == "GP001"
    # the commit came from Python, with no human keystroke involved
    assert "procedure:selected:GP001" in dom.stage_events


def test_B2_enter_is_dispatched_by_selenium_not_by_a_human():
    dom, session = _portal()
    before = dom.counters["send_keys"]
    ProcedureSelector(session).execute("GP001")
    assert dom.counters["send_keys"] > before


# ---------------------------------------------------------------------------
# C. EXACT_OPTION
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ordering", [
    ["GP001", "GP001A", "GP0010", "XGP001"],
    ["GP001A", "GP0010", "XGP001", "GP001"],      # exact match LAST
    ["XGP001", "GP001", "GP001A", "GP0010"],
])
def test_C_only_the_exact_option_is_ever_selected(ordering):
    labels = {"GP001": "GP001 - General Physician Consultation",
              "GP001A": "GP001A - GP Follow Up",
              "GP0010": "GP0010 - GP Night Visit",
              "XGP001": "XGP001 - External GP Referral"}
    catalogue = [Option(c, labels[c], "Consultation") for c in ordering]
    dom, session = _portal_with(catalogue)

    assert ProcedureSelector(session).execute("GP001") is True
    assert dom.selected_code() == "GP001"


def test_C2_a_near_match_alone_is_refused():
    catalogue = [Option("GP001A", "GP001A - GP Follow Up", "Consultation"),
                 Option("XGP001", "XGP001 - External GP", "Consultation")]
    dom, session = _portal_with(catalogue)

    with pytest.raises(NoSuchElementException):
        ProcedureSelector(session).execute("GP001")
    assert dom.procedure_is_selected() is False


# ---------------------------------------------------------------------------
# D / E. LISTBOX timing - condition driven, never a fixed sleep
# ---------------------------------------------------------------------------

def test_D_delayed_listbox_is_waited_for():
    dom, session = _portal(listbox_delay=0.25)
    started = time.perf_counter()
    assert ProcedureSelector(session).execute("GP001") is True
    elapsed = time.perf_counter() - started

    assert dom.selected_code() == "GP001"
    assert elapsed >= 0.25                      # it really did wait
    assert session.counters.fixed_sleep_ms == 0  # but not by sleeping


def test_E_immediate_listbox_is_not_slowed_down():
    dom, session = _portal(listbox_delay=0.0)
    started = time.perf_counter()
    assert ProcedureSelector(session).execute("GP001") is True
    elapsed = time.perf_counter() - started

    assert dom.selected_code() == "GP001"
    assert elapsed < 1.0
    assert session.counters.fixed_sleep_ms == 0


# ---------------------------------------------------------------------------
# F. STALE_REACT_ELEMENT
# ---------------------------------------------------------------------------

def test_F_a_rerendered_combobox_is_reacquired():
    dom, session = _portal(stale_after_type=True)
    assert ProcedureSelector(session).execute("GP001") is True
    assert dom.selected_code() == "GP001"


# ---------------------------------------------------------------------------
# G. EMPTY_OPTION
# ---------------------------------------------------------------------------

def test_G_absent_code_is_not_found_and_never_succeeds():
    dom, session = _portal(missing_codes=("GP001",))
    with pytest.raises(NoSuchElementException):
        ProcedureSelector(session).execute("GP001")
    assert dom.procedure_is_selected() is False


# ---------------------------------------------------------------------------
# H. WRONG_OPTION  (covered by C2 as well, asserted here on the committed code)
# ---------------------------------------------------------------------------

def test_H_engine_stops_rather_than_commit_a_wrong_option():
    catalogue = [Option("GP0010", "GP0010 - GP Night Visit", "Consultation")]
    dom, session = _portal_with(catalogue)
    with pytest.raises(NoSuchElementException):
        ProcedureSelector(session).execute("GP001")
    assert dom.selected_code() == ""


# ---------------------------------------------------------------------------
# I. ENTER_REJECTED -> safe exact-option fallback
# ---------------------------------------------------------------------------

def test_I_enter_rejected_falls_back_to_the_exact_option_click():
    dom, session = _portal(enter_rejected=True)
    assert ProcedureSelector(session).execute("GP001") is True

    assert dom.selected_code() == "GP001"
    assert "procedure:enter-rejected" in dom.stage_events   # Enter really failed
    assert dom.counters["js_clicks"] == 0                   # never a JS click


def test_I2_a_javascript_click_is_never_used_to_commit_a_react_option():
    """A JS click fires only `click`; React-Select listens on `mousedown`."""
    dom, session = _portal()
    ProcedureSelector(session).execute("GP001")
    assert dom.counters["js_clicks"] == 0
    assert dom.counters["mousedown"] > 0


# ---------------------------------------------------------------------------
# J / K / L. Speciality
# ---------------------------------------------------------------------------

def test_J_speciality_auto_populates_and_is_not_touched_again():
    dom, session = _portal()
    ProcedureSelector(session).execute("LB001")

    assert dom.selected_code("speciality") == "Laboratory"
    value, verdict = SpecialitySynchronizer(session).verify_expected("LB001")
    assert verdict == "AUTO_VERIFIED"
    assert value == "Laboratory"
    assert "speciality:auto:Laboratory" in dom.stage_events


def test_K_missing_speciality_is_detected_not_assumed():
    dom, session = _portal(speciality_auto=False)
    ProcedureSelector(session).execute("LB001")

    assert dom.selected["speciality"] is None
    _, verdict = SpecialitySynchronizer(session).verify_expected("LB001", timeout=0.4)
    assert verdict == "MISSING"


def test_L_wrong_speciality_is_detected_before_plus():
    dom, session = _portal(speciality_wrong_value="Radiology")
    ProcedureSelector(session).execute("LB001")        # expects Laboratory

    value, verdict = SpecialitySynchronizer(session).verify_expected("LB001")
    assert verdict == "WRONG"
    assert value == "Radiology"


def test_L2_unknown_code_is_never_guessed():
    """No canonical mapping exists for GP -> the engine must not invent one."""
    assert resolve_expected_speciality("GP001") is None

    dom, session = _portal()
    ProcedureSelector(session).execute("GP001")
    _, verdict = SpecialitySynchronizer(session).verify_expected("GP001")
    assert verdict == "AUTO_UNVERIFIED"      # accepted, but explicitly unproven


@pytest.mark.parametrize("code,expected", [
    ("LB001", "Laboratory"), ("RI001", "Radiology"),
    ("CN002", "Consultation"), ("BL001", "Blood"),
])
def test_L3_only_operator_supplied_mappings_exist(code, expected):
    assert resolve_expected_speciality(code) == expected


# ---------------------------------------------------------------------------
# M. REASON
# ---------------------------------------------------------------------------

def test_M_reason_others_is_a_real_selection_not_typed_text():
    dom, session = _portal()
    ProcedureSelector(session).execute("GP001")
    outcome = EnhancementReasonController(session).execute()

    assert outcome.present and outcome.selected
    assert dom.selected["reason"] is not None
    assert dom.selected["reason"].code == "Others"
    assert dom.typed_text("reason") == ""        # React cleared the search text


def test_M2_reason_selection_does_not_use_a_javascript_click():
    dom, session = _portal()
    ProcedureSelector(session).execute("GP001")
    before = dom.counters["js_clicks"]
    EnhancementReasonController(session).execute()
    assert dom.counters["js_clicks"] == before


# ---------------------------------------------------------------------------
# N. PLUS visibility
# ---------------------------------------------------------------------------

def test_N_plus_is_unavailable_until_every_stage_is_valid():
    dom, _ = _portal()
    assert dom.plus["visible"] is False          # nothing selected yet

    session = _session(dom)
    ProcedureSelector(session).execute("GP001")
    assert dom.plus["visible"] is False          # reason still missing

    EnhancementReasonController(session).execute()
    assert dom.plus["visible"] is True           # now, and only now


# ---------------------------------------------------------------------------
# O / P. Plus dispatch and the locked-quantity headline
# ---------------------------------------------------------------------------

def test_O_one_transaction_dispatches_plus_exactly_once():
    dom, session = _portal()
    result = TreatmentPlanOrchestrator(session).process_item({"code": "GP001", "qty": 1})

    assert result.success is True
    assert dom.plus_clicks == 1
    assert len(result.transactions) == 1
    tx = result.transactions[0]
    assert tx.ledger.dispatch_count(tx.identity.transaction_id) == 1


def test_P_gp001_locked_quantity_30():
    """The headline: 30 verified units, one selection of everything else."""
    dom, session = _portal()
    result = TreatmentPlanOrchestrator(session).process_item({"code": "GP001", "qty": 30})
    counters = session.counters

    assert result.success is True
    assert result.state == "COMPLETED"
    # exactly 30 physical dispatches and 30 portal rows
    assert dom.plus_clicks == 30
    assert dom.plus_clicks_by_code["GP001"] == 30
    assert len(dom.rows) == 30
    assert all(row["code"] == "GP001" for row in dom.rows)
    # 30 distinct transactions, each dispatched exactly once
    ids = [tx.identity.transaction_id for tx in result.transactions]
    assert len(ids) == len(set(ids)) == 30
    for tx in result.transactions:
        assert tx.ledger.dispatch_count(tx.identity.transaction_id) == 1
    # the stages were driven once, then reused on observed evidence
    assert counters.procedure_selections == 1
    assert counters.speciality_syncs == 1
    assert counters.reason_selections == 1
    assert counters.stage_context_reuses == 29 * 3
    # and no fixed sleep anywhere on the hot path
    assert counters.fixed_sleep_ms == 0


def test_P2_locked_quantity_is_generic_not_gp001_specific():
    for code, qty in (("LB001", 4), ("RI001", 7), ("CN002", 3)):
        dom, session = _portal()
        result = TreatmentPlanOrchestrator(session).process_item(
            {"code": code, "qty": qty})
        assert result.success is True, code
        assert dom.plus_clicks == qty, code
        assert len(dom.rows) == qty, code
        assert session.counters.procedure_selections == 1, code


def test_P3_quantity_lock_is_never_bypassed_by_typing():
    """`#noofdays` is read-only; the engine must not try to write 30 into it."""
    dom, session = _portal()
    TreatmentPlanOrchestrator(session).process_item({"code": "GP001", "qty": 30})
    assert dom.quantity["value"] == ""           # never written
    assert len(dom.rows) == 30                   # achieved by 30 real Adds


# ---------------------------------------------------------------------------
# hot path hygiene (section 11)
# ---------------------------------------------------------------------------

def test_no_fixed_sleep_anywhere_on_the_react_hot_path():
    dom, session = _portal(listbox_delay=0.1)
    TreatmentPlanOrchestrator(session).process_item({"code": "GP001", "qty": 5})
    assert session.counters.fixed_sleep_ms == 0


def test_stage_reuse_is_refused_without_probe_evidence():
    """No probe => no evidence => re-drive everything.  Slower, never unsafe."""
    dom = build_react_portal()
    session = _session(dom)
    original = dom._probe

    def broken(spec):
        fields = (spec or {}).get("fields", [])
        if set(fields) >= {"procedure", "speciality", "reason"}:
            return {"ok": False}                 # probe unusable
        return original(spec)

    dom._probe = broken
    result = TreatmentPlanOrchestrator(session).process_item({"code": "GP001", "qty": 4})

    assert result.success is True
    assert dom.plus_clicks == 4
    assert session.counters.stage_context_reuses == 0
    assert session.counters.procedure_selections == 4
