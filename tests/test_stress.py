"""Stress: realistic batch sizes against fast / medium / slow portals.

27, 84 and 200 items are the sizes named in the acceptance contract.  The
assertions are invariants (never a double Plus, never a silent loss, bounded
DOM cost per item), not wall-clock thresholds, so the suite is reproducible.
"""

from __future__ import annotations

import pytest

from cghs.txstate import TxState
from tests.support.fake_portal import Latency, build_portal
from tests.support.harness import item, make_runner, patient


def codes(count: int):
    return ["SV%04d" % i for i in range(count)]


def run_batch(count: int, latency: Latency, **portal_kwargs):
    plan = codes(count)
    portal = build_portal(plan, latency=latency, **portal_kwargs)
    runner = make_runner(portal, commit_timeout=2.0, reconcile_grace=0.5)
    result = runner.run([patient("STRESS PATIENT", [item(c, 1) for c in plan])])
    return portal, runner, result


@pytest.mark.parametrize("count", [27, 84])
def test_batch_completes_with_exactly_one_plus_per_item_fast(count):
    portal, runner, result = run_batch(count, Latency.fast())
    assert result.summary.items_total == count
    assert result.summary.items_completed == count
    assert result.summary.items_failed == 0
    assert result.summary.items_reconciliation_required == 0
    assert sum(portal.plus_clicks_by_code.values()) == count
    assert len(portal.rows) == count
    assert result.summary.duplicate_plus_attempts_blocked == 0


@pytest.mark.parametrize("count", [27, 84])
def test_batch_is_correct_on_a_medium_latency_portal(count):
    portal, runner, result = run_batch(count, Latency.medium())
    assert result.summary.items_completed == count
    assert sum(portal.plus_clicks_by_code.values()) == count


@pytest.mark.slow
def test_two_hundred_items_stay_within_the_session_and_dom_budget():
    portal, runner, result = run_batch(200, Latency.fast())
    assert result.summary.items_completed == 200
    assert runner.session.driver_attaches == 1
    assert result.summary.frame_discoveries == 1
    assert result.summary.full_tab_scans <= 1
    assert result.summary.fixed_sleep_ms == 0
    per_item = result.summary.total_dom_calls / 200.0
    assert per_item < 80, per_item


@pytest.mark.slow
def test_slow_portal_still_never_double_clicks_plus():
    portal, runner, result = run_batch(27, Latency.slow())
    assert sum(portal.plus_clicks_by_code.values()) == 27
    for code, count in portal.plus_clicks_by_code.items():
        assert count == 1, (code, count)


def test_mixed_failures_are_isolated_and_reported_not_swallowed():
    plan = codes(30)
    portal = build_portal(plan, latency=Latency.fast())
    portal.config.faults.commit_never = {plan[5], plan[17]}
    portal.config.faults.commit_wrong_qty = {plan[9]}
    portal.config.faults.click_raises_stale = {plan[22]}
    runner = make_runner(portal, commit_timeout=0.4, reconcile_grace=0.25)

    result = runner.run([patient("MIXED", [item(c, 1) for c in plan])])

    assert result.summary.items_total == 30
    assert result.summary.items_completed == 26
    assert result.summary.items_reconciliation_required == 3    # 2 unknown + 1 wrong qty
    assert result.summary.items_failed == 1                     # the pre-dispatch failure
    # the failing items did not corrupt the others
    assert len([r for r in portal.rows if r["code"] in plan]) == 27
    assert portal.plus_clicks_by_code.get(plan[22], 0) == 0


def test_a_long_batch_keeps_per_item_telemetry_for_every_item():
    portal, runner, result = run_batch(27, Latency.fast())
    assert len(result.summary.items) == 27
    for record in result.summary.items:
        assert record["final_code"]
        assert record["state"] == TxState.COMPLETED.value
        assert record["elapsed_ms"] >= 0
        assert record["dom_calls"] > 0


def test_cancellation_mid_batch_stops_promptly_and_cleanly():
    plan = codes(40)
    portal = build_portal(plan, latency=Latency.fast())
    flag = {"stop": False}
    runner = make_runner(portal, cancel_check=lambda: flag["stop"])

    processed = []
    original = runner.orchestrator.process_item

    def counting(item_dict):
        result = original(item_dict)
        processed.append(result.code)
        if len(processed) == 10:
            flag["stop"] = True
        return result

    runner.orchestrator.process_item = counting
    result = runner.run([patient("CANCEL", [item(c, 1) for c in plan])])

    assert len(processed) == 10
    assert sum(portal.plus_clicks_by_code.values()) == 10
    assert "cancel" in result.stopped_reason.lower()
