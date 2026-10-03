"""Explicit Plus-dispatch transaction state machine.

This module exists because of the root-cause defect found in the baseline:

    [TX-UNKNOWN]
    [TX-UNKNOWN-ASSUMED-COMMITTED]  -> mark COMMITTED -> mark COMPLETED -> True

An unverifiable portal result was converted into success.  That inflated the
success counters and hid rows that were never added.

THE RULE IMPLEMENTED HERE
-------------------------
A Plus/Add that was dispatched but cannot be verified is **never** success.
It becomes :data:`TxState.RECONCILIATION_REQUIRED`, the transaction freezes, no
second Plus is issued, and the item is surfaced for an operator.

THE INVARIANT ENFORCED HERE
---------------------------
A transaction may never cause two physical Plus mutations unless the state
machine has *conclusively established* that the first mutation did not occur
(:data:`DispatchProof.PROVEN_NOT_DISPATCHED`).  The ledger is the single
authority; controllers cannot click without an authorisation token.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class TxState(str, Enum):
    PREPARED = "PREPARED"
    DUPLICATE_PROVEN = "DUPLICATE_PROVEN"
    DISPATCH_INTENT = "DISPATCH_INTENT"
    DISPATCHED = "DISPATCHED"
    WAITING_FOR_COMMIT = "WAITING_FOR_COMMIT"
    COMMITTED = "COMMITTED"
    COMPLETED = "COMPLETED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    FAILED_BEFORE_DISPATCH = "FAILED_BEFORE_DISPATCH"
    FAILED_AFTER_DISPATCH = "FAILED_AFTER_DISPATCH"
    CANCELLED = "CANCELLED"


#: States from which no further portal mutation may be attempted.
TERMINAL_STATES = frozenset({
    TxState.COMPLETED,
    TxState.DUPLICATE_PROVEN,
    TxState.RECONCILIATION_REQUIRED,
    TxState.FAILED_AFTER_DISPATCH,
    TxState.CANCELLED,
})

#: States that count as a successful portal commit.
SUCCESS_STATES = frozenset({TxState.COMMITTED, TxState.COMPLETED})

LEGAL_TRANSITIONS: Dict[TxState, frozenset] = {
    TxState.PREPARED: frozenset({
        TxState.DUPLICATE_PROVEN, TxState.DISPATCH_INTENT,
        TxState.FAILED_BEFORE_DISPATCH, TxState.CANCELLED,
    }),
    TxState.DISPATCH_INTENT: frozenset({
        TxState.DISPATCHED, TxState.FAILED_BEFORE_DISPATCH,
        TxState.RECONCILIATION_REQUIRED, TxState.CANCELLED,
    }),
    TxState.DISPATCHED: frozenset({
        TxState.WAITING_FOR_COMMIT, TxState.RECONCILIATION_REQUIRED,
        TxState.FAILED_AFTER_DISPATCH,
    }),
    TxState.WAITING_FOR_COMMIT: frozenset({
        TxState.COMMITTED, TxState.RECONCILIATION_REQUIRED,
        TxState.FAILED_AFTER_DISPATCH,
    }),
    TxState.COMMITTED: frozenset({TxState.COMPLETED}),
    # A frozen transaction may only be closed by *proof*, never by assumption.
    TxState.RECONCILIATION_REQUIRED: frozenset({TxState.COMMITTED}),
    # Retry is permitted exactly once and only with dispatch-absence proof.
    TxState.FAILED_BEFORE_DISPATCH: frozenset({TxState.PREPARED}),
    TxState.FAILED_AFTER_DISPATCH: frozenset(),
    TxState.COMPLETED: frozenset(),
    TxState.DUPLICATE_PROVEN: frozenset(),
    TxState.CANCELLED: frozenset(),
}


class DispatchProof(str, Enum):
    """Evidence quality about whether the browser actually mutated the portal."""

    UNKNOWN = "UNKNOWN"
    PROVEN_NOT_DISPATCHED = "PROVEN_NOT_DISPATCHED"
    PROVEN_DISPATCHED = "PROVEN_DISPATCHED"


class Diagnostic(str, Enum):
    OK = "TX-OK"
    DUPLICATE_PROVEN = "TX-DUPLICATE-PROVEN"
    PRECONDITION_FAILED = "TX-PRECONDITION-FAILED"
    REASON_MISSING = "TX-REASON-REQUIRED-MISSING"
    SPECIALITY_LOCKED = "TX-SPECIALITY-LOCKED"
    PLUS_NOT_FOUND = "TX-PLUS-NOT-FOUND"
    CLICK_EXCEPTION_PRE_DISPATCH = "TX-CLICK-EXCEPTION-PRE-DISPATCH"
    CLICK_EXCEPTION_AMBIGUOUS = "TX-CLICK-EXCEPTION-AMBIGUOUS"
    STALE_AFTER_DISPATCH = "TX-STALE-AFTER-DISPATCH"
    COMMIT_TIMEOUT_UNKNOWN = "TX-COMMIT-TIMEOUT-UNKNOWN"
    COMMIT_VERIFIED = "TX-COMMIT-VERIFIED"
    COMMIT_VERIFIED_LATE = "TX-COMMIT-VERIFIED-LATE-RECONCILE"
    ROW_CODE_MISMATCH = "TX-ROW-CODE-MISMATCH"
    ROW_QTY_MISMATCH = "TX-ROW-QTY-MISMATCH"
    CANCELLED_PRE_DISPATCH = "TX-CANCELLED-PRE-DISPATCH"
    CANCELLED_POST_DISPATCH = "TX-CANCELLED-POST-DISPATCH"
    BROWSER_DISCONNECTED = "TX-BROWSER-DISCONNECTED"
    CONTEXT_LOST = "TX-PORTAL-CONTEXT-LOST"
    AMBIGUOUS_PATIENT_TAB = "TX-AMBIGUOUS-PATIENT-TAB"


class IllegalTransition(RuntimeError):
    pass


class DuplicateDispatchBlocked(RuntimeError):
    """Raised when something tries to click Plus twice for one transaction."""


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------

class DispatchLedger:
    """Single authority for 'may this transaction physically click Plus?'.

    Thread-safe because the Qt worker thread and any future supervisor must not
    be able to race a second dispatch through.
    """

    def __init__(self, counters=None):
        self._lock = threading.RLock()
        self._allowed: Dict[str, int] = {}
        self._dispatched: Dict[str, int] = {}
        self._retry_grants: Dict[str, int] = {}
        self._tokens: Dict[str, object] = {}
        self.blocked_attempts = 0
        self.counters = counters

    # ---- queries -----------------------------------------------------
    def dispatch_count(self, tx_id: str) -> int:
        with self._lock:
            return self._dispatched.get(tx_id, 0)

    def retry_grants(self, tx_id: str) -> int:
        with self._lock:
            return self._retry_grants.get(tx_id, 0)

    def register(self, tx_id: str):
        with self._lock:
            self._allowed.setdefault(tx_id, 1)
            self._dispatched.setdefault(tx_id, 0)

    # ---- authorisation -----------------------------------------------
    def authorize(self, tx_id: str) -> object:
        """Return a one-shot token permitting exactly one physical click."""
        with self._lock:
            self.register(tx_id)
            if self._dispatched[tx_id] >= self._allowed[tx_id]:
                self.blocked_attempts += 1
                if self.counters is not None:
                    self.counters.duplicate_plus_attempts_blocked += 1
                raise DuplicateDispatchBlocked(
                    f"{tx_id}: dispatched={self._dispatched[tx_id]} "
                    f"allowed={self._allowed[tx_id]} - duplicate Plus BLOCKED")
            token = object()
            self._tokens[tx_id] = token
            return token

    def record_physical_dispatch(self, tx_id: str, token: object):
        with self._lock:
            if self._tokens.get(tx_id) is not token:
                self.blocked_attempts += 1
                if self.counters is not None:
                    self.counters.duplicate_plus_attempts_blocked += 1
                raise DuplicateDispatchBlocked(f"{tx_id}: stale/unknown dispatch token")
            self._tokens.pop(tx_id, None)
            self._dispatched[tx_id] = self._dispatched.get(tx_id, 0) + 1
            if self.counters is not None:
                self.counters.plus_dispatches += 1
            if self._dispatched[tx_id] > self._allowed[tx_id]:  # pragma: no cover - guard
                raise DuplicateDispatchBlocked(
                    f"{tx_id}: INVARIANT VIOLATION dispatched={self._dispatched[tx_id]}")

    def release(self, tx_id: str, token: object):
        """Return an unused token (the click was never issued)."""
        with self._lock:
            if self._tokens.get(tx_id) is token:
                self._tokens.pop(tx_id, None)

    def authorize_retry(self, tx_id: str, proof: DispatchProof) -> bool:
        """Grant one extra dispatch, ONLY with conclusive absence proof."""
        with self._lock:
            if proof is not DispatchProof.PROVEN_NOT_DISPATCHED:
                return False
            if self._retry_grants.get(tx_id, 0) >= 1:
                return False
            self._retry_grants[tx_id] = self._retry_grants.get(tx_id, 0) + 1
            self._allowed[tx_id] = self._allowed.get(tx_id, 1) + 1
            if self.counters is not None:
                self.counters.retries += 1
            return True

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "dispatched": dict(self._dispatched),
                "allowed": dict(self._allowed),
                "retry_grants": dict(self._retry_grants),
                "blocked_attempts": self.blocked_attempts,
            }


# ---------------------------------------------------------------------------
# Transaction
# ---------------------------------------------------------------------------

@dataclass
class TxIdentity:
    bill_id: str
    internal_code: str
    portal_value: str
    unit_index: int
    transaction_id: str
    run_id: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class PlusTransaction:
    identity: TxIdentity
    ledger: DispatchLedger
    requested_quantity: int = 1
    expected_portal_quantity: int = 1
    state: TxState = TxState.PREPARED
    diagnostic: Diagnostic = Diagnostic.OK
    dispatch_proof: DispatchProof = DispatchProof.UNKNOWN
    before_rows: int = 0
    before_matching_qty: int = 0
    committed_row: Optional[Dict[str, Any]] = None
    reconciliation_outcome: str = ""
    last_error: str = ""
    events: List[Dict[str, Any]] = field(default_factory=list)
    timings: Dict[str, float] = field(default_factory=dict)
    _token: Optional[object] = None

    def __post_init__(self):
        self.ledger.register(self.identity.transaction_id)
        self._record(self.state, "transaction prepared")

    # ---- state machine ------------------------------------------------
    def _record(self, state: TxState, message: str):
        self.events.append({
            "at": time.time(),
            "state": state.value,
            "message": message,
            "diagnostic": self.diagnostic.value,
        })

    def transition(self, new_state: TxState, message: str = "",
                   diagnostic: Optional[Diagnostic] = None):
        if new_state not in LEGAL_TRANSITIONS[self.state]:
            raise IllegalTransition(
                f"{self.identity.transaction_id}: {self.state.value} -> {new_state.value} is illegal")
        self.state = new_state
        if diagnostic is not None:
            self.diagnostic = diagnostic
        self._record(new_state, message)
        return self

    # ---- dispatch -----------------------------------------------------
    def begin_dispatch(self) -> object:
        """Register dispatch INTENT and take the one-shot click token.

        Intent is recorded *before* the click so a crash between intent and the
        browser call is reconcilable; the token is what actually permits the
        physical mutation.
        """
        token = self.ledger.authorize(self.identity.transaction_id)
        self._token = token
        self.transition(TxState.DISPATCH_INTENT, "dispatch intent registered")
        return token

    def confirm_browser_click(self, how: str = ""):
        """The browser click call RETURNED - this is not success, only dispatch."""
        self.ledger.record_physical_dispatch(self.identity.transaction_id, self._token)
        self._token = None
        self.dispatch_proof = DispatchProof.PROVEN_DISPATCHED
        self.transition(TxState.DISPATCHED, f"browser click returned ({how})")

    def abort_before_click(self, reason: str, diagnostic: Diagnostic,
                           cancelled: bool = False):
        """The click was never issued - safe, retryable state."""
        if self._token is not None:
            self.ledger.release(self.identity.transaction_id, self._token)
            self._token = None
        self.dispatch_proof = DispatchProof.PROVEN_NOT_DISPATCHED
        self.last_error = reason
        target = TxState.CANCELLED if cancelled else TxState.FAILED_BEFORE_DISPATCH
        self.transition(target, reason, diagnostic)

    def mark_ambiguous_click(self, reason: str):
        """Click raised and we cannot prove whether the portal saw it."""
        if self._token is not None:
            # The token is consumed: we must behave as if a mutation may exist.
            self.ledger.record_physical_dispatch(self.identity.transaction_id, self._token)
            self._token = None
        self.dispatch_proof = DispatchProof.UNKNOWN
        self.last_error = reason
        self.transition(TxState.RECONCILIATION_REQUIRED, reason,
                        Diagnostic.CLICK_EXCEPTION_AMBIGUOUS)

    # ---- outcomes -----------------------------------------------------
    def begin_wait(self):
        self.transition(TxState.WAITING_FOR_COMMIT, "waiting for portal commit")

    def mark_committed(self, row: Dict[str, Any], diagnostic: Diagnostic = Diagnostic.COMMIT_VERIFIED):
        self.committed_row = row
        self.transition(TxState.COMMITTED, f"exact row verified {row}", diagnostic)
        self.transition(TxState.COMPLETED, "transaction completed")

    def mark_reconciliation_required(self, reason: str, diagnostic: Diagnostic):
        self.reconciliation_outcome = reason
        self.last_error = reason
        self.transition(TxState.RECONCILIATION_REQUIRED, reason, diagnostic)

    def mark_duplicate(self, reason: str):
        self.transition(TxState.DUPLICATE_PROVEN, reason, Diagnostic.DUPLICATE_PROVEN)

    def allow_retry(self) -> bool:
        """Retry is permitted ONLY from a proven pre-dispatch failure."""
        if self.state is not TxState.FAILED_BEFORE_DISPATCH:
            return False
        if self.dispatch_proof is not DispatchProof.PROVEN_NOT_DISPATCHED:
            return False
        if not self.ledger.authorize_retry(self.identity.transaction_id,
                                           self.dispatch_proof):
            return False
        self.transition(TxState.PREPARED, "retry authorised by dispatch-absence proof")
        return True

    # ---- reporting ----------------------------------------------------
    @property
    def is_success(self) -> bool:
        return self.state in SUCCESS_STATES

    @property
    def needs_operator(self) -> bool:
        return self.state is TxState.RECONCILIATION_REQUIRED

    def as_record(self) -> Dict[str, Any]:
        return {
            "identity": self.identity.as_dict(),
            "state": self.state.value,
            "diagnostic": self.diagnostic.value,
            "dispatch_proof": self.dispatch_proof.value,
            "requested_quantity": self.requested_quantity,
            "expected_portal_quantity": self.expected_portal_quantity,
            "before_rows": self.before_rows,
            "before_matching_qty": self.before_matching_qty,
            "committed_row": self.committed_row,
            "plus_dispatch_count": self.ledger.dispatch_count(self.identity.transaction_id),
            "retry_grants": self.ledger.retry_grants(self.identity.transaction_id),
            "reconciliation_outcome": self.reconciliation_outcome,
            "timings": self.timings,
            "events": self.events,
            "last_error": self.last_error,
        }


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------

class TransactionJournal:
    """Append-only JSONL audit trail (compatible with the existing file)."""

    def __init__(self, path: Optional[str] = None, enabled: bool = True):
        self.path = path or os.path.join(os.getcwd(), "transaction_journal.jsonl")
        self.enabled = enabled
        self.records: List[Dict[str, Any]] = []

    def write(self, tx: PlusTransaction):
        record = tx.as_record()
        self.records.append(record)
        if not self.enabled:
            return
        try:
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, default=str) + "\n")
        except Exception:
            # Journalling must never break the portal run.
            pass
