"""Deterministic property / fuzz testing.

A seeded RNG generates random portals (latency, faults, locked codes, decoy
options, pre-existing rows) and random item plans.  Every run must satisfy the
same invariants, the most important being:

    NO TRANSACTION ID EVER DISPATCHES A SECOND PHYSICAL PLUS.

Seeds are fixed, so a failure is reproducible by seed number.
"""

from __future__ import annotations

import random

import pytest

from cghs.txstate import DispatchProof, TxState
from tests.support.fake_portal import Latency, build_portal
from tests.support.harness import item, make_runner, patient

SEEDS = list(range(40))
SUCCESS_STATES = {TxState.COMPLETED, TxState.DUPLICATE_PROVEN}


def build_random_case(seed: int):
    rng = random.Random(seed)
    size = rng.randint(1, 14)
    plan = ["FZ%04d" % rng.randrange(0, 60) for _ in range(size)]
    unique = sorted(set(plan))

    latency = rng.choice([Latency.instant(), Latency.fast(), Latency.fast(),
                          Latency.medium()])
    locked = {c for c in unique if rng.random() < 0.15}
    portal = build_portal(unique, latency=latency, locked_codes=locked)

    faults = portal.config.faults
    for code in unique:
        roll = rng.random()
        if roll < 0.08:
            faults.commit_never.add(code)
        elif roll < 0.14:
            faults.click_raises_stale.add(code)
        elif roll < 0.19:
            faults.click_raises_after_dispatch.add(code)
        elif roll < 0.24:
            faults.commit_late.add(code)
        elif roll < 0.28:
            faults.commit_wrong_qty.add(code)
        elif roll < 0.31:
            faults.commit_wrong_code[code] = "ZZ%03d" % rng.randrange(999)
        elif roll < 0.34:
            faults.unrelated_mutation.add(code)
    faults.late_commit_delay = rng.choice([0.05, 0.15, 0.3])
    if rng.random() < 0.2:
        faults.probe_unsupported = True
    if rng.random() < 0.15:
        faults.virtualized_table = True
    if rng.random() < 0.15:
        faults.stale_procedure_on_send_keys = rng.randint(1, 2)
    if rng.random() < 0.1:
        faults.reason_absent = True
    if rng.random() < 0.2:
        portal.config.decoy_options = [f"{c}1 - DECOY" for c in unique[:2]]
    if rng.random() < 0.25:
        portal.seed_rows([("PRE%03d" % i, "1") for i in range(rng.randint(1, 30))])

    items = [item(code, rng.randint(1, 3)) for code in plan]
    return portal, items


@pytest.mark.fuzz
@pytest.mark.parametrize("seed", SEEDS)
def test_invariants_hold_for_every_generated_portal(seed):
    portal, items = build_random_case(seed)
    runner = make_runner(portal, commit_timeout=0.6, reconcile_grace=0.3)

    result = runner.run([patient("FUZZ", items)])
    transactions = runner.orchestrator.transactions

    # --- INVARIANT 1: one transaction id never dispatches twice ------------
    per_tx = {}
    for tx in transactions:
        tx_id = tx.identity.transaction_id
        per_tx[tx_id] = per_tx.get(tx_id, 0) + tx.ledger.dispatch_count(tx_id)
    for tx_id, count in per_tx.items():
        assert count <= 1, f"seed {seed}: {tx_id} dispatched {count} times"

    # --- INVARIANT 2: physical clicks never exceed authorised dispatches ---
    authorised = sum(tx.ledger.dispatch_count(tx.identity.transaction_id)
                     for tx in transactions)
    physical = sum(portal.plus_clicks_by_code.values())
    assert physical == authorised, f"seed {seed}: {physical} clicks vs {authorised} authorised"

    # --- INVARIANT 3: success requires a verified row ----------------------
    for tx in transactions:
        if tx.is_success:
            assert tx.state in SUCCESS_STATES, (seed, tx.state)
            if tx.state is TxState.COMPLETED:
                assert tx.committed_row is not None, (seed, tx.identity.transaction_id)

    # --- INVARIANT 4: unknown outcomes are frozen, never counted -----------
    for tx in transactions:
        if tx.state is TxState.RECONCILIATION_REQUIRED:
            assert tx.is_success is False
            assert tx.needs_operator is True

    # --- INVARIANT 5: a retry implies PROVEN absence of the first mutation -
    for tx in transactions:
        if tx.ledger.retry_grants(tx.identity.transaction_id):
            assert tx.dispatch_proof is DispatchProof.PROVEN_NOT_DISPATCHED, seed

    # --- INVARIANT 6: the summary accounts for every item ------------------
    summary = result.summary
    expected_total = len({(i["code"], i["qty"]) for i in items})
    assert summary.items_total <= len(items)
    assert summary.items_total >= len({i["code"] for i in items})
    assert (summary.items_completed + summary.items_failed
            + summary.items_reconciliation_required
            + summary.items_skipped_duplicate) == summary.items_total

    # --- INVARIANT 7: no state is ever reported outside the vocabulary -----
    allowed = {s.value for s in TxState}
    for record in summary.items:
        assert record["state"] in allowed, (seed, record["state"])


@pytest.mark.fuzz
@pytest.mark.parametrize("seed", SEEDS[:20])
def test_rerunning_the_same_plan_never_re_adds_a_verified_row(seed):
    """Idempotence: what the portal PROVABLY holds is never added twice.

    Items whose outcome could not be proved are deliberately NOT covered by
    this invariant - the tool reports them as RECONCILIATION_REQUIRED and the
    operator decides, which is the whole point of freezing them.
    """
    portal, items = build_random_case(seed)
    runner = make_runner(portal, commit_timeout=0.6, reconcile_grace=0.3)
    queue = [patient("FUZZ", items)]

    first_result = runner.run(queue)
    after_first = dict(portal.plus_clicks_by_code)
    verified = {record["final_code"]: record["quantity"]
                for record in first_result.summary.items
                if record["state"] == TxState.COMPLETED.value}

    runner.run(queue)                       # same plan, same portal

    for code in verified:
        assert portal.plus_clicks_by_code.get(code, 0) == after_first.get(code, 0), \
            f"seed {seed}: {code} was re-clicked after a VERIFIED commit"


@pytest.mark.fuzz
@pytest.mark.parametrize("seed", [5, 11, 31])
def test_an_unreadable_table_can_never_produce_a_success(seed):
    """If the portal exposes no readable rows, nothing may be counted as added.

    Seeds 11 and 5 generate a portal with both the compact probe disabled and
    a virtualized grid, i.e. no way at all to read the Treatment Plan.  The
    only correct behaviour is to freeze every item for the operator.
    """
    portal, items = build_random_case(seed)
    assert portal.config.faults.probe_unsupported
    assert portal.config.faults.virtualized_table
    runner = make_runner(portal, commit_timeout=0.4, reconcile_grace=0.2)

    result = runner.run([patient("BLIND", items)])

    assert result.summary.items_completed == 0
    assert result.summary.items_reconciliation_required == result.summary.items_total
    for record in result.summary.items:
        assert record["state"] != TxState.COMPLETED.value


def test_state_machine_rejects_every_illegal_transition():
    """Exhaustive check of the transition table."""
    from cghs.txstate import LEGAL_TRANSITIONS, IllegalTransition, PlusTransaction
    from cghs.txstate import DispatchLedger, TxIdentity

    for source in TxState:
        for target in TxState:
            tx = PlusTransaction(
                identity=TxIdentity(bill_id="B", internal_code="C", portal_value="C",
                                    unit_index=1, transaction_id=f"{source}->{target}"),
                ledger=DispatchLedger())
            tx.state = source
            legal = target in LEGAL_TRANSITIONS.get(source, set())
            if legal:
                tx.transition(target, "exhaustive check")
                assert tx.state is target
            else:
                with pytest.raises(IllegalTransition):
                    tx.transition(target, "exhaustive check")
                assert tx.state is source
