"""A/C. PERFORMANCE TESTS - every claim here is a measured counter.

These tests assert *budgets*, not wall-clock times, so they are deterministic
on any machine: DOM round trips, discovery counts, cache hit ratios and the
amount of blind sleeping are all counted by cghs.telemetry.PerfCounters.
"""

from __future__ import annotations

import pytest

from cghs.txstate import TxState
from tests.support.fake_portal import Latency, build_portal
from tests.support.harness import (
    item,
    make_orchestrator,
    make_runner,
    measure_sleep,
    patient,
)

CODES = ["LB012", "CN002", "CC001", "WC001", "CC002"]


# ---------------------------------------------------------------------------
# 6.1 persistent batch session
# ---------------------------------------------------------------------------

def many_codes(count: int):
    return ["SV%03d" % i for i in range(count)]


def test_one_hundred_items_use_one_attach_and_one_discovery():
    codes = many_codes(100)
    portal = build_portal(codes, latency=Latency.fast())
    runner = make_runner(portal)
    # One patient, 100 distinct line items: 1 process, 1 attach, 1 discovery.
    result = runner.run([patient("DES RAJ", [item(c, 1) for c in codes])])

    assert result.summary.items_total == 100
    assert result.summary.items_completed == 100
    assert runner.session.driver_attaches == 1
    assert result.summary.frame_discoveries == 1
    assert result.summary.full_tab_scans <= 1
    assert portal.counters["window_switches"] <= 1
    assert portal.counters["window_handles"] <= 1       # no repeated full tab scans
    assert portal.plus_clicks_by_code == {c: 1 for c in codes}


def test_dom_cost_per_item_is_flat_across_a_long_batch():
    codes = many_codes(60)
    portal = build_portal(codes, latency=Latency.fast())
    runner = make_runner(portal)
    runner.run([patient("DES RAJ", [item(c, 1) for c in codes])])

    per_item = [t.dom_calls for t in runner.orchestrator.telemetry]
    head = sum(per_item[:5]) / 5.0
    tail = sum(per_item[-5:]) / 5.0
    # The 56th item must not cost more than the 3rd: no per-item rediscovery
    # and no table-size-proportional verification.
    assert tail <= head * 1.2, (head, tail)


def test_frame_is_not_rediscovered_per_item():
    portal = build_portal(["LB012"], latency=Latency.fast())
    session, orch = make_orchestrator(portal)
    for n in range(12):
        portal.rows.clear()
        session.set_bill(f"BILL-{n}")        # production path: new bill, new ledger
        orch.process_item(item("LB012", 1))
    # A new bill re-proves the locators, but NEVER re-scans for the frame.
    assert session.counters.frame_discoveries == 1
    assert session.counters.frame_cache_hits >= 11


def test_frame_cache_is_invalidated_and_recovered_when_the_context_dies():
    portal = build_portal(["LB012"], latency=Latency.fast())
    session, orch = make_orchestrator(portal)
    orch.process_item(item("LB012", 1))
    assert session.counters.frame_discoveries == 1

    session.invalidate_context("simulated context loss")
    portal.rows.clear()
    session.set_bill("BILL-2")
    result = orch.process_item(item("LB012", 1))

    assert result.state == TxState.COMPLETED.value
    assert session.counters.frame_discoveries == 2          # recovered, not blind


# ---------------------------------------------------------------------------
# 6.3 locator strategy cache
# ---------------------------------------------------------------------------

def test_locator_strategy_cache_hit_rate_climbs_after_the_first_item():
    """Within one bill the strategy cache must carry almost every lookup."""
    codes = many_codes(11)
    portal = build_portal(codes, latency=Latency.fast())
    session, orch = make_orchestrator(portal)

    orch.process_item(item(codes[0], 1))
    first = session.counters.copy()

    for code in codes[1:]:
        orch.process_item(item(code, 1))
    later = session.counters.delta(first)

    hits, misses = later["locator_cache_hits"], later["locator_cache_misses"]
    assert hits > 0
    assert hits / float(hits + misses) > 0.8, (hits, misses)


def test_locator_cache_is_dropped_at_a_bill_boundary():
    """Bill isolation outranks the cache: a new patient page is re-proved."""
    portal = build_portal(["LB012"], latency=Latency.fast())
    session, orch = make_orchestrator(portal)
    orch.process_item(item("LB012", 1))
    assert session.resolver.cached_strategy("PROCEDURE_INPUT") is not None

    session.set_bill("NEXT-BILL")
    assert session.resolver.cached_strategy("PROCEDURE_INPUT") is None


def test_stale_cached_strategy_is_invalidated_not_trusted():
    portal = build_portal(["LB012"], latency=Latency.fast())
    session, orch = make_orchestrator(portal)
    orch.process_item(item("LB012", 1))
    assert session.resolver.cached_strategy("PROCEDURE_INPUT") is not None

    # The portal re-renders with different markup for that one control.
    portal.rename_control("procedure")
    portal.rows.clear()
    session.set_bill("BILL-RERENDER")
    result = orch.process_item(item("LB012", 1))

    assert result.state == TxState.COMPLETED.value
    assert session.counters.locator_cache_misses >= 1


def test_locate_all_never_returns_the_same_element_twice():
    portal = build_portal(["LB012"], latency=Latency.fast())
    portal.config.faults.overlapping_row_locators = True
    session, _ = make_orchestrator(portal)
    session.ensure_context()
    rows = session.resolver.locate_all("TABLE_ROWS")
    identities = [session.resolver._identity(el) for el in rows]
    assert len(identities) == len(set(identities))


# ---------------------------------------------------------------------------
# 6.4 / 6.5 compact probes and bounded polling
# ---------------------------------------------------------------------------

def test_single_item_dom_budget():
    """A happy-path item must stay inside a documented DOM round-trip budget."""
    portal = build_portal(["LB012"], latency=Latency.fast())
    session, orch = make_orchestrator(portal)
    result = orch.process_item(item("LB012", 1))
    assert result.state == TxState.COMPLETED.value
    telemetry = orch.telemetry[-1]
    assert telemetry.dom_calls <= 80, telemetry.as_dict()
    assert telemetry.execute_script_calls <= 40, telemetry.as_dict()


def test_steady_state_item_is_cheaper_than_the_first_item():
    portal = build_portal(["LB012", "CN002"], latency=Latency.fast())
    session, orch = make_orchestrator(portal)
    orch.process_item(item("LB012", 1))
    first = orch.telemetry[-1].dom_calls

    for _ in range(5):
        orch.process_item(item("CN002", 1))
    steady = orch.telemetry[-1].dom_calls

    assert steady <= first, f"steady {steady} > first {first}"


def test_probe_fallback_still_works_when_execute_script_is_unavailable():
    portal = build_portal(["LB012"], latency=Latency.fast())
    portal.config.faults.probe_unsupported = True
    session, orch = make_orchestrator(portal)
    result = orch.process_item(item("LB012", 1))
    assert result.state == TxState.COMPLETED.value       # documented fallback path
    assert session.counters.probe_fallbacks >= 1


def test_no_unconditional_global_idle_gate_per_micro_operation():
    """The 15s idle gate must not be charged per control read."""
    portal = build_portal(["LB012"], latency=Latency.fast())
    session, orch = make_orchestrator(portal)
    with measure_sleep() as meter:
        orch.process_item(item("LB012", 1))
    # No fixed sleeping at all on the happy path; waits are condition-driven.
    assert session.counters.fixed_sleep_ms == 0
    assert meter.total_ms < 1500, f"slept {meter.total_ms}ms"


def test_polling_budget_is_bounded_when_the_portal_never_commits():
    portal = build_portal(["LB012"], latency=Latency.fast())
    portal.config.faults.commit_never = {"LB012"}
    session, orch = make_orchestrator(portal, commit_timeout=0.5, reconcile_grace=0.3)
    result = orch.process_item(item("LB012", 1))
    assert result.state == TxState.RECONCILIATION_REQUIRED.value
    # commit_timeout + grace, with slack for the settle window - NOT 15s.
    assert orch.telemetry[-1].elapsed_ms < 3000, orch.telemetry[-1].as_dict()


# ---------------------------------------------------------------------------
# 6.8 targeted verification
# ---------------------------------------------------------------------------

def test_verification_is_targeted_not_a_full_table_rescan():
    portal = build_portal(["LB012"], latency=Latency.fast())
    # 150 pre-existing rows: a full-table verification would read them all.
    portal.seed_rows([("XX%03d" % i, "1") for i in range(150)])
    session, orch = make_orchestrator(portal)

    before = session.counters.copy()
    result = orch.process_item(item("LB012", 1))
    delta = session.counters.delta(before)

    assert result.state == TxState.COMPLETED.value
    assert delta["element_reads"] < 150, delta
    assert delta["dom_calls"] < 120, delta


def test_dom_cost_does_not_grow_with_table_size():
    small = build_portal(["LB012"], latency=Latency.fast())
    big = build_portal(["LB012"], latency=Latency.fast())
    big.seed_rows([("XX%03d" % i, "1") for i in range(400)])

    costs = []
    for portal in (small, big):
        session, orch = make_orchestrator(portal)
        orch.process_item(item("LB012", 1))
        costs.append(orch.telemetry[-1].dom_calls)

    assert costs[1] <= costs[0] * 1.5, costs


# ---------------------------------------------------------------------------
# telemetry completeness
# ---------------------------------------------------------------------------

def test_every_required_telemetry_field_is_populated():
    portal = build_portal(["LB012"], latency=Latency.fast())
    session, orch = make_orchestrator(portal)
    orch.process_item(item("LB012", 1))
    record = orch.telemetry[-1].as_dict()
    for field in ("run_id", "bill_id", "final_code", "quantity", "state", "stage",
                  "elapsed_ms", "dom_calls", "locator_cache_hit", "locator_cache_miss",
                  "frame_cache_hit", "frame_cache_miss", "verification_outcome"):
        assert field in record, field
    assert record["run_id"]
    assert record["final_code"] == "LB012"
    assert record["state"] == TxState.COMPLETED.value


def test_batch_summary_reports_every_required_metric():
    portal = build_portal(CODES, latency=Latency.fast())
    runner = make_runner(portal)
    result = runner.run([patient("DES RAJ", [item(c, 1) for c in CODES])])
    summary = result.summary.as_dict()
    for field in ("batch_elapsed_ms", "items_total", "items_completed", "items_failed",
                  "items_reconciliation_required", "avg_item_ms", "p50_item_ms",
                  "p95_item_ms", "total_dom_calls", "total_execute_script_calls",
                  "full_tab_scans", "frame_discoveries", "fixed_sleep_ms", "retries",
                  "duplicate_plus_attempts_blocked"):
        assert field in summary, field
    assert summary["items_total"] == len(CODES)
    assert summary["p95_item_ms"] >= summary["p50_item_ms"]
