"""B. CORRECTNESS / STATE-MACHINE TESTS.

Every test here must be able to FAIL for a real defect.  In particular the
first test in this file fails against the baseline implementation, which
converted an unverifiable Plus into COMPLETED.
"""

from __future__ import annotations

import pathlib

import pytest
from selenium.common.exceptions import WebDriverException

from cghs.txstate import (
    Diagnostic,
    DispatchLedger,
    DispatchProof,
    DuplicateDispatchBlocked,
    IllegalTransition,
    PlusTransaction,
    TransactionJournal,
    TxIdentity,
    TxState,
)
from tests.support.fake_portal import Latency, build_portal
from tests.support.harness import item, make_orchestrator, make_runner, patient

REPO = pathlib.Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# THE root-cause regression
# ---------------------------------------------------------------------------

def test_unknown_portal_result_is_never_success():
    """The exact defect: dispatched + unverifiable must NOT become COMPLETED."""
    portal = build_portal(["LB012"], latency=Latency.fast())
    portal.config.faults.commit_never = {"LB012"}
    session, orch = make_orchestrator(portal, commit_timeout=0.3, reconcile_grace=0.2)

    result = orch.process_item(item("LB012", 1))

    assert result.state == TxState.RECONCILIATION_REQUIRED.value
    assert result.success is False
    assert result.needs_operator is True
    assert result.diagnostic == Diagnostic.COMMIT_TIMEOUT_UNKNOWN.value
    # the physical mutation was attempted exactly once and never repeated
    assert portal.plus_clicks_by_code.get("LB012") == 1
    # and it is NOT counted as a completed item
    tx = result.transactions[-1]
    assert tx.state is TxState.RECONCILIATION_REQUIRED
    assert tx.is_success is False


def _executable_string_literals(path: pathlib.Path):
    """Every string constant in a module EXCEPT module/class/function docstrings."""
    import ast
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), str(path))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in docstrings:
            yield node.value


def test_baseline_assumed_committed_literal_is_gone_from_executable_code():
    """`TX-UNKNOWN-ASSUMED-COMMITTED` must not be produced by any shipped code.

    Documentation may describe the defect; no executable statement may emit it.
    """
    offenders = []
    for path in sorted((REPO / "cghs").rglob("*.py")) + [REPO / "app.py"]:
        for literal in _executable_string_literals(path):
            upper = literal.upper()
            if "ASSUMED-COMMITTED" in upper or "ASSUMED_COMMITTED" in upper:
                offenders.append((str(path.relative_to(REPO)), literal))
    assert offenders == []


# ---------------------------------------------------------------------------
# exactly-one-Plus invariant
# ---------------------------------------------------------------------------

def test_success_dispatches_exactly_one_plus():
    portal = build_portal(["LB012"], latency=Latency.fast())
    session, orch = make_orchestrator(portal)
    result = orch.process_item(item("LB012", 1))
    assert result.state == TxState.COMPLETED.value
    assert result.success is True
    assert portal.plus_clicks_by_code["LB012"] == 1
    assert len(portal.rows) == 1


def test_duplicate_submission_is_blocked_by_the_ledger():
    """Even if the portal LOSES the row, the same transaction never re-clicks.

    This is the last-resort invariant: the dispatch ledger is keyed by
    transaction id, so a second physical Plus for one transaction is
    impossible regardless of what the DOM reports.
    """
    portal = build_portal(["LB012"], latency=Latency.fast())
    session, orch = make_orchestrator(portal, commit_timeout=0.3, reconcile_grace=0.2)

    first = orch.process_item(item("LB012", 1))
    assert first.state == TxState.COMPLETED.value
    assert portal.plus_clicks_by_code["LB012"] == 1

    portal.rows.clear()                      # the portal "loses" the committed row
    orch.last_code = None                    # and the orchestrator is asked again
    second = orch.process_item(item("LB012", 1))

    assert second.transactions[-1].state is TxState.DUPLICATE_PROVEN
    assert portal.plus_clicks_by_code["LB012"] == 1
    assert session.counters.duplicate_plus_attempts_blocked >= 1


def test_rerunning_a_committed_plan_adds_no_second_plus():
    portal = build_portal(["LB012", "CN002"], latency=Latency.fast())
    runner = make_runner(portal)
    queue = [patient("DES RAJ", [item("LB012", 1), item("CN002", 2)])]
    first = runner.run(queue)
    assert first.summary.items_completed == 2
    clicks_after_first = dict(portal.plus_clicks_by_code)

    second = runner.run(queue)                      # same plan, same portal
    assert second.summary.items_skipped_duplicate == 2
    assert portal.plus_clicks_by_code == clicks_after_first


# ---------------------------------------------------------------------------
# dispatch failure classification
# ---------------------------------------------------------------------------

def test_click_exception_before_dispatch_is_not_success_and_retries_once():
    portal = build_portal(["LB012"], latency=Latency.fast())
    portal.config.faults.click_raises_stale = {"LB012"}
    session, orch = make_orchestrator(portal, commit_timeout=0.3, reconcile_grace=0.2)

    result = orch.process_item(item("LB012", 1))

    tx = result.transactions[-1]
    assert tx.state is TxState.FAILED_BEFORE_DISPATCH
    assert tx.dispatch_proof is DispatchProof.PROVEN_NOT_DISPATCHED
    assert result.success is False
    # The portal never saw a click, and the retry budget is exactly one.
    assert portal.plus_clicks_by_code.get("LB012", 0) == 0
    assert session.ledger.retry_grants(tx.identity.transaction_id) == 1


def test_click_returns_but_commit_is_delayed_never_clicks_twice():
    portal = build_portal(["LB012"], latency=Latency.fast())
    portal.config.faults.commit_late = {"LB012"}
    portal.config.faults.late_commit_delay = 0.45
    session, orch = make_orchestrator(portal, commit_timeout=1.5, reconcile_grace=0.3)

    result = orch.process_item(item("LB012", 1))

    assert result.state == TxState.COMPLETED.value
    assert portal.plus_clicks_by_code["LB012"] == 1


def test_late_row_is_closed_by_reconciliation_not_by_a_retry():
    portal = build_portal(["LB012"], latency=Latency.fast())
    portal.config.faults.commit_late = {"LB012"}
    portal.config.faults.late_commit_delay = 0.35
    session, orch = make_orchestrator(portal, commit_timeout=0.15, reconcile_grace=0.9)

    result = orch.process_item(item("LB012", 1))

    tx = result.transactions[-1]
    assert tx.state is TxState.COMPLETED
    assert tx.diagnostic is Diagnostic.COMMIT_VERIFIED_LATE
    assert session.counters.reconciliations >= 1
    assert portal.plus_clicks_by_code["LB012"] == 1


def test_click_exception_after_dispatch_freezes_for_reconciliation():
    portal = build_portal(["LB012"], latency=Latency.fast())
    portal.config.faults.click_raises_after_dispatch = {"LB012"}
    portal.config.faults.commit_never = {"LB012"}
    session, orch = make_orchestrator(portal, commit_timeout=0.3, reconcile_grace=0.2)

    result = orch.process_item(item("LB012", 1))

    tx = result.transactions[-1]
    assert tx.state is TxState.RECONCILIATION_REQUIRED
    assert tx.dispatch_proof is DispatchProof.UNKNOWN
    assert result.success is False
    assert portal.plus_clicks_by_code["LB012"] == 1          # never a second one


def test_click_exception_after_dispatch_that_did_commit_is_reconciled_to_success():
    portal = build_portal(["LB012"], latency=Latency.fast())
    portal.config.faults.click_raises_after_dispatch = {"LB012"}
    session, orch = make_orchestrator(portal, commit_timeout=0.4, reconcile_grace=0.4)

    result = orch.process_item(item("LB012", 1))

    assert result.state == TxState.COMPLETED.value
    assert portal.plus_clicks_by_code["LB012"] == 1


# ---------------------------------------------------------------------------
# verification exactness
# ---------------------------------------------------------------------------

def test_wrong_row_code_is_not_success():
    portal = build_portal(["LB012"], latency=Latency.fast())
    portal.config.faults.commit_wrong_code = {"LB012": "LB099"}
    session, orch = make_orchestrator(portal, commit_timeout=0.3, reconcile_grace=0.2)

    result = orch.process_item(item("LB012", 1))

    assert result.success is False
    assert result.state == TxState.RECONCILIATION_REQUIRED.value
    assert result.transactions[-1].diagnostic is Diagnostic.ROW_CODE_MISMATCH


def test_wrong_quantity_is_not_success():
    portal = build_portal(["LB012"], latency=Latency.fast())
    portal.config.faults.commit_wrong_qty = {"LB012"}
    session, orch = make_orchestrator(portal, commit_timeout=0.3, reconcile_grace=0.2)

    result = orch.process_item(item("LB012", 3))

    assert result.success is False
    assert result.state == TxState.RECONCILIATION_REQUIRED.value
    assert result.transactions[-1].diagnostic is Diagnostic.ROW_QTY_MISMATCH


def test_unrelated_table_mutation_does_not_mark_the_target_committed():
    portal = build_portal(["LB012"], latency=Latency.fast())
    portal.config.faults.unrelated_mutation = {"LB012"}
    session, orch = make_orchestrator(portal, commit_timeout=0.4, reconcile_grace=0.2)

    result = orch.process_item(item("LB012", 1))

    assert result.success is False
    assert any(r["code"] == "ZZ999" for r in portal.rows)
    assert not any(r["code"] == "LB012" for r in portal.rows)


def test_substring_code_collision_is_rejected():
    """A ``CC001`` row must never satisfy a ``C001`` transaction."""
    from cghs.dom import row_matches_code
    assert row_matches_code("CC001", "C001") is False
    assert row_matches_code("C001", "C001") is True
    assert row_matches_code("LB0121", "LB012") is False
    assert row_matches_code("drugs(DRGU100-None)", "DRUG100") is True
    assert row_matches_code("CNSU100", "CNSU100") is True


# ---------------------------------------------------------------------------
# preconditions must block the Plus
# ---------------------------------------------------------------------------

def test_missing_reason_option_blocks_the_plus():
    portal = build_portal(["LB012"], latency=Latency.fast())
    portal.config.faults.reason_without_others = True
    session, orch = make_orchestrator(portal, commit_timeout=0.3, reconcile_grace=0.2)

    result = orch.process_item(item("LB012", 1))

    assert result.success is False
    assert portal.plus_clicks_by_code.get("LB012", 0) == 0
    assert result.transactions[-1].state is TxState.FAILED_BEFORE_DISPATCH
    assert result.transactions[-1].diagnostic is Diagnostic.REASON_MISSING


def test_absent_reason_control_remains_optional():
    """Verified portal behaviour: some flows have no reason control at all."""
    portal = build_portal(["LB012"], latency=Latency.fast())
    portal.config.faults.reason_absent = True
    session, orch = make_orchestrator(portal)
    result = orch.process_item(item("LB012", 1))
    assert result.state == TxState.COMPLETED.value
    assert portal.plus_clicks_by_code["LB012"] == 1


def test_no_exact_procedure_option_means_no_plus():
    portal = build_portal(["LB012"], latency=Latency.fast())
    portal.config.decoy_options = ["LB0121 - DECOY", "XLB012 - DECOY"]
    portal.catalogue.pop("LB012")                     # the real option is absent
    session, orch = make_orchestrator(portal, commit_timeout=0.3)

    result = orch.process_item(item("LB012", 1))

    assert result.success is False
    assert portal.plus_clicks_by_code.get("LB012", 0) == 0


def test_exact_option_is_chosen_among_near_matches():
    portal = build_portal(["LB012"], latency=Latency.fast())
    portal.config.decoy_options = ["LB0121 - NEAR MATCH", "XLB012 - NEAR MATCH"]
    session, orch = make_orchestrator(portal)
    result = orch.process_item(item("LB012", 1))
    assert result.success is True
    assert portal.procedure_value == "LB012 - SERVICE LB012"


def test_quantity_mismatch_raises_before_any_plus():
    portal = build_portal(["LB012"], latency=Latency.fast())

    class FrozenQuantity(type(portal)):
        pass

    # Portal refuses to accept the typed quantity.
    original = portal.execute_script

    def refuse(script, *args):
        if "HTMLInputElement.prototype" in str(script) and args and args[0].kind == "quantity":
            return True                                  # silently ignored
        return original(script, *args)

    portal.execute_script = refuse
    session, orch = make_orchestrator(portal, commit_timeout=0.3)
    result = orch.process_item(item("LB012", 4))

    assert result.success is False
    assert portal.plus_clicks_by_code.get("LB012", 0) == 0


# ---------------------------------------------------------------------------
# locked quantity
# ---------------------------------------------------------------------------

def test_locked_quantity_uses_one_plus_per_unit():
    portal = build_portal(["CN002"], latency=Latency.fast(), locked_codes={"CN002"})
    session, orch = make_orchestrator(portal)
    result = orch.process_item(item("CN002", 3))
    assert result.success is True
    assert portal.plus_clicks_by_code["CN002"] == 3
    assert len([r for r in portal.rows if r["code"] == "CN002"]) == 3


def test_locked_quantity_stops_after_an_unverifiable_unit():
    portal = build_portal(["CN002"], latency=Latency.fast(), locked_codes={"CN002"})
    session, orch = make_orchestrator(portal, commit_timeout=0.25, reconcile_grace=0.15)

    calls = {"n": 0}
    original = portal._handle_plus

    def flaky():
        calls["n"] += 1
        if calls["n"] == 2:
            portal.config.faults.commit_never = {"CN002"}
        return original()

    portal._handle_plus = flaky
    result = orch.process_item(item("CN002", 4))

    assert result.success is False
    assert result.needs_operator is True
    # unit 1 and unit 2 clicked; the run stops at the unverified unit 2
    assert portal.plus_clicks_by_code["CN002"] == 2


# ---------------------------------------------------------------------------
# cancellation / disconnect
# ---------------------------------------------------------------------------

def test_cancellation_before_plus_dispatches_nothing():
    portal = build_portal(["LB012"], latency=Latency.fast())
    flag = {"cancelled": False}
    session, orch = make_orchestrator(portal, cancel_check=lambda: flag["cancelled"])
    flag["cancelled"] = True
    result = orch.process_item(item("LB012", 1))
    assert result.state == TxState.CANCELLED.value
    assert portal.plus_clicks_by_code.get("LB012", 0) == 0


def test_cancellation_during_verification_never_retries():
    portal = build_portal(["LB012"], latency=Latency.fast())
    portal.config.faults.commit_never = {"LB012"}
    flag = {"cancelled": False}
    session, orch = make_orchestrator(portal, commit_timeout=0.3, reconcile_grace=0.2,
                                      cancel_check=lambda: flag["cancelled"])
    original = portal._handle_plus

    def cancel_after_click():
        result = original()
        flag["cancelled"] = True
        return result

    portal._handle_plus = cancel_after_click
    result = orch.process_item(item("LB012", 1))

    assert result.state == TxState.RECONCILIATION_REQUIRED.value
    assert portal.plus_clicks_by_code["LB012"] == 1


def test_browser_disconnect_gives_a_precise_failure_class():
    portal = build_portal(["LB012"], latency=Latency.fast())
    portal.config.faults.browser_disconnect_after = 2
    session, orch = make_orchestrator(portal, commit_timeout=0.3, reconcile_grace=0.2)
    result = orch.process_item(item("LB012", 1))
    assert result.success is False
    assert result.diagnostic in (Diagnostic.BROWSER_DISCONNECTED.value,
                                 Diagnostic.CONTEXT_LOST.value,
                                 Diagnostic.PRECONDITION_FAILED.value)
    assert portal.plus_clicks_by_code.get("LB012", 0) == 0


# ---------------------------------------------------------------------------
# stale element recovery
# ---------------------------------------------------------------------------

def test_stale_procedure_element_is_reacquired():
    portal = build_portal(["LB012"], latency=Latency.fast())
    portal.config.faults.stale_procedure_on_send_keys = 1
    session, orch = make_orchestrator(portal)
    result = orch.process_item(item("LB012", 1))
    assert result.success is True
    assert session.counters.stale_recoveries >= 1


def test_stale_quantity_element_is_reacquired():
    portal = build_portal(["LB012"], latency=Latency.fast())
    portal.config.faults.stale_quantity_on_set = 1
    session, orch = make_orchestrator(portal)
    result = orch.process_item(item("LB012", 2))
    assert result.success is True


def test_stale_speciality_read_recovers_without_a_plus_loss():
    portal = build_portal(["LB012"], latency=Latency.fast())
    portal.config.faults.probe_unsupported = True
    portal.config.faults.stale_speciality_reads = 2
    session, orch = make_orchestrator(portal)
    result = orch.process_item(item("LB012", 1))
    assert result.success is True
    assert portal.plus_clicks_by_code["LB012"] == 1


# ---------------------------------------------------------------------------
# bill isolation
# ---------------------------------------------------------------------------

def test_bill_isolation_lets_the_same_code_be_added_to_the_next_patient():
    """Bill A's committed LB012 must not suppress bill B's LB012."""
    portal = build_portal(["LB012"], latency=Latency.fast())
    portal.switch_patient("PATIENT A", ip_case="IP-A", bill_number="BILL-A")
    runner = make_runner(portal)
    queue = [patient("PATIENT A", [item("LB012", 1)]),
             patient("PATIENT B", [item("LB012", 1)])]

    def operator_opens_the_next_plan(index, status):
        # Models the operator switching the Chrome tab to the next patient.
        if status == "IN_PROGRESS" and queue[index]["name"] == "PATIENT B":
            portal.switch_patient("PATIENT B", ip_case="IP-B", bill_number="BILL-B")

    result = runner.run(queue, on_patient_status=operator_opens_the_next_plan)

    assert result.summary.items_completed == 2
    assert portal.plus_clicks_by_code["LB012"] == 2
    assert portal._rows_by_patient["PATIENT A"] == [{"code": "LB012", "qty": "1"}]
    assert portal._rows_by_patient["PATIENT B"] == [{"code": "LB012", "qty": "1"}]


def test_batch_stops_when_the_operator_did_not_switch_patient():
    """Writing bill B into patient A's plan is a hard STOP, not a warning."""
    portal = build_portal(["LB012", "CN002"], latency=Latency.fast())
    portal.switch_patient("PATIENT A", ip_case="IP-A", bill_number="BILL-A")
    runner = make_runner(portal)
    queue = [patient("PATIENT A", [item("LB012", 1)]),
             patient("PATIENT B", [item("CN002", 1)])]

    result = runner.run(queue)                 # nobody switches the tab

    assert "STOP" in result.stopped_reason
    assert result.summary.items_completed == 1              # only patient A
    assert "CN002" not in portal.plus_clicks_by_code        # nothing for B
    assert portal._rows_by_patient["PATIENT A"] == [{"code": "LB012", "qty": "1"}]
    assert [p["status"] for p in result.patients] == ["COMPLETED", "STOPPED_WRONG_PATIENT"]


# ---------------------------------------------------------------------------
# state machine unit level
# ---------------------------------------------------------------------------

def _identity(tx_id="B|LB012|LB012|1"):
    return TxIdentity(bill_id="B", internal_code="LB012", portal_value="LB012",
                      unit_index=1, transaction_id=tx_id)


def test_illegal_transition_raises():
    tx = PlusTransaction(identity=_identity(), ledger=DispatchLedger())
    with pytest.raises(IllegalTransition):
        tx.transition(TxState.COMMITTED, "cheating")


def test_reconciliation_required_cannot_become_completed_directly():
    tx = PlusTransaction(identity=_identity(), ledger=DispatchLedger())
    tx.begin_dispatch()
    tx.confirm_browser_click("js")
    tx.begin_wait()
    tx.mark_reconciliation_required("unknown", Diagnostic.COMMIT_TIMEOUT_UNKNOWN)
    with pytest.raises(IllegalTransition):
        tx.transition(TxState.COMPLETED, "cheating")
    assert tx.is_success is False


def test_ledger_blocks_the_second_token():
    ledger = DispatchLedger()
    tx = PlusTransaction(identity=_identity(), ledger=ledger)
    token = tx.begin_dispatch()
    tx.confirm_browser_click("js")
    with pytest.raises(DuplicateDispatchBlocked):
        ledger.authorize(tx.identity.transaction_id)
    assert ledger.dispatch_count(tx.identity.transaction_id) == 1


def test_retry_requires_absence_proof():
    ledger = DispatchLedger()
    tx = PlusTransaction(identity=_identity(), ledger=ledger)
    tx.begin_dispatch()
    tx.confirm_browser_click("js")
    tx.begin_wait()
    tx.mark_reconciliation_required("unknown", Diagnostic.COMMIT_TIMEOUT_UNKNOWN)
    assert tx.allow_retry() is False
    assert ledger.authorize_retry(tx.identity.transaction_id, DispatchProof.UNKNOWN) is False
    assert ledger.authorize_retry(tx.identity.transaction_id,
                                  DispatchProof.PROVEN_DISPATCHED) is False


def test_retry_is_granted_at_most_once():
    ledger = DispatchLedger()
    tx = PlusTransaction(identity=_identity(), ledger=ledger)
    tx.begin_dispatch()
    tx.abort_before_click("stale", Diagnostic.CLICK_EXCEPTION_PRE_DISPATCH)
    assert tx.allow_retry() is True
    tx.begin_dispatch()
    tx.abort_before_click("stale again", Diagnostic.CLICK_EXCEPTION_PRE_DISPATCH)
    assert tx.allow_retry() is False
    assert ledger.retry_grants(tx.identity.transaction_id) == 1


def test_journal_records_every_transaction(tmp_path):
    journal_path = tmp_path / "transaction_journal.jsonl"
    journal = TransactionJournal(path=str(journal_path))
    portal = build_portal(["LB012"], latency=Latency.fast())
    session, orch = make_orchestrator(portal, journal=journal)
    orch.process_item(item("LB012", 1))
    assert journal_path.exists()
    lines = journal_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    import json
    record = json.loads(lines[0])
    assert record["state"] == "COMPLETED"
    assert record["plus_dispatch_count"] == 1
    assert record["identity"]["internal_code"] == "LB012"


# ---------------------------------------------------------------------------
# PT / WC post-Plus row recognition
#
# Live 39706 run: the Procedure control, the Plus dispatch and the table
# mutation all worked, yet three items stalled:
#
#   PT004 qty=24  PLUS-DISPATCHED  rows 108 -> 110  matching qty 0 -> 0
#   PT005 qty=41  PLUS-DISPATCHED  rows 110 -> 112  matching qty 0 -> 0
#   WC001 qty=2   PLUS-DISPATCHED  rows 128 -> 130  matching qty 0 -> 0
#   -> TX-ROW-CODE-MISMATCH -> RECONCILIATION_REQUIRED, no second Plus
#
# The verifier was right to refuse: it was handed ``code: ""``.  The row-code
# EXTRACTOR, not the matcher, is the defect - its hardcoded family list
# (LB|RI|CI|CN|RP|GP|CC|C) is narrower than the code registry, which has
# always contained PT and WC.  row_matches_code("PT004 - ...", "PT004") was
# already True; the cell text never reached it.
# ---------------------------------------------------------------------------

PT_WC_CASES = [("PT004", "24"), ("PT005", "41"), ("WC001", "2")]


def test_pt_and_wc_rows_are_extracted_by_the_canonical_token_regex():
    """The extractor must see what the matcher already accepts."""
    from cghs.dom import _CODE_TOKEN_RE, row_matches_code

    for code, _qty in PT_WC_CASES:
        cell = f"{code} - portal description"
        assert row_matches_code(cell, code) is True, \
            f"{code}: matcher regressed"
        assert _CODE_TOKEN_RE.findall(cell), \
            f"{code}: extractor dropped the code cell -> verifier sees code=''"


def test_compact_probe_and_python_row_readers_agree_on_every_family():
    """Section 12: one side must never recognise a family the other drops."""
    import re as _re
    from cghs.dom import PROBE_JS, _CODE_TOKEN_RE
    from cghs.controllers import TableReader   # noqa: F401  (import guard)

    js = _re.search(r"var codeRe = /(?P<body>.+?)/[a-z]*;", PROBE_JS)
    assert js, "PROBE_JS no longer declares codeRe"
    probe_re = _re.compile(js.group("body"), _re.IGNORECASE)

    controllers_src = (REPO / "cghs" / "controllers.py").read_text(encoding="utf-8")

    for code, _qty in PT_WC_CASES + [("LB012", "1"), ("CN002", "1"),
                                     ("C001", "1"), ("CC001", "1"),
                                     ("RI034", "1"), ("DRGU100", "1"),
                                     ("CNSU100", "1")]:
        cell = f"{code} - portal description"
        assert bool(probe_re.search(cell)) is bool(_CODE_TOKEN_RE.search(cell)), \
            f"{code}: compact probe and Python reader disagree"

    for family in ("PT", "WC"):
        assert family in controllers_src, \
            f"the Selenium row reader still drops the {family} family"


@pytest.mark.parametrize("code,qty", PT_WC_CASES)
def test_pt_wc_row_is_recognised_and_verified_after_one_plus(code, qty):
    """End to end: one Plus, exact row identified, quantity verified."""
    portal = build_portal([code], latency=Latency.fast())
    portal.quantity_locked = False
    # Force the Selenium row reader: the compact-probe emulation hands back
    # the row model verbatim, so only this path actually runs production's
    # row-code extraction against real <td> text - which is where the live
    # PT/WC rows were being dropped.
    portal.config.faults.probe_unsupported = True
    session, orch = make_orchestrator(portal, commit_timeout=0.6,
                                      reconcile_grace=0.2)

    result = orch.process_item(item(code, int(qty)))

    assert result.success is True, result.state
    assert result.state == TxState.COMPLETED.value
    assert len(portal.plus_clicks) == 1, "exactly one Plus must be dispatched"
    assert any(r["code"] == code for r in portal.rows)


@pytest.mark.parametrize("code,qty", PT_WC_CASES)
def test_pt_wc_rows_are_counted_by_the_duplicate_guard(code, qty):
    """A PT/WC row already on the portal must be SEEN, or the duplicate
    guard would happily add it a second time."""
    portal = build_portal([code], latency=Latency.fast())
    portal.seed_rows([(code, "1")])
    portal.config.faults.probe_unsupported = True
    session, _ = make_orchestrator(portal)
    session.ensure_context()

    from cghs.controllers import TableReader
    from cghs.dom import row_matches_code
    rows = TableReader(session).snapshot().items
    assert any(row_matches_code(r.get("code", ""), code) for r in rows), \
        f"{code}: an existing portal row is invisible to the duplicate guard"


def test_pt_wc_change_does_not_relax_prefix_collision_safety():
    """Section 7: exact matching, still no substring/prefix widening."""
    from cghs.dom import row_matches_code
    assert row_matches_code("CC001", "C001") is False
    assert row_matches_code("C001", "C001") is True
    assert row_matches_code("LB0121", "LB012") is False
    assert row_matches_code("PT0041", "PT004") is False
    assert row_matches_code("WC0011", "WC001") is False
    assert row_matches_code("PT005", "PT004") is False
    assert row_matches_code("WC001", "C001") is False
    assert row_matches_code("drugs(DRGU100-None)", "DRUG100") is True


def test_table_change_without_the_target_row_still_reconciles():
    """Section 8: a mutated table alone is NEVER success, PT/WC included."""
    portal = build_portal(["PT004"], latency=Latency.fast())
    portal.config.faults.unrelated_mutation = {"PT004"}
    portal.config.faults.probe_unsupported = True
    session, orch = make_orchestrator(portal, commit_timeout=0.4,
                                      reconcile_grace=0.2)

    result = orch.process_item(item("PT004", 1))

    assert result.success is False
    assert any(r["code"] == "ZZ999" for r in portal.rows)
    assert not any(r["code"] == "PT004" for r in portal.rows)
