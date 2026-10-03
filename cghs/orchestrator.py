"""Treatment Plan orchestration - deterministic, verified, exactly-one-Plus.

Canonical owner of ``TreatmentPlanOrchestrator`` (moved out of ``app.py``).

ROOT-CAUSE FIX
--------------
The baseline ended an unverifiable Plus with::

    [TX-UNKNOWN-ASSUMED-COMMITTED] -> COMMITTED -> COMPLETED -> return True

This orchestrator instead freezes the transaction, re-reads the portal,
reconciles the exact patient / code / quantity / row identity and, when the
outcome is still unknown, leaves it in ``RECONCILIATION_REQUIRED``:

* not completed,
* not counted as success,
* no second Plus,
* surfaced with a precise diagnostic.

Portal mutations are strictly sequential (6.10) - nothing here is parallelised.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    WebDriverException,
)

from .controllers import (
    AmountController,
    CommitVerifier,
    EnhancementReasonController,
    MutationWatch,
    PlusButtonController,
    ProcedureNameController,
    ProcedureSelector,
    QuantityController,
    RowSnapshot,
    SpecialityClearController,
    SpecialitySynchronizer,
    StageContextProbe,
    TableReader,
    is_placeholder,
)
from .dom import PortalContextLost, is_browser_disconnect
from .locators import portal_input_value
from .session import PortalSession
from .tabs import AmbiguousPatientTab, NoPatientTab, PatientNotSwitched
from .telemetry import BatchPerformanceSummary, EnterpriseLogger, ItemTelemetry
from .txstate import (
    Diagnostic,
    DispatchProof,
    PlusTransaction,
    TxIdentity,
    TxState,
)

AMOUNT_BASED_CODES = {"DRUG100", "CNSU100"}
AMOUNT_PROCEDURE_NAMES = {"DRUG100": "DRUGS", "CNSU100": "CONSUMABLES"}


@dataclass
class ItemResult:
    code: str
    requested_qty: int
    state: str
    diagnostic: str
    success: bool = False
    needs_operator: bool = False
    detail: str = ""
    transactions: List[PlusTransaction] = field(default_factory=list)
    telemetry: Optional[ItemTelemetry] = None

    @property
    def plus_dispatches(self) -> int:
        return sum(tx.ledger.dispatch_count(tx.identity.transaction_id)
                   for tx in self.transactions)


#: sentinel meaning "the reason stage was NOT proven valid and must be driven"
_REASON_MUST_RUN = object()


class TreatmentPlanOrchestrator:
    """Session-scoped.  Created ONCE per batch, reused for every item."""

    def __init__(self, session: PortalSession, logger: Optional[EnterpriseLogger] = None,
                 commit_timeout: float = 6.0, reconcile_grace: float = 1.5):
        self.session = session
        self.logger = logger or session.logger
        self.commit_timeout = commit_timeout
        self.reconcile_grace = reconcile_grace

        self.table = TableReader(session)
        self.watch = MutationWatch(session)
        self.proc_sel = ProcedureSelector(session)
        self.spec_sync = SpecialitySynchronizer(session)
        self.spec_clear = SpecialityClearController(session)
        self.qty_ctrl = QuantityController(session)
        self.proc_name_ctrl = ProcedureNameController(session)
        self.amount_ctrl = AmountController(session)
        self.reason_ctrl = EnhancementReasonController(session)
        self.stage_probe = StageContextProbe(session)
        # The absence-proof settle window must cover the portal's own commit
        # latency, otherwise 'no row yet' would be misread as 'never clicked'.
        self.plus_ctrl = PlusButtonController(
            session, self.table, self.watch,
            settle_s=max(0.25, min(reconcile_grace, commit_timeout / 2.0)))
        self.verifier = CommitVerifier(session, self.table, self.watch)

        self.last_code: Optional[str] = None
        self.telemetry: List[ItemTelemetry] = []
        self.transactions: List[PlusTransaction] = []

    # ------------------------------------------------------------------
    # identity helpers
    # ------------------------------------------------------------------
    def _tx_id(self, code: str, unit_idx: int) -> str:
        bill = self.session.bill_id or "current"
        return f"{bill}|{code.upper()}|{portal_input_value(code)}|{unit_idx}"

    def _identity(self, code: str, unit_idx: int) -> TxIdentity:
        return TxIdentity(
            bill_id=self.session.bill_id or "current",
            internal_code=code.upper(),
            portal_value=portal_input_value(code),
            unit_index=unit_idx,
            transaction_id=self._tx_id(code, unit_idx),
            run_id=self.session.run_id,
        )

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def process_item(self, item: Dict[str, Any]) -> ItemResult:
        code = (item.get("code") or "").strip()
        qty = int(item.get("qty", 1) or 1)
        amount = item.get("amount")
        is_amount_based = amount is not None or code.upper() in AMOUNT_BASED_CODES
        bill_qty = 1 if is_amount_based else qty

        started = time.time()
        before_counters = self.session.counters.copy()
        tele = ItemTelemetry(
            run_id=self.session.run_id, bill_id=self.session.bill_id or "",
            session_id=self.session.session_id, final_code=code.upper(), quantity=bill_qty,
            stage="START")
        result = ItemResult(code=code.upper(), requested_qty=bill_qty,
                            state="PENDING", diagnostic=Diagnostic.OK.value, telemetry=tele)

        try:
            result = self._process_item_inner(item, code, bill_qty, amount,
                                              is_amount_based, tele, result)
        except (AmbiguousPatientTab, NoPatientTab) as exc:
            result.state = TxState.FAILED_BEFORE_DISPATCH.value
            result.diagnostic = Diagnostic.AMBIGUOUS_PATIENT_TAB.value
            result.detail = str(exc)
            self.logger.error(f"[STOP] {exc}")
        except PortalContextLost as exc:
            self.session.invalidate_context(f"context lost: {exc}")
            result.state = TxState.FAILED_BEFORE_DISPATCH.value
            result.diagnostic = Diagnostic.CONTEXT_LOST.value
            result.detail = str(exc)
            self.logger.error(f"[CONTEXT-LOST] {code}: {exc}")
        except (NoSuchElementException, StaleElementReferenceException,
                TimeoutError, ValueError) as exc:
            # Caught BEFORE WebDriverException on purpose: these are subclasses
            # of it, and a missing/stale control is a precondition failure, not
            # a dead browser.
            if result.state in ("PENDING", ""):
                result.state = TxState.FAILED_BEFORE_DISPATCH.value
                result.diagnostic = Diagnostic.PRECONDITION_FAILED.value
            result.detail = str(exc)
            self.logger.error(f"[PRECONDITION] {code}: {exc}")
        except WebDriverException as exc:
            self.session.invalidate_context(f"webdriver error: {exc}")
            result.state = TxState.FAILED_BEFORE_DISPATCH.value
            result.diagnostic = (Diagnostic.BROWSER_DISCONNECTED.value
                                 if is_browser_disconnect(exc)
                                 else Diagnostic.CONTEXT_LOST.value)
            result.detail = str(exc)
            self.logger.error(f"[BROWSER] {code}: {exc}")
        finally:
            delta = self.session.counters.delta(before_counters)
            tele.elapsed_ms = round((time.time() - started) * 1000.0, 3)
            tele.state = result.state
            tele.dom_calls = delta.get("dom_calls", 0)
            tele.execute_script_calls = delta.get("execute_script_calls", 0)
            tele.find_elements_calls = delta.get("find_elements_calls", 0)
            tele.locator_cache_hit = delta.get("locator_cache_hits", 0)
            tele.locator_cache_miss = delta.get("locator_cache_misses", 0)
            tele.frame_cache_hit = delta.get("frame_cache_hits", 0)
            tele.frame_cache_miss = delta.get("frame_cache_misses", 0)
            tele.tab_cache_hit = delta.get("tab_cache_hits", 0)
            tele.tab_cache_miss = delta.get("tab_cache_misses", 0)
            tele.verification_outcome = result.diagnostic
            if result.transactions:
                tele.reconciliation_outcome = result.transactions[-1].reconciliation_outcome
            self.telemetry.append(tele)
            self.logger.info(
                f"[ITEM] {code} qty={bill_qty} state={result.state} "
                f"diag={result.diagnostic} {tele.elapsed_ms:.0f}ms dom={tele.dom_calls}")
        return result

    # ------------------------------------------------------------------
    def _process_item_inner(self, item, code, bill_qty, amount, is_amount_based,
                            tele: ItemTelemetry, result: ItemResult) -> ItemResult:
        if self.session.is_cancelled():
            result.state = TxState.CANCELLED.value
            result.diagnostic = Diagnostic.CANCELLED_PRE_DISPATCH.value
            return result

        tele.stage = "CONTEXT"
        self.session.ensure_context()

        tele.stage = "SPECIALITY_CLEAR"
        self._clear_stale_speciality_if_needed(code)

        tele.stage = "DUPLICATE_CHECK"
        before = self.table.snapshot()
        if before.matching_quantity(code) >= bill_qty:
            self.logger.info(
                f"[DUP-GUARD-ENTRY] {code} already in portal "
                f"({before.matching_quantity(code)}/{bill_qty}) - SKIP, no Plus")
            self.last_code = code
            result.state = TxState.DUPLICATE_PROVEN.value
            result.diagnostic = Diagnostic.DUPLICATE_PROVEN.value
            result.success = True
            return result

        tele.stage = "PROCEDURE"
        self.proc_sel.execute(code)
        self.session.counters.procedure_selections += 1

        tele.stage = "SPECIALITY"
        try:
            self.spec_sync.execute(timeout=3.0)
            self.session.counters.speciality_syncs += 1
        except TimeoutError as exc:
            self.logger.warn(f"[SPECIALITY-RECOVERY] initial sync failed for {code}: {exc}")
            if not self._recover_speciality(code):
                result.state = TxState.FAILED_BEFORE_DISPATCH.value
                result.diagnostic = Diagnostic.SPECIALITY_LOCKED.value
                result.detail = "speciality could not be synchronised - NO PLUS"
                return result

        tele.stage = "QUANTITY_LOCK"
        is_locked = self.qty_ctrl.is_locked()
        self.logger.info(f"[LOCK-DECISION] {code} qty={bill_qty} locked={is_locked}")

        if is_amount_based:
            tele.stage = "AMOUNT"
            name = AMOUNT_PROCEDURE_NAMES.get(code.upper(), code)
            try:
                self.proc_name_ctrl.execute(name)
            except (NoSuchElementException, WebDriverException) as exc:
                self.logger.warn(f"[PROC-NAME] {code}: {exc}")
            self.amount_ctrl.execute(amount if amount is not None else float(bill_qty))
            tx = self._run_plus_transaction(code, 1, bill_qty, expected_portal_qty=1,
                                            before=before, tele=tele)
            return self._finish(result, [tx], code)

        if is_locked:
            return self._process_locked_quantity(code, bill_qty, before, tele, result)

        tele.stage = "QUANTITY"
        if self.qty_ctrl.current_value() != str(bill_qty):
            self.qty_ctrl.execute(bill_qty)
        tx = self._run_plus_transaction(code, 1, bill_qty, expected_portal_qty=bill_qty,
                                        before=before, tele=tele)
        return self._finish(result, [tx], code)

    # ------------------------------------------------------------------
    def _process_locked_quantity(self, code, required_qty, before: RowSnapshot,
                                 tele: ItemTelemetry, result: ItemResult) -> ItemResult:
        """Locked quantity: the portal accepts 1 unit per Plus (proven path)."""
        self.logger.info(f"[TX-LOCKED] {code} qty {required_qty} locked -> {required_qty} units")
        transactions: List[PlusTransaction] = []
        current = before
        already = current.matching_quantity(code)
        if already >= required_qty:
            result.state = TxState.DUPLICATE_PROVEN.value
            result.diagnostic = Diagnostic.DUPLICATE_PROVEN.value
            result.success = True
            self.last_code = code
            return result

        stage_ctx = None
        for unit_idx in range(already + 1, required_qty + 1):
            if self.session.is_cancelled():
                self.logger.warn(f"[CANCELLED] {code} stopping before unit {unit_idx}")
                break
            tele.stage = f"LOCKED_UNIT_{unit_idx}"
            self.session.counters.locked_unit_transactions += 1

            if unit_idx > already + 1:
                # ONE compact probe decides what (if anything) must be re-driven.
                # No fixed sleep, no cooldown, no unconditional re-selection:
                # the portal's OWN reported state is the only input.
                stage_ctx = self.stage_probe.read()
                if not self._restore_stages(stage_ctx, code, unit_idx, result):
                    break
                # the previous unit's commit was already verified, so the row
                # count it produced is known - no second full snapshot here.
            tx = self._run_plus_transaction(code, unit_idx, required_qty,
                                            expected_portal_qty=1, before=current,
                                            tele=tele, stage_ctx=stage_ctx)
            transactions.append(tx)
            if not tx.is_success:
                break
            current = self.table.snapshot()
        return self._finish(result, transactions, code, required_qty=required_qty)

    # ------------------------------------------------------------------
    def _restore_stages(self, ctx, code: str, unit_idx: int,
                        result: ItemResult) -> bool:
        """Re-drive ONLY the stages the portal actually dropped.

        ``ctx.probed`` is False when the compact probe is unavailable; every
        reuse predicate is then False and the full proven flow runs, so a
        portal that cannot be probed loses speed, never safety.
        """
        counters = self.session.counters
        reused = []

        needs_procedure = not ctx.procedure_matches(code)
        needs_speciality = not ctx.speciality_ready()

        # The portal DERIVES the speciality from the selected procedure.  If the
        # speciality was dropped, syncing it alone cannot repopulate it - the
        # procedure has to be re-selected first.  The baseline flow always
        # paired the two, which is why it never hit this; reusing the procedure
        # while the speciality is empty would deadlock the sync.
        if needs_speciality and not needs_procedure:
            self.logger.trace(
                f"[STAGE-REUSE] unit {unit_idx} speciality dropped - "
                f"re-selecting procedure to repopulate it")
            needs_procedure = True

        if needs_procedure:
            self.proc_sel.execute(code)
            counters.procedure_selections += 1
        else:
            reused.append("procedure")

        if not needs_speciality:
            reused.append("speciality")
        else:
            try:
                self.spec_sync.execute(timeout=3.0)
                counters.speciality_syncs += 1
            except TimeoutError:
                if not self._recover_speciality(code):
                    result.detail = f"speciality lock before unit {unit_idx}"
                    return False
                counters.speciality_syncs += 1

        if reused:
            counters.stage_context_reuses += len(reused)
            self.logger.trace(
                f"[STAGE-REUSE] unit {unit_idx} reused {'+'.join(reused)} "
                f"(portal still holds them)")
        return True

    # ------------------------------------------------------------------
    def _finish(self, result: ItemResult, transactions: List[PlusTransaction],
                code: str, required_qty: Optional[int] = None) -> ItemResult:
        self.last_code = code
        result.transactions = transactions
        self.transactions.extend(transactions)
        if not transactions:
            result.state = result.state if result.state != "PENDING" else TxState.FAILED_BEFORE_DISPATCH.value
            return result
        last = transactions[-1]
        result.state = last.state.value
        result.diagnostic = last.diagnostic.value
        result.detail = last.last_error or last.reconciliation_outcome
        all_ok = all(tx.is_success or tx.state is TxState.DUPLICATE_PROVEN for tx in transactions)
        if required_qty is not None:
            committed = sum(1 for tx in transactions if tx.is_success)
            all_ok = all_ok and committed >= (required_qty - transactions[0].before_matching_qty)
        result.success = bool(all_ok)
        result.needs_operator = any(tx.needs_operator for tx in transactions)
        return result

    # ------------------------------------------------------------------
    # THE transaction
    # ------------------------------------------------------------------
    def _run_plus_transaction(self, code: str, unit_idx: int, required_qty: int,
                              expected_portal_qty: int, before: RowSnapshot,
                              tele: ItemTelemetry, stage_ctx=None) -> PlusTransaction:
        tx = PlusTransaction(
            identity=self._identity(code, unit_idx),
            ledger=self.session.ledger,
            requested_quantity=required_qty,
            expected_portal_quantity=expected_portal_qty,
        )
        tx.before_rows = before.count
        tx.before_matching_qty = before.matching_quantity(code)
        started = time.time()

        try:
            self._drive_transaction(tx, code, required_qty, expected_portal_qty, before,
                                    stage_ctx=stage_ctx)
        except (PortalContextLost, WebDriverException) as exc:
            self._fail_on_exception(tx, exc, Diagnostic.BROWSER_DISCONNECTED
                                    if isinstance(exc, WebDriverException)
                                    else Diagnostic.CONTEXT_LOST, code, expected_portal_qty, before)
        except (NoSuchElementException, TimeoutError, ValueError) as exc:
            self._fail_on_exception(tx, exc, Diagnostic.PRECONDITION_FAILED,
                                    code, expected_portal_qty, before)
        finally:
            tx.timings["total_ms"] = round((time.time() - started) * 1000.0, 3)
            self.watch.disconnect()
            self.session.journal.write(tx)
        return tx

    def _fail_on_exception(self, tx: PlusTransaction, exc: Exception,
                           diagnostic: Diagnostic, code: str,
                           expected_portal_qty: int, before: RowSnapshot):
        """An exception is never success - and never a silent retry either."""
        message = f"{type(exc).__name__}: {exc}"
        if tx.state in (TxState.PREPARED,):
            tx.transition(TxState.FAILED_BEFORE_DISPATCH, message, diagnostic)
            tx.dispatch_proof = DispatchProof.PROVEN_NOT_DISPATCHED
            tx.last_error = message
            return
        if tx.state in (TxState.DISPATCH_INTENT,):
            tx.abort_before_click(message, diagnostic)
            return
        if tx.state in (TxState.DISPATCHED, TxState.WAITING_FOR_COMMIT):
            # A mutation may exist - reconcile, never assume.
            outcome = self.verifier.reconcile(tx, code, expected_portal_qty, before,
                                              grace=self.reconcile_grace)
            if outcome.committed:
                tx.mark_committed(outcome.row or {}, Diagnostic.COMMIT_VERIFIED_LATE)
            else:
                tx.mark_reconciliation_required(
                    f"{message} | {outcome.detail}", diagnostic)

    # ------------------------------------------------------------------
    def _drive_transaction(self, tx: PlusTransaction, code: str, required_qty: int,
                           expected_portal_qty: int, before: RowSnapshot,
                           stage_ctx=None):
        tx_id = tx.identity.transaction_id
        self.logger.info(f"[TX-START] {tx_id} expect_qty={expected_portal_qty} "
                         f"rows_before={before.count}")

        if self.session.is_cancelled():
            tx.transition(TxState.CANCELLED, "cancelled before preparation",
                          Diagnostic.CANCELLED_PRE_DISPATCH)
            return

        # ---- reason (mandatory when the control exists) -------------------
        # NOTE on ordering: NoSuchElementException/StaleElementReferenceException
        # are WebDriverException subclasses.  They are caught FIRST so that a
        # missing control is never misreported as a dead browser.
        if stage_ctx is not None and stage_ctx.reason_ready():
            # The probe proved the portal still holds a valid reason - selecting
            # it again would be a DOM round trip that changes nothing.
            self.session.counters.stage_context_reuses += 1
            self.logger.trace(f"[STAGE-REUSE] {tx_id} reason already satisfied")
            reason_outcome = None
        else:
            reason_outcome = _REASON_MUST_RUN
        try:
            if reason_outcome is _REASON_MUST_RUN:
                self.reason_ctrl.execute()
                self.session.counters.reason_selections += 1
        except (NoSuchElementException, StaleElementReferenceException, ValueError) as exc:
            # A stale speciality can suppress the reason option list, so ONE
            # recovery attempt is allowed - but the operator-visible cause of
            # the block stays the reason control, never a guessed one.
            self.logger.warn(f"[REASON] {tx_id} blocked: {exc}")
            recovered = self._recover_speciality(code)
            if recovered:
                try:
                    self.reason_ctrl.execute()
                except (NoSuchElementException, StaleElementReferenceException,
                        ValueError) as exc2:
                    exc, recovered = exc2, False
            if not recovered:
                tx.transition(TxState.FAILED_BEFORE_DISPATCH,
                              f"mandatory enhancement reason unavailable: {exc}",
                              Diagnostic.REASON_MISSING)
                tx.dispatch_proof = DispatchProof.PROVEN_NOT_DISPATCHED
                return
        except WebDriverException as exc:
            diagnostic = (Diagnostic.BROWSER_DISCONNECTED if is_browser_disconnect(exc)
                          else Diagnostic.PRECONDITION_FAILED)
            tx.transition(TxState.FAILED_BEFORE_DISPATCH,
                          f"reason step failed before any mutation: {exc}", diagnostic)
            tx.dispatch_proof = DispatchProof.PROVEN_NOT_DISPATCHED
            return

        # ---- last duplicate gate immediately before the mutation ----------
        pre = self.table.snapshot()
        if pre.matching_quantity(code) >= required_qty:
            self.logger.info(f"[DUP-GUARD-AT-PLUS] {code} portal qty "
                             f"{pre.matching_quantity(code)} >= {required_qty} - SKIP PLUS")
            tx.mark_duplicate("portal already holds the required quantity")
            return
        before = pre
        tx.before_rows = before.count
        tx.before_matching_qty = before.matching_quantity(code)

        self.watch.install()

        # ---- dispatch ------------------------------------------------------
        dispatched = self.plus_ctrl.dispatch(tx, code, before)
        if not dispatched and tx.state is TxState.FAILED_BEFORE_DISPATCH:
            if tx.allow_retry():
                self.logger.info(f"[RETRY] {tx_id} authorised by dispatch-absence proof")
                dispatched = self.plus_ctrl.dispatch(tx, code, before)
        if not dispatched:
            if tx.state is TxState.RECONCILIATION_REQUIRED:
                outcome = self.verifier.reconcile(tx, code, expected_portal_qty, before,
                                                  grace=self.reconcile_grace)
                if outcome.committed:
                    tx.mark_committed(outcome.row or {}, Diagnostic.COMMIT_VERIFIED_LATE)
                else:
                    tx.reconciliation_outcome = outcome.detail
            self.logger.error(f"[TX-NO-DISPATCH] {tx_id} state={tx.state.value} "
                              f"diag={tx.diagnostic.value}")
            return

        # ---- wait for commit ----------------------------------------------
        tx.begin_wait()
        outcome = self.verifier.wait_for_commit(code, expected_portal_qty, before,
                                                timeout=self.commit_timeout)
        if outcome.committed:
            tx.mark_committed(outcome.row or {}, outcome.diagnostic)
            self.logger.info(f"[PLUS-CONFIRMED] {tx_id} row={outcome.row}")
            return

        # ---- RECONCILE: the fix for TX-UNKNOWN-ASSUMED-COMMITTED ----------
        reconciled = self.verifier.reconcile(tx, code, expected_portal_qty, before,
                                             grace=self.reconcile_grace)
        if reconciled.committed:
            tx.mark_committed(reconciled.row or {}, Diagnostic.COMMIT_VERIFIED_LATE)
            self.logger.info(f"[TX-RECONCILED-LATE] {tx_id} committed on re-read")
            return

        tx.mark_reconciliation_required(
            reconciled.detail or outcome.detail or "portal result could not be verified",
            reconciled.diagnostic)
        self.logger.error(
            f"[RECONCILIATION_REQUIRED] {tx_id} {tx.diagnostic.value} - "
            f"NOT counted as success, NO second Plus. {tx.reconciliation_outcome}")

    # ------------------------------------------------------------------
    # speciality helpers
    # ------------------------------------------------------------------
    def _clear_stale_speciality_if_needed(self, next_code: str):
        if self.last_code is None or self.last_code == next_code:
            return
        self.spec_clear.execute(next_code)

    def _recover_speciality(self, code: str) -> bool:
        self.logger.info(f"[SPECIALITY-RECOVERY] clearing stale speciality for {code}")
        try:
            self.spec_clear.execute(code)
            try:
                self.session.sync.wait_until(
                    lambda: is_placeholder(self.spec_sync.current_value()),
                    timeout=2.0, desc="speciality cleared")
            except TimeoutError:
                current = self.spec_sync.current_value()
                if not is_placeholder(current):
                    self.logger.error(
                        f"[SPECIALITY-X-CLEAR-FAIL] still {current!r} - STOP BEFORE PLUS")
                    return False
            self.spec_sync.execute(timeout=4.0)
            return True
        except (TimeoutError, NoSuchElementException, StaleElementReferenceException,
                WebDriverException) as exc:
            if isinstance(exc, WebDriverException) and is_browser_disconnect(exc):
                raise                      # a dead browser is not a speciality lock
            self.logger.error(f"[SPECIALITY-RECOVERY] failed: {exc}")
            return False


# ---------------------------------------------------------------------------
# Batch execution (6.1 / 6.9)
# ---------------------------------------------------------------------------

@dataclass
class BatchResult:
    summary: BatchPerformanceSummary
    patients: List[Dict[str, Any]] = field(default_factory=list)
    stopped_reason: str = ""

    @property
    def needs_operator(self) -> bool:
        return self.summary.items_reconciliation_required > 0


class BatchRunner:
    """One session, one orchestrator, N patients, strictly sequential."""

    def __init__(self, session: PortalSession, logger: Optional[EnterpriseLogger] = None,
                 **orchestrator_kwargs):
        self.session = session
        self.logger = logger or session.logger
        self.orchestrator = TreatmentPlanOrchestrator(session, self.logger, **orchestrator_kwargs)

    @staticmethod
    def dedupe(items: List[Dict[str, Any]], logger: EnterpriseLogger) -> List[Dict[str, Any]]:
        seen = set()
        out = []
        for item in items:
            code = (item.get("code") or "").upper()
            if not code or code == "MANUAL_REQUIRED":
                continue
            if code in seen:
                logger.warn(f"[DEDUP] duplicate {code} in queue - SKIP")
                continue
            seen.add(code)
            out.append(item)
        return out

    def run(self, batch_queue: List[Dict[str, Any]],
            on_patient_status: Optional[Callable[[int, str], None]] = None) -> BatchResult:
        started = time.time()
        patients: List[Dict[str, Any]] = []
        stopped_reason = ""

        for index, patient in enumerate(batch_queue):
            if self.session.is_cancelled():
                stopped_reason = "cancelled by operator"
                break
            name = patient.get("name", f"patient_{index}")
            self.session.set_bill(f"{name}_{index}", expected_patient=name)
            self.orchestrator.last_code = None
            items = self.dedupe(patient.get("items", []), self.logger)
            self.logger.info("=" * 78)
            self.logger.info(f"[PATIENT {index + 1}/{len(batch_queue)}] {name} ({len(items)} codes)")
            if on_patient_status:
                on_patient_status(index, "IN_PROGRESS")

            completed, failed, review = 0, [], []
            try:
                self.session.ensure_context()
            except PatientNotSwitched as exc:
                self.logger.error(f"[STOP] {exc}")
                stopped_reason = str(exc)
                if on_patient_status:
                    on_patient_status(index, "STOPPED_WRONG_PATIENT")
                patients.append({
                    "name": name, "status": "STOPPED_WRONG_PATIENT", "completed": 0,
                    "failed": [], "reconciliation_required": [],
                    "items_total": len(items),
                })
                break
            except (AmbiguousPatientTab, NoPatientTab) as exc:
                raise
            for item in items:
                if self.session.is_cancelled():
                    stopped_reason = "cancelled by operator"
                    break
                result = self.orchestrator.process_item(item)
                if result.needs_operator:
                    review.append(result.code)
                elif result.success:
                    completed += 1
                else:
                    failed.append(result.code)

            status = "COMPLETED"
            if review:
                status = "RECONCILIATION_REQUIRED"
            elif failed:
                status = "PARTIAL"
            if on_patient_status:
                on_patient_status(index, status)
            patients.append({
                "name": name, "status": status, "completed": completed,
                "failed": failed, "reconciliation_required": review,
                "items_total": len(items),
            })
            self.logger.info(
                f"[PATIENT DONE] {name} status={status} completed={completed} "
                f"failed={failed} review={review}")
            if stopped_reason:
                break

        summary = BatchPerformanceSummary.build(
            self.session.counters, self.orchestrator.telemetry,
            batch_elapsed_ms=(time.time() - started) * 1000.0,
            driver_attaches=self.session.driver_attaches,
            treatment_plan_discoveries=self.session.treatment_plan_discoveries,
        )
        return BatchResult(summary=summary, patients=patients, stopped_reason=stopped_reason)


# ---------------------------------------------------------------------------
# Compatibility / test-only fallback (6.1)
# ---------------------------------------------------------------------------

def execute_item(item: Dict[str, Any], logger: Optional[EnterpriseLogger] = None,
                 debugger_address: str = "127.0.0.1:9222") -> ItemResult:
    """Single-item, attach-per-call execution.

    EXPLICIT COMPATIBILITY / TEST FALLBACK ONLY.  Production batches must use
    :class:`BatchRunner`, which attaches once and reuses the verified session;
    calling this per item re-attaches the driver and re-discovers the Treatment
    Plan, which is exactly the latency this task removed.
    """
    log = logger or EnterpriseLogger()
    log.warn("[COMPAT] execute_item() attaches a fresh driver - not the production path")
    session = PortalSession.attach(log, debugger_address=debugger_address)
    try:
        orchestrator = TreatmentPlanOrchestrator(session, log)
        session.set_bill("compat_single_item")
        return orchestrator.process_item(item)
    finally:
        session.close()
