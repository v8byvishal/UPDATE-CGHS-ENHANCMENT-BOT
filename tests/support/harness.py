"""Shared test helpers: session wiring, sleep metering, item builders."""

from __future__ import annotations

import contextlib
import time
from typing import Any, Dict, List, Optional

from cghs.orchestrator import BatchRunner, TreatmentPlanOrchestrator
from cghs.session import PortalSession
from cghs.telemetry import EnterpriseLogger
from cghs.txstate import TransactionJournal

from .fake_portal import FakePortal


class CollectingSignal:
    def __init__(self):
        self.records: List[str] = []

    def emit(self, message: str):
        self.records.append(message)


def make_logger(trace: bool = False) -> EnterpriseLogger:
    """A logger that never prints - keeps the test output readable."""
    return EnterpriseLogger(CollectingSignal(), trace_enabled=trace)


def make_session(portal: FakePortal, *, trace: bool = False,
                 journal: Optional[TransactionJournal] = None,
                 cancel_check=None) -> PortalSession:
    session = PortalSession(portal, make_logger(trace),
                            journal=journal or TransactionJournal(enabled=False),
                            cancel_check=cancel_check, trace=trace)
    return session


def make_orchestrator(portal: FakePortal, *, commit_timeout: float = 1.2,
                      reconcile_grace: float = 0.4, **kwargs):
    session = make_session(portal, **kwargs)
    orchestrator = TreatmentPlanOrchestrator(session, session.logger,
                                             commit_timeout=commit_timeout,
                                             reconcile_grace=reconcile_grace)
    session.set_bill("TESTBILL")
    return session, orchestrator


def make_runner(portal: FakePortal, *, commit_timeout: float = 1.2,
                reconcile_grace: float = 0.4, **kwargs) -> BatchRunner:
    session = make_session(portal, **kwargs)
    return BatchRunner(session, session.logger, commit_timeout=commit_timeout,
                       reconcile_grace=reconcile_grace)


def item(code: str, qty: int = 1, amount: Optional[float] = None) -> Dict[str, Any]:
    data: Dict[str, Any] = {"code": code, "qty": qty}
    if amount is not None:
        data["amount"] = amount
    return data


def patient(name: str, items: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"name": name, "items": items}


class SleepMeter:
    """Measures every ``time.sleep`` performed by the code under test.

    Used for the polling-budget evidence - it works identically for the legacy
    module and the new package because both ultimately call ``time.sleep``.
    """

    def __init__(self):
        self.total_ms = 0.0
        self.calls: List[float] = []

    @property
    def count(self) -> int:
        return len(self.calls)


@contextlib.contextmanager
def measure_sleep():
    meter = SleepMeter()
    original = time.sleep

    def counting_sleep(seconds):
        meter.total_ms += float(seconds) * 1000.0
        meter.calls.append(float(seconds))
        original(seconds)

    time.sleep = counting_sleep
    try:
        yield meter
    finally:
        time.sleep = original
