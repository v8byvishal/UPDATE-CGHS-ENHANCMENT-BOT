"""Logging, performance counters and per-item telemetry.

Canonical owner of ``EnterpriseLogger`` (moved out of ``app.py``) plus the new
performance instrumentation required by the hardening task.

Design rules
------------
* Detailed diagnostics are emitted at STATE TRANSITIONS, never from a polling
  loop.  Low-level polling output is only produced when ``trace`` is enabled.
* Every sleep in the hot path goes through :meth:`PerfCounters.sleep` so that a
  test can assert "no unconditional fixed sleeps remain".
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

class EnterpriseLogger:
    """Sink-agnostic logger.

    ``log_signal`` is anything exposing ``.emit(str)`` - a ``pyqtSignal`` in the
    desktop app, a list-appender in tests.  Keeping it duck-typed is what allows
    the automation core to be imported without PyQt5.
    """

    def __init__(self, log_signal: Optional[Any] = None, trace_enabled: bool = False):
        self.log_signal = log_signal
        self.trace_enabled = trace_enabled
        self.records: List[str] = []

    def _emit(self, level: str, msg: str):
        formatted = f"[{time.strftime('%H:%M:%S', time.localtime())}] [{level}] {msg}"
        self.records.append(formatted)
        if self.log_signal is None:
            print(formatted)
        else:
            try:
                self.log_signal.emit(formatted)
            except Exception:
                print(formatted)

    def info(self, msg: str):
        self._emit("INFO", msg)

    def warn(self, msg: str):
        self._emit("WARN", f"\u26a0\ufe0f {msg}")

    def error(self, msg: str):
        self._emit("ERROR", f"\u274c {msg}")

    def trace(self, msg: str):
        """Low-level poll/probe diagnostics - suppressed unless trace mode is on."""
        if self.trace_enabled:
            self._emit("TRACE", msg)


# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------

@dataclass
class PerfCounters:
    """Session-scoped counters.  Cheap integer arithmetic only."""

    execute_script_calls: int = 0
    find_elements_calls: int = 0
    element_reads: int = 0
    full_tab_scans: int = 0
    frame_discoveries: int = 0
    locator_cache_hits: int = 0
    locator_cache_misses: int = 0
    frame_cache_hits: int = 0
    frame_cache_misses: int = 0
    tab_cache_hits: int = 0
    tab_cache_misses: int = 0
    probe_calls: int = 0
    probe_fallbacks: int = 0
    fixed_sleep_ms: float = 0.0
    adaptive_sleep_ms: float = 0.0
    explicit_wait_ms: float = 0.0
    retries: int = 0
    # --- locked-quantity stage reuse (task 3/5/6) ---------------------
    #: how many times each hot-path stage was actually DRIVEN.  For a locked
    #: quantity of N these must stay far below N once the portal is proven to
    #: keep the stage selected between units.
    procedure_selections: int = 0
    speciality_syncs: int = 0
    reason_selections: int = 0
    #: a compact probe proved the cached stage was still valid -> work skipped
    stage_context_reuses: int = 0
    locked_unit_transactions: int = 0
    plus_dispatches: int = 0
    duplicate_plus_attempts_blocked: int = 0
    reconciliations: int = 0
    stale_recoveries: int = 0
    procedure_reacquisitions: int = 0
    shared_control_failures: int = 0

    # ---- derived -----------------------------------------------------
    @property
    def dom_calls(self) -> int:
        return self.execute_script_calls + self.find_elements_calls + self.element_reads

    # ---- instrumentation helpers -------------------------------------
    def sleep(self, seconds: float, adaptive: bool = True) -> None:
        """The ONLY sanctioned sleep in the hot path.

        ``adaptive=False`` records an unconditional fixed sleep, which the
        polling-budget test asserts must stay at zero.
        """
        if seconds <= 0:
            return
        if adaptive:
            self.adaptive_sleep_ms += seconds * 1000.0
        else:
            self.fixed_sleep_ms += seconds * 1000.0
        time.sleep(seconds)

    def snapshot(self) -> Dict[str, Any]:
        data = {k: v for k, v in self.__dict__.items()}
        data["dom_calls"] = self.dom_calls
        return data

    def delta(self, other: "PerfCounters") -> Dict[str, Any]:
        out = {}
        for k, v in self.__dict__.items():
            ov = getattr(other, k, 0)
            if isinstance(v, (int, float)):
                out[k] = round(v - ov, 3)
        out["dom_calls"] = self.dom_calls - other.dom_calls
        return out

    def copy(self) -> "PerfCounters":
        return PerfCounters(**{k: v for k, v in self.__dict__.items()})


# ---------------------------------------------------------------------------
# Adaptive polling
# ---------------------------------------------------------------------------

class AdaptivePoller:
    """Fast when the portal reacts quickly, backs off modestly when idle.

    Explicitly NOT a constant ``sleep(0.08)``: the first few polls are nearly
    free, which is what removes latency from the common fast path.
    """

    __slots__ = ("counters", "initial", "factor", "maximum", "_current")

    def __init__(self, counters: PerfCounters, initial: float = 0.01,
                 factor: float = 1.6, maximum: float = 0.12):
        self.counters = counters
        self.initial = initial
        self.factor = factor
        self.maximum = maximum
        self._current = initial

    def reset(self):
        self._current = self.initial

    def wait(self):
        self.counters.sleep(self._current, adaptive=True)
        self._current = min(self._current * self.factor, self.maximum)


# ---------------------------------------------------------------------------
# Per-item telemetry
# ---------------------------------------------------------------------------

@dataclass
class ItemTelemetry:
    run_id: str = ""
    bill_id: str = ""
    session_id: str = ""
    final_code: str = ""
    quantity: int = 0
    state: str = ""
    stage: str = ""
    elapsed_ms: float = 0.0
    dom_calls: int = 0
    execute_script_calls: int = 0
    find_elements_calls: int = 0
    locator_cache_hit: int = 0
    locator_cache_miss: int = 0
    frame_cache_hit: int = 0
    frame_cache_miss: int = 0
    tab_cache_hit: int = 0
    tab_cache_miss: int = 0
    verification_outcome: str = ""
    reconciliation_outcome: str = ""
    stages: Dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    k = (len(ordered) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    frac = k - lo
    return round(ordered[lo] + (ordered[hi] - ordered[lo]) * frac, 3)


@dataclass
class BatchPerformanceSummary:
    """Concise, machine-readable batch summary (section 10 of the contract)."""

    batch_elapsed_ms: float = 0.0
    items_total: int = 0
    items_completed: int = 0
    items_failed: int = 0
    items_reconciliation_required: int = 0
    items_skipped_duplicate: int = 0
    avg_item_ms: float = 0.0
    p50_item_ms: float = 0.0
    p95_item_ms: float = 0.0
    total_dom_calls: int = 0
    total_execute_script_calls: int = 0
    total_find_elements_calls: int = 0
    full_tab_scans: int = 0
    frame_discoveries: int = 0
    fixed_sleep_ms: float = 0.0
    adaptive_sleep_ms: float = 0.0
    retries: int = 0
    procedure_selections: int = 0
    speciality_syncs: int = 0
    reason_selections: int = 0
    stage_context_reuses: int = 0
    locked_unit_transactions: int = 0
    duplicate_plus_attempts_blocked: int = 0
    plus_dispatches: int = 0
    locator_cache_hits: int = 0
    locator_cache_misses: int = 0
    frame_cache_hits: int = 0
    frame_cache_misses: int = 0
    driver_attaches: int = 0
    python_processes: int = 1
    treatment_plan_discoveries: int = 0
    items: List[Dict[str, Any]] = field(default_factory=list)

    @classmethod
    def build(cls, counters: PerfCounters, telemetry: List[ItemTelemetry],
              batch_elapsed_ms: float, driver_attaches: int = 1,
              treatment_plan_discoveries: int = 1) -> "BatchPerformanceSummary":
        durations = [t.elapsed_ms for t in telemetry]
        completed = sum(1 for t in telemetry if t.state == "COMPLETED")
        recon = sum(1 for t in telemetry if t.state == "RECONCILIATION_REQUIRED")
        dup = sum(1 for t in telemetry if t.state == "DUPLICATE_PROVEN")
        failed = len(telemetry) - completed - recon - dup
        return cls(
            batch_elapsed_ms=round(batch_elapsed_ms, 3),
            items_total=len(telemetry),
            items_completed=completed,
            items_failed=failed,
            items_reconciliation_required=recon,
            items_skipped_duplicate=dup,
            avg_item_ms=round(sum(durations) / len(durations), 3) if durations else 0.0,
            p50_item_ms=_percentile(durations, 50),
            p95_item_ms=_percentile(durations, 95),
            total_dom_calls=counters.dom_calls,
            total_execute_script_calls=counters.execute_script_calls,
            total_find_elements_calls=counters.find_elements_calls,
            full_tab_scans=counters.full_tab_scans,
            frame_discoveries=counters.frame_discoveries,
            fixed_sleep_ms=round(counters.fixed_sleep_ms, 3),
            adaptive_sleep_ms=round(counters.adaptive_sleep_ms, 3),
            retries=counters.retries,
            procedure_selections=counters.procedure_selections,
            speciality_syncs=counters.speciality_syncs,
            reason_selections=counters.reason_selections,
            stage_context_reuses=counters.stage_context_reuses,
            locked_unit_transactions=counters.locked_unit_transactions,
            duplicate_plus_attempts_blocked=counters.duplicate_plus_attempts_blocked,
            plus_dispatches=counters.plus_dispatches,
            locator_cache_hits=counters.locator_cache_hits,
            locator_cache_misses=counters.locator_cache_misses,
            frame_cache_hits=counters.frame_cache_hits,
            frame_cache_misses=counters.frame_cache_misses,
            driver_attaches=driver_attaches,
            treatment_plan_discoveries=treatment_plan_discoveries,
            items=[t.as_dict() for t in telemetry],
        )

    def as_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)
