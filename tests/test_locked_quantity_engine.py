"""Hard locked-quantity engine tests (task section 16, cases A-Q).

The engine under test must satisfy one contract:

    For a code whose portal quantity field is LOCKED, a required quantity of N
    is executed as N verified one-unit transactions, each with its own
    duplicate-safe transaction id, and NO stage is re-driven unless the portal
    itself dropped it.

A note on portal semantics, because it decides what these tests may assert.
This environment has no browser, so whether the real portal clears the
procedure / speciality / reason after an Add is **not observable here**.  The
engine therefore never assumes either way: it reads the current stage values
with one compact probe and re-drives only what actually drifted.  That makes
it correct under both behaviours, and both behaviours are exercised below
(``reset_*_after_add``).  Real-portal confirmation stays NOT_YET_VERIFIED.

What is NOT modelled: nothing here proves the real portal adds exactly one
unit per Add.  That comes from the baseline implementation, which was proven
against the live portal and whose ``_process_locked_quantity`` performs
``required_qty`` single-unit transactions.  The engine preserves that.
"""

from __future__ import annotations

import pytest

from cghs.telemetry import PerfCounters
from cghs.txstate import TxState
from tests.support.fake_portal import Latency, build_portal
from tests.support.harness import make_orchestrator, make_runner, item, patient

LOCKED = {"GP001"}


def run_locked(code="GP001", qty=30, seed_rows=0, **cfg):
    """Execute one locked-quantity item and return (result, portal, counters)."""
    portal = build_portal([code], locked_codes={code}, **cfg)
    if seed_rows:
        portal.seed_rows([(code, "1") for _ in range(seed_rows)])
    session, orch = make_orchestrator(portal)
    session.set_bill("BILL1")
    session.ensure_context()
    result = orch.process_item({"code": code, "qty": qty})
    return result, portal, session.counters, orch


# ---------------------------------------------------------------------------
# A / B / C - the core quantities
# ---------------------------------------------------------------------------

def test_A_gp001_locked_quantity_30_produces_exactly_30_verified_units():
    result, portal, counters, orch = run_locked("GP001", 30)

    assert result.success is True
    assert result.state == TxState.COMPLETED.value

    # exactly 30 physical Plus dispatches - no more, no fewer
    assert portal.plus_clicks_by_code["GP001"] == 30
    assert sum(portal.plus_clicks_by_code.values()) == 30
    assert len(portal.rows) == 30
    assert all(row["code"].upper().startswith("GP001") for row in portal.rows)

    # one transaction per unit, each individually proven
    assert len(result.transactions) == 30
    assert all(tx.is_success for tx in result.transactions)
    assert counters.locked_unit_transactions == 30
    assert counters.duplicate_plus_attempts_blocked == 0

    # every transaction id is unique and carries its unit index
    ids = [tx.identity.transaction_id for tx in result.transactions]
    assert len(set(ids)) == 30
    assert ids[0].endswith("|1") and ids[-1].endswith("|30")


def test_B_locked_quantity_1_dispatches_one_plus():
    result, portal, counters, _ = run_locked("GP001", 1)
    assert result.success is True
    assert portal.plus_clicks_by_code["GP001"] == 1
    assert len(portal.rows) == 1
    assert counters.locked_unit_transactions == 1


def test_C_locked_quantity_5_produces_five_verified_units():
    result, portal, counters, _ = run_locked("GP001", 5)
    assert result.success is True
    assert portal.plus_clicks_by_code["GP001"] == 5
    assert len(portal.rows) == 5
    assert len(result.transactions) == 5


# ---------------------------------------------------------------------------
# D - the engine is generic, never GP001-specific
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("code", ["GP001", "CC001", "WC001", "LB0121", "RR001"])
def test_D_any_locked_code_uses_the_same_engine_path(code):
    result, portal, counters, _ = run_locked(code, 7)
    assert result.success is True
    assert portal.plus_clicks_by_code[code] == 7
    assert len(portal.rows) == 7
    assert counters.locked_unit_transactions == 7


def test_D2_no_code_specific_branch_exists_in_the_engine():
    """A hardcoded GP001 branch would make the engine non-generic."""
    import pathlib
    source = pathlib.Path("cghs/orchestrator.py").read_text(encoding="utf-8")
    for literal in ('"GP001"', "'GP001'"):
        assert literal not in source, "orchestrator contains a GP001-specific branch"


# ---------------------------------------------------------------------------
# E / F / G - pre-existing portal quantity
# ---------------------------------------------------------------------------

def test_E_partial_existing_quantity_adds_only_the_remainder():
    """required 30, portal already holds 12 -> add exactly 18."""
    result, portal, counters, _ = run_locked("GP001", 30, seed_rows=12)

    assert result.success is True
    assert portal.plus_clicks_by_code.get("GP001", 0) == 18
    assert len(portal.rows) == 30
    assert counters.locked_unit_transactions == 18


def test_F_complete_existing_quantity_adds_nothing():
    """required 30, portal already holds 30 -> zero Plus."""
    result, portal, counters, _ = run_locked("GP001", 30, seed_rows=30)

    assert portal.plus_clicks_by_code.get("GP001", 0) == 0
    assert len(portal.rows) == 30
    assert result.state == TxState.DUPLICATE_PROVEN.value
    assert result.success is True
    assert counters.locked_unit_transactions == 0


def test_G_duplicate_portal_rows_are_counted_not_over_added():
    """Existing rows are aggregated by quantity, never double counted."""
    portal = build_portal(["GP001"], locked_codes={"GP001"})
    # one row already carrying 5 units, plus 3 single rows => 8 present
    portal.seed_rows([("GP001", "5")] + [("GP001", "1") for _ in range(3)])
    session, orch = make_orchestrator(portal)
    session.set_bill("BILL1")
    session.ensure_context()

    result = orch.process_item({"code": "GP001", "qty": 10})

    assert result.success is True
    assert portal.plus_clicks_by_code.get("GP001", 0) == 2      # 10 - 8
    assert session.counters.locked_unit_transactions == 2


@pytest.mark.parametrize("already,required,expected_clicks", [
    (0, 30, 30), (1, 30, 29), (12, 30, 18), (29, 30, 1), (30, 30, 0), (31, 30, 0),
])
def test_E2_remaining_units_are_exact_for_every_starting_point(already, required,
                                                               expected_clicks):
    result, portal, counters, _ = run_locked("GP001", required, seed_rows=already)
    assert portal.plus_clicks_by_code.get("GP001", 0) == expected_clicks
    assert result.success is True


# ---------------------------------------------------------------------------
# H / I / J / K - stage reuse vs. stage reset
# ---------------------------------------------------------------------------

def test_I_procedure_is_NOT_reselected_when_the_portal_keeps_it():
    """The whole point of the fast path: no redundant re-selection."""
    result, portal, counters, _ = run_locked("GP001", 30)
    assert result.success is True
    assert portal.plus_clicks_by_code["GP001"] == 30
    # one selection for the whole 30-unit run, not 30
    assert counters.procedure_selections == 1
    assert counters.speciality_syncs == 1
    assert counters.stage_context_reuses >= 29


def test_H_procedure_IS_reselected_when_the_portal_resets_it():
    result, portal, counters, _ = run_locked("GP001", 5,
                                             reset_procedure_after_add=True)
    assert result.success is True
    assert portal.plus_clicks_by_code["GP001"] == 5
    assert len(portal.rows) == 5
    # the portal dropped it every time, so it must be re-driven every time
    assert counters.procedure_selections == 5


def test_K_reason_is_NOT_reselected_when_the_portal_keeps_it():
    result, portal, counters, _ = run_locked("GP001", 20)
    assert result.success is True
    assert counters.reason_selections == 1


def test_J_reason_IS_reselected_when_the_portal_resets_it():
    result, portal, counters, _ = run_locked("GP001", 5,
                                             reset_reason_after_add=True)
    assert result.success is True
    assert portal.plus_clicks_by_code["GP001"] == 5
    assert counters.reason_selections == 5


def test_speciality_reset_forces_a_procedure_reselect_to_repopulate_it():
    """The portal derives speciality from procedure - syncing alone cannot fix it."""
    result, portal, counters, _ = run_locked("GP001", 5,
                                             reset_speciality_after_add=True)
    assert result.success is True
    assert portal.plus_clicks_by_code["GP001"] == 5
    assert counters.speciality_syncs == 5
    assert counters.procedure_selections == 5


def test_every_stage_reset_still_completes_all_units():
    result, portal, counters, _ = run_locked(
        "GP001", 8, reset_procedure_after_add=True,
        reset_speciality_after_add=True, reset_reason_after_add=True)
    assert result.success is True
    assert portal.plus_clicks_by_code["GP001"] == 8
    assert len(portal.rows) == 8
    assert counters.procedure_selections == 8
    assert counters.reason_selections == 8


def test_stage_reuse_is_refused_when_the_probe_is_unavailable():
    """No probe => no evidence => re-drive everything.  Speed may drop, safety may not."""
    portal = build_portal(["GP001"], locked_codes={"GP001"})
    portal.config.faults.probe_unsupported = True
    session, orch = make_orchestrator(portal)
    session.set_bill("BILL1")
    session.ensure_context()
    result = orch.process_item({"code": "GP001", "qty": 5})
    counters = session.counters
    assert result.success is True
    assert portal.plus_clicks_by_code["GP001"] == 5
    # without the probe the engine cannot prove reuse is safe
    assert counters.procedure_selections == 5
    assert counters.stage_context_reuses == 0


# ---------------------------------------------------------------------------
# L / M / N / O - failure handling inside a locked run
# ---------------------------------------------------------------------------

def test_N_unknown_outcome_mid_run_freezes_and_stops():
    """Unit 1 commits, unit 2's outcome is unknown => RECONCILIATION_REQUIRED."""
    portal = build_portal(["GP001"], locked_codes={"GP001"})
    session, orch = make_orchestrator(portal, commit_timeout=0.4, reconcile_grace=0.2)
    session.set_bill("BILL1")
    session.ensure_context()

    original = portal._append_row
    state = {"n": 0}

    def gated(code, qty):
        state["n"] += 1
        if state["n"] == 2:
            return                      # unit 2 silently never lands
        original(code, qty)

    portal._append_row = gated
    result = orch.process_item({"code": "GP001", "qty": 4})

    assert result.success is False
    assert result.needs_operator is True
    assert result.state == TxState.RECONCILIATION_REQUIRED.value
    # it STOPPED at the unverified unit - no blind continuation
    assert portal.plus_clicks_by_code["GP001"] == 2
    assert session.counters.duplicate_plus_attempts_blocked == 0


def test_no_transaction_id_ever_dispatches_twice_in_a_locked_run():
    result, portal, counters, _ = run_locked("GP001", 30)
    ids = [tx.identity.transaction_id for tx in result.transactions]
    assert len(ids) == len(set(ids)) == 30
    # 30 unique ids and exactly 30 physical clicks => one dispatch per id
    assert portal.plus_clicks_by_code["GP001"] == 30
    assert counters.duplicate_plus_attempts_blocked == 0
    # the ledger is the authority: exactly one physical dispatch per tx id
    for tx in result.transactions:
        assert tx.ledger.dispatch_count(tx.identity.transaction_id) == 1


@pytest.mark.parametrize("latency", ["fast", "slow"])
def test_PQ_locked_run_is_correct_on_fast_and_slow_portals(latency):
    """P/Q: correctness identical; only the condition waits differ."""
    portal = build_portal(["GP001"], locked_codes={"GP001"},
                          latency=getattr(Latency, latency)())
    session, orch = make_orchestrator(portal, commit_timeout=3.0)
    session.set_bill("BILL1")
    session.ensure_context()
    result = orch.process_item({"code": "GP001", "qty": 6})

    assert result.success is True
    assert portal.plus_clicks_by_code["GP001"] == 6
    assert len(portal.rows) == 6


def test_O_delayed_mutation_is_waited_on_not_slept_through():
    portal = build_portal(["GP001"], locked_codes={"GP001"})
    portal.config.faults.commit_late = {"GP001"}
    portal.config.faults.late_commit_delay = 0.25
    session, orch = make_orchestrator(portal, commit_timeout=2.0)
    session.set_bill("BILL1")
    session.ensure_context()
    result = orch.process_item({"code": "GP001", "qty": 3})

    assert result.success is True
    assert portal.plus_clicks_by_code["GP001"] == 3
    assert session.counters.fixed_sleep_ms == 0


# ---------------------------------------------------------------------------
# no fixed sleeping anywhere on the locked hot path
# ---------------------------------------------------------------------------

def test_no_fixed_sleep_between_locked_units():
    result, portal, counters, _ = run_locked("GP001", 30)
    assert result.success is True
    assert counters.fixed_sleep_ms == 0


def test_dom_cost_per_unit_does_not_grow_with_the_number_of_units():
    """A per-unit full table rescan would make this grow; it must not."""
    _, p10, c10, _ = run_locked("GP001", 10)
    _, p40, c40, _ = run_locked("GP001", 40)
    per_unit_10 = p10.dom_calls() / 10.0
    per_unit_40 = p40.dom_calls() / 40.0
    assert per_unit_40 <= per_unit_10 * 1.25, (
        f"per-unit DOM cost grew: {per_unit_10:.1f} -> {per_unit_40:.1f}")


# ---------------------------------------------------------------------------
# section 10 - amount-based codes must NEVER be unit-split
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("code", ["DRUG100", "CNSU100"])
def test_amount_based_codes_are_never_split_into_units(code):
    """Quantity semantics do not apply to amount-based internal codes."""
    portal = build_portal([code], locked_codes={code})
    session, orch = make_orchestrator(portal)
    session.set_bill("BILL1")
    session.ensure_context()

    result = orch.process_item({"code": code, "qty": 1, "amount": 6486.50})

    assert sum(portal.plus_clicks_by_code.values()) == 1, (
        f"{code} must produce exactly ONE Plus regardless of amount")
    assert session.counters.locked_unit_transactions == 0
    assert result.success is True
