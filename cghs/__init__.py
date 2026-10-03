"""CGHS portal automation core.

Canonical, importable implementation of the portal automation that previously
lived inside the monolithic ``app.py``.  Importing this package does NOT require
PyQt5, PyMuPDF or a running browser, which is what makes the automation
testable.

Layers (import order is also the dependency order):

    locators    static locator + CGHS code registry (built once)
    telemetry   logging, perf counters, adaptive polling, batch summary
    rules       pure CGHS bill text rules
    parsing     PDF -> EnhancementPlan items (lazy PyMuPDF)
    dom         compact probes, cached locator resolution, targeted waits
    txstate     Plus dispatch state machine + ledger + journal
    tabs        patient tab discovery with evidence + ambiguity stop
    session     one attach, one verified cached context
    controllers one portal control each
    orchestrator deterministic item sequence, exactly-one-Plus per transaction
"""

__version__ = "3.0.0"

__all__ = [
    "locators", "telemetry", "rules", "parsing", "dom",
    "txstate", "tabs", "session", "controllers", "orchestrator",
]
