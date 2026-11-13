"""Shutdown sequencing for the CGHS Automation Console.

PRESENTATION / LIFECYCLE SUPPORT ONLY.  No portal logic, no Selenium, no
PyQt import - so the decision can be executed and tested directly instead of
being inferred from the GUI source.

WHY THIS MODULE EXISTS
``closeEvent()`` previously logged a warning when the status-probe thread
refused to stop, then dropped the reference and accepted the close anyway.
That destroys a running ``QThread``: Qt aborts the process when a QThread
object is deleted while its ``run()`` is still executing, and on the worker
side it can tear down a thread in the middle of a Selenium call.

The rule implemented here is simple and absolute: **the window may only
close once every thread it owns has actually finished.**  If any owned
thread is still alive, the close is refused and all references are
preserved so a later attempt can finish the job.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ShutdownDecision:
    """What the window should do about a close request."""

    #: True only when every owned thread has finished.
    accept: bool
    #: Clear the probe reference?  Never true while its thread is alive.
    clear_probe: bool
    #: Which stage blocked, or ``ALL_STOPPED``.
    stage: str
    #: Operator-facing explanation.
    reason: str
    #: Keep refusing to start new work while a close is pending.
    keep_shutdown_latch: bool = True


def plan_shutdown(worker, probe, wait_ms, log=None) -> ShutdownDecision:
    """Stop the threads this window owns, in a safe order.

    ``worker`` is the batch automation thread, ``probe`` the Chrome status
    monitor; either may be ``None``.  Both are expected to expose
    ``stop()``/``wait(ms)``, and the worker additionally ``isRunning()``.

    Order matters.  The enhancement worker is settled FIRST, because it is
    the only thread that can be mid-transaction; the status probe is a
    passive poller and is stopped afterwards.  Nothing is force-terminated:
    a half-finished portal mutation is far worse than a slow exit.
    """
    def _say(message):
        if log is not None:
            try:
                log(message)
            except Exception:                               # noqa: BLE001
                pass

    # ---- 1. the enhancement worker -------------------------------------
    if worker is not None and _is_running(worker):
        _say("[SHUTDOWN] Waiting for the current action to finish safely")
        _call(worker, "stop")
        if not _wait(worker, wait_ms):
            return ShutdownDecision(
                accept=False,
                clear_probe=False,
                stage="WORKER_BUSY",
                reason=("The automation is still finishing an action. "
                        "Nothing has been killed - close again in a moment "
                        "to complete shutdown."),
            )

    # ---- 2. the Chrome status probe ------------------------------------
    if probe is not None:
        _call(probe, "stop")
        if not _wait(probe, wait_ms):
            # The reported defect.  Accepting here would destroy a live
            # QThread, so refuse and KEEP the reference: dropping it would
            # leak the thread and lose the only handle able to join it.
            return ShutdownDecision(
                accept=False,
                clear_probe=False,
                stage="PROBE_BUSY",
                reason=("The Chrome monitor has not stopped yet. The window "
                        "will stay open until it does - close again in a "
                        "moment."),
            )

    return ShutdownDecision(
        accept=True,
        clear_probe=True,
        stage="ALL_STOPPED",
        reason="All background threads finished; closing.",
    )


# ---------------------------------------------------------------------------
# Defensive helpers: shutdown must never raise.
# ---------------------------------------------------------------------------
def _is_running(thread) -> bool:
    checker = getattr(thread, "isRunning", None)
    if checker is None:
        return True
    try:
        return bool(checker())
    except Exception:                                       # noqa: BLE001
        return True


def _call(thread, name) -> None:
    method = getattr(thread, name, None)
    if method is None:
        return
    try:
        method()
    except Exception:                                       # noqa: BLE001
        pass


def _wait(thread, wait_ms) -> bool:
    waiter = getattr(thread, "wait", None)
    if waiter is None:
        return True
    try:
        return bool(waiter(wait_ms))
    except Exception:                                       # noqa: BLE001
        return False
