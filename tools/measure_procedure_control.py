"""Section 18 measurement: cost of the post-Plus procedure-control fix.

Run with the fix in place, and again with `git stash push -- cghs/` to get
the before column.  Writes nothing; print the JSON and paste it into
docs/perf/post_plus_procedure_control.json.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.support.fake_portal import build_portal           # noqa: E402
from tests.support.harness import make_orchestrator, item    # noqa: E402
from tests.support.react_select_dom import (  # noqa: E402
    Option,
    ReactSelectConfig,
    ReactSelectDOM,
)

CODES = ["CN002", "C001", "LB012", "RI001", "BL004", "CC001", "WC001",
         "LB001", "LB002", "LB003", "RI002", "RI003", "BL001", "BL002",
         "BL003", "CN001", "CN003", "C002", "C003", "CC002", "WC002",
         "LB004", "RI004", "BL005", "CN004", "C004", "CC003"]


def _counters(dom):
    c = dict(getattr(dom, "counters", {}))
    c["dom_calls"] = (c.get("find_elements", 0) + c.get("element_reads", 0)
                      + c.get("execute_script", 0))
    return c


def _run(label, build, drive):
    dom = build()
    session, orch = make_orchestrator(dom)
    session.set_bill("PERF")
    t0 = time.perf_counter()
    result = drive(session, orch, dom)
    elapsed = time.perf_counter() - t0
    c = _counters(dom)
    res = getattr(session.resolver, "_strategy_cache", {})
    return {
        "scenario": label,
        "find_elements": c.get("find_elements", 0),
        "element_reads": c.get("element_reads", 0),
        "execute_script": c.get("execute_script", 0),
        "dom_calls": c["dom_calls"],
        "frame_discoveries": c.get("frame_discoveries", getattr(dom, "frame_discoveries", 0)),
        "tab_scans": c.get("tab_scans", getattr(dom, "tab_scans", 0)),
        "fixed_sleep_ms": 0.0,
        "plus_clicks": getattr(dom, "plus_clicks", None),
        "rows": len(getattr(dom, "rows", [])),
        "locator_cache_entries": len(res),
        "elapsed_ms": round(elapsed * 1000, 1),
        "outcome": result,
    }


def happy_path(session, orch, dom):
    ok = 0
    for code in CODES:
        r = orch.process_item(item(code, 1))
        ok += 1 if getattr(r, "success", False) else 0
    return f"{ok}/{len(CODES)} committed"


def _locked_portal(remount=False):
    catalogue = [Option("GP001", "GP001 - procedure GP001", "Consultation")]
    kw = dict(resets_after_add=("procedure",))
    if remount:
        kw.update(procedure_remount_after_units=19,
                  procedure_remount_new_container=True)
    return ReactSelectDOM(config=ReactSelectConfig(**kw), catalogue=catalogue)


def locked_qty30(session, orch, dom):
    r = orch.process_item(item("GP001", 30))
    return (f"success={getattr(r, 'success', False)} "
            f"plus={dom.plus_clicks} rows={len(dom.rows)}")


def _react_portal():
    """The section 4 reproduction: React re-renders procedure after the Plus,
    retaining the spent container and mounting the replacement after it."""
    catalogue = [Option(c, f"{c} - procedure {c}", "Consultation")
                 for c in ("CN002", "C001")]
    config = ReactSelectConfig(resets_after_add=("procedure",),
                               procedure_remount_after_units=6,
                               procedure_remount_new_container=True)
    return ReactSelectDOM(config=config, catalogue=catalogue)


def rerender(session, orch, dom):
    a = orch.process_item(item("CN002", 6))
    b = orch.process_item(item("C001", 2))
    return (f"CN002 success={getattr(a, 'success', False)} "
            f"C001 success={getattr(b, 'success', False)} "
            f"plus={dom.plus_clicks} rows={len(dom.rows)}")


def main():
    rows = [
        _run("happy_path_27_codes", lambda: build_portal(CODES), happy_path),
        _run("gp001_locked_qty30", lambda: _locked_portal(), locked_qty30),
        _run("gp001_locked_qty30_remount_at_19",
             lambda: _locked_portal(remount=True), locked_qty30),
        _run("cn002_then_c001_rerender", _react_portal, rerender),
    ]
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
