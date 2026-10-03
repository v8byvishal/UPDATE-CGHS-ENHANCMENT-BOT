#!/usr/bin/env python3
"""Measured before/after comparison: baseline app.py vs the hardened cghs core.

Both sides drive the SAME deterministic portal double (tests/support/fake_portal)
with the SAME item plan, so the numbers are a like-for-like comparison of the
automation layer rather than of a browser.

What is measured
----------------
* wall clock per item and per batch,
* DOM round trips (execute_script + find_elements + element property reads),
* how much of the elapsed time is spent sleeping,
* full tab scans and frame discoveries,
* physical Plus clicks (a correctness check that rides along with the perf run).

What is NOT measured
--------------------
Anything about a real Chrome or the live CGHS/NHA portal.  This sandbox has no
browser, so those numbers stay NOT_YET_VERIFIED.  The portal double models the
portal's *protocol* (async commit, async speciality, dropdown latency), not its
network timings; the honest claim from this harness is the reduction in DOM
round trips and in fixed waiting, which is what dominates the live run.

Usage::

    python3 tools/benchmark_hotpath.py --items 27 --profile fast --repeat 3
    python3 tools/benchmark_hotpath.py --all --out docs/perf
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import pathlib
import statistics
import sys
import time
from typing import Any, Dict, List

REPO = pathlib.Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tests.support.fake_portal import Latency, build_portal          # noqa: E402
from tests.support.harness import (item, make_orchestrator, make_runner,  # noqa: E402
                                   patient)
from tests.support.legacy_loader import BASELINE_COMMIT, load_baseline  # noqa: E402

PROFILES = {"fast": Latency.fast, "medium": Latency.medium, "slow": Latency.slow}


def plan_codes(count: int) -> List[str]:
    return ["BM%04d" % i for i in range(count)]


# ---------------------------------------------------------------------------
# hardened side
# ---------------------------------------------------------------------------

def run_hardened(count: int, profile: str) -> Dict[str, Any]:
    codes = plan_codes(count)
    portal = build_portal(codes, latency=PROFILES[profile]())
    runner = make_runner(portal, commit_timeout=2.0, reconcile_grace=0.5)

    started = time.perf_counter()
    result = runner.run([patient("BENCH", [item(c, 1) for c in codes])])
    elapsed = (time.perf_counter() - started) * 1000.0

    counters = runner.session.counters
    per_item = [t.elapsed_ms for t in runner.orchestrator.telemetry]
    return {
        "implementation": "hardened",
        "items": count,
        "profile": profile,
        "elapsed_ms": round(elapsed, 1),
        "per_item_ms": round(elapsed / count, 2),
        "p50_item_ms": round(statistics.median(per_item), 2) if per_item else 0.0,
        "dom_calls": portal.dom_calls(),
        "dom_calls_per_item": round(portal.dom_calls() / float(count), 2),
        "execute_script": portal.counters["execute_script"],
        "find_elements": portal.counters["find_elements"],
        "element_reads": portal.counters["element_reads"],
        "probe_calls": portal.counters["probe_calls"],
        "window_handles_scans": portal.counters["window_handles"],
        "frame_discoveries": counters.frame_discoveries,
        "fixed_sleep_ms": round(counters.fixed_sleep_ms, 1),
        "adaptive_sleep_ms": round(counters.adaptive_sleep_ms, 1),
        "plus_clicks": sum(portal.plus_clicks_by_code.values()),
        "completed": result.summary.items_completed,
        "reconciliation_required": result.summary.items_reconciliation_required,
    }


# ---------------------------------------------------------------------------
# baseline side
# ---------------------------------------------------------------------------

def run_baseline(count: int, profile: str) -> Dict[str, Any]:
    """Drive the ORIGINAL orchestrator from the baseline commit."""
    codes = plan_codes(count)
    portal = build_portal(codes, latency=PROFILES[profile]())

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        legacy = load_baseline()
        logger = legacy.EnterpriseLogger(None)
        orchestrator = legacy.TreatmentPlanOrchestrator(portal, logger)
        orchestrator.set_current_bill("BENCH_0")

        started = time.perf_counter()
        completed = 0
        for code in codes:
            try:
                ok = orchestrator.process_item({"code": code, "qty": 1})
            except Exception:                              # noqa: BLE001
                ok = False
            completed += 1 if ok else 0
        elapsed = (time.perf_counter() - started) * 1000.0

    return {
        "implementation": "baseline",
        "baseline_commit": BASELINE_COMMIT,
        "items": count,
        "profile": profile,
        "elapsed_ms": round(elapsed, 1),
        "per_item_ms": round(elapsed / count, 2),
        "dom_calls": portal.dom_calls(),
        "dom_calls_per_item": round(portal.dom_calls() / float(count), 2),
        "execute_script": portal.counters["execute_script"],
        "find_elements": portal.counters["find_elements"],
        "element_reads": portal.counters["element_reads"],
        "probe_calls": portal.counters["probe_calls"],
        "window_handles_scans": portal.counters["window_handles"],
        "plus_clicks": sum(portal.plus_clicks_by_code.values()),
        "completed": completed,
        "stdout_bytes": len(buffer.getvalue()),
    }


# ---------------------------------------------------------------------------
# comparison
# ---------------------------------------------------------------------------

def compare(count: int, profile: str, repeat: int,
            baseline_repeat: int = 1) -> Dict[str, Any]:
    """Run both implementations over the SAME plan on the SAME portal double.

    The baseline is intentionally repeated fewer times: it costs seconds per
    item (that is the finding), so repeating it adds minutes without adding
    information.  The reported figure for each side is its BEST run.
    """
    hardened = [run_hardened(count, profile) for _ in range(repeat)]
    baseline = []
    baseline_error = ""
    for _ in range(max(1, baseline_repeat)):
        try:
            baseline.append(run_baseline(count, profile))
        except Exception as exc:                            # noqa: BLE001
            baseline_error = f"{type(exc).__name__}: {exc}"
            break

    out: Dict[str, Any] = {
        "items": count,
        "profile": profile,
        "repeat": repeat,
        "hardened": min(hardened, key=lambda r: r["elapsed_ms"]),
        "baseline": min(baseline, key=lambda r: r["elapsed_ms"]) if baseline else None,
        "baseline_error": baseline_error,
    }
    if baseline:
        b, h = out["baseline"], out["hardened"]
        # Equal-work guard: a speed comparison is only meaningful when both
        # sides produced the SAME portal outcome.
        out["equal_work"] = (b["completed"] == h["completed"] == count
                             and b["plus_clicks"] == h["plus_clicks"] == count)
        out["delta"] = {
            "elapsed_ms_baseline": b["elapsed_ms"],
            "elapsed_ms_hardened": h["elapsed_ms"],
            "speedup_x": round(b["elapsed_ms"] / h["elapsed_ms"], 2) if h["elapsed_ms"] else None,
            "dom_calls_baseline": b["dom_calls"],
            "dom_calls_hardened": h["dom_calls"],
            "dom_calls_reduction_pct": round(
                100.0 * (b["dom_calls"] - h["dom_calls"]) / float(b["dom_calls"]), 1)
            if b["dom_calls"] else None,
            "per_item_ms_baseline": b["per_item_ms"],
            "per_item_ms_hardened": h["per_item_ms"],
        }
    return out


def render_table(results: List[Dict[str, Any]]) -> str:
    header = (f"{'items':>6} {'profile':>8} | {'baseline ms':>12} {'hardened ms':>12} "
              f"{'speedup':>8} | {'base DOM':>9} {'hard DOM':>9} {'DOM -%':>7}")
    lines = [header, "-" * len(header)]
    for entry in results:
        delta = entry.get("delta")
        if not delta:
            lines.append(f"{entry['items']:>6} {entry['profile']:>8} | "
                         f"baseline ENVIRONMENT_BLOCKED "
                         f"({entry.get('baseline_error', '')[:60]})")
            continue
        lines.append(
            f"{entry['items']:>6} {entry['profile']:>8} | "
            f"{delta['elapsed_ms_baseline']:>12.1f} {delta['elapsed_ms_hardened']:>12.1f} "
            f"{delta['speedup_x']:>7.2f}x | "
            f"{delta['dom_calls_baseline']:>9} {delta['dom_calls_hardened']:>9} "
            f"{delta['dom_calls_reduction_pct']:>6.1f}%")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--items", type=int, default=27)
    parser.add_argument("--profile", choices=sorted(PROFILES), default="fast")
    parser.add_argument("--repeat", type=int, default=3,
                        help="repeats for the hardened implementation")
    parser.add_argument("--baseline-repeat", type=int, default=1,
                        help="repeats for the baseline (slow: seconds per item)")
    parser.add_argument("--all", action="store_true",
                        help="run the standard 27/84 x fast/medium matrix")
    parser.add_argument("--out", default="", help="directory for JSON results")
    parser.add_argument("--locked", action="store_true",
                        help="run the locked-quantity matrix (GP001 x 1/10/30/84/200)")
    parser.add_argument("--locked-code", default="GP001")
    args = parser.parse_args()

    if args.locked:
        data = locked_matrix(args.locked_code)
        print(f"\nLOCKED QUANTITY - {data['code']} (hardened)")
        head = (f"{'units':>6} {'total ms':>10} {'ms/unit':>9} {'DOM':>8} {'proc':>5} "
                f"{'spec':>5} {'reason':>7} {'plus':>5} {'sleep':>6}")
        print(head); print("-" * len(head))
        for h in data["hardened"]:
            print(f"{h['units']:>6} {h['elapsed_ms']:>10.1f} {h['avg_ms_per_unit']:>9.2f} "
                  f"{h['dom_calls']:>8} {h['procedure_selections']:>5} "
                  f"{h['speciality_syncs']:>5} {h['reason_selections']:>7} "
                  f"{h['plus_dispatches']:>5} {h['fixed_sleep_ms']:>6.0f}")
        if data["comparisons"]:
            print(f"\nOLD vs HARDENED ({data['code']} locked)")
            head2 = (f"{'units':>6} {'equal':>6} {'old ms':>11} {'new ms':>9} {'x':>7} "
                     f"{'old DOM':>9} {'new DOM':>8} {'DOM -%':>7}")
            print(head2); print("-" * len(head2))
            for c in data["comparisons"]:
                print(f"{c['units']:>6} {str(c['equal_work']):>6} {c['old_total_ms']:>11.1f} "
                      f"{c['new_total_ms']:>9.1f} {c['speedup_x']:>6.1f}x "
                      f"{c['old_dom_calls']:>9} {c['new_dom_calls']:>8} "
                      f"{c['dom_reduction_pct']:>6.1f}%")
        if args.out:
            out_dir = pathlib.Path(args.out); out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / "benchmark_locked_quantity.json"
            path.write_text(json.dumps({
                "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "baseline_commit": BASELINE_COMMIT,
                "python": sys.version.split()[0], "cpu_count": os.cpu_count(),
                "note": ("Deterministic portal double. Real portal latency is NOT "
                         "modelled; the DOM-call and stage-drive counts are the "
                         "latency-independent findings."),
                **data}, indent=2), encoding="utf-8")
            print(f"\nwrote {path}", file=sys.stderr)
        return 0

    if args.all:
        matrix = [(10, "fast"), (27, "fast"), (10, "medium")]
    else:
        matrix = [(args.items, args.profile)]

    results = []
    for count, profile in matrix:
        print(f"[bench] {count} items, {profile} profile ...", file=sys.stderr)
        results.append(compare(count, profile, args.repeat,
                               baseline_repeat=args.baseline_repeat))

    table = render_table(results)
    print(table)

    scaling = []
    if args.all:
        print("\n[bench] hardened-only scaling ...", file=sys.stderr)
        for count in (27, 84, 200):
            scaling.append(run_hardened(count, "fast"))
        print("\nHardened scaling (fast profile)")
        print(f"{'items':>6} {'elapsed ms':>11} {'ms/item':>9} {'DOM/item':>9} "
              f"{'frame disc':>11} {'tab scans':>10} {'fixed sleep ms':>15}")
        for entry in scaling:
            print(f"{entry['items']:>6} {entry['elapsed_ms']:>11.1f} "
                  f"{entry['per_item_ms']:>9.2f} {entry['dom_calls_per_item']:>9.2f} "
                  f"{entry['frame_discoveries']:>11} {entry['window_handles_scans']:>10} "
                  f"{entry['fixed_sleep_ms']:>15.1f}")

    if args.out:
        out_dir = pathlib.Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "baseline_commit": BASELINE_COMMIT,
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "cpu_count": os.cpu_count(),
            "note": ("Measured against the deterministic portal double. "
                     "Live Chrome / live CGHS portal numbers are NOT_YET_VERIFIED "
                     "in this environment."),
            "results": results,
            "hardened_scaling": scaling,
            "table": table,
        }
        path = out_dir / "benchmark_hotpath.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nwrote {path}", file=sys.stderr)
    return 0




# ---------------------------------------------------------------------------
# locked-quantity benchmark (task section 17)
# ---------------------------------------------------------------------------

def run_locked_hardened(code: str, qty: int, profile: str = "fast") -> Dict[str, Any]:
    portal = build_portal([code], locked_codes={code}, latency=PROFILES[profile]())
    session, orch = _locked_orchestrator(portal)
    started = time.perf_counter()
    result = orch.process_item({"code": code, "qty": qty})
    elapsed = (time.perf_counter() - started) * 1000.0
    c = session.counters
    per_unit = [t * 1000.0 for t in getattr(orch, "_unit_times", [])] or \
               [elapsed / max(1, qty)] * qty
    return {
        "implementation": "hardened", "code": code, "units": qty, "profile": profile,
        "elapsed_ms": round(elapsed, 1),
        "avg_ms_per_unit": round(elapsed / qty, 3),
        "p50_ms": round(statistics.median(per_unit), 3),
        "p95_ms": round(sorted(per_unit)[max(0, int(len(per_unit) * 0.95) - 1)], 3),
        "dom_calls": portal.dom_calls(),
        "execute_script": portal.counters["execute_script"],
        "find_elements": portal.counters["find_elements"],
        "element_reads": portal.counters["element_reads"],
        "fixed_sleep_ms": round(c.fixed_sleep_ms, 1),
        "frame_discoveries": c.frame_discoveries,
        "tab_scans": portal.counters["window_handles"],
        "procedure_selections": c.procedure_selections,
        "speciality_syncs": c.speciality_syncs,
        "reason_selections": c.reason_selections,
        "plus_dispatches": sum(portal.plus_clicks_by_code.values()),
        "verified_units": sum(1 for tx in result.transactions if tx.is_success),
        "rows": len(portal.rows),
        "success": bool(result.success),
    }


def _locked_orchestrator(portal):
    session, orch = make_orchestrator(portal, commit_timeout=2.0, reconcile_grace=0.5)
    session.set_bill("BENCH")
    session.ensure_context()
    return session, orch


def run_locked_baseline(code: str, qty: int, profile: str = "fast") -> Dict[str, Any]:
    """The ORIGINAL _process_locked_quantity, same portal, same plan."""
    portal = build_portal([code], locked_codes={code}, latency=PROFILES[profile]())
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        legacy = load_baseline()
        logger = legacy.EnterpriseLogger(None)
        orchestrator = legacy.TreatmentPlanOrchestrator(portal, logger)
        orchestrator.set_current_bill("BENCH_0")
        started = time.perf_counter()
        try:
            ok = orchestrator.process_item({"code": code, "qty": qty})
        except Exception:                                   # noqa: BLE001
            ok = False
        elapsed = (time.perf_counter() - started) * 1000.0
    return {
        "implementation": "baseline", "code": code, "units": qty, "profile": profile,
        "elapsed_ms": round(elapsed, 1),
        "avg_ms_per_unit": round(elapsed / qty, 3),
        "dom_calls": portal.dom_calls(),
        "execute_script": portal.counters["execute_script"],
        "find_elements": portal.counters["find_elements"],
        "element_reads": portal.counters["element_reads"],
        "plus_dispatches": sum(portal.plus_clicks_by_code.values()),
        "rows": len(portal.rows),
        "success": bool(ok),
    }


def locked_matrix(code: str = "GP001", sizes=(1, 10, 30, 84, 200),
                  baseline_sizes=(1, 10, 30)) -> Dict[str, Any]:
    out = {"code": code, "hardened": [], "baseline": [], "comparisons": []}
    for n in sizes:
        print(f"[bench-locked] hardened {code} x{n}", file=sys.stderr)
        out["hardened"].append(run_locked_hardened(code, n))
    for n in baseline_sizes:
        print(f"[bench-locked] baseline  {code} x{n}", file=sys.stderr)
        try:
            out["baseline"].append(run_locked_baseline(code, n))
        except Exception as exc:                            # noqa: BLE001
            out["baseline"].append({"units": n, "error": f"{type(exc).__name__}: {exc}"})

    by_units = {b.get("units"): b for b in out["baseline"]}
    for h in out["hardened"]:
        b = by_units.get(h["units"])
        if not b or "error" in b:
            continue
        equal = (b["plus_dispatches"] == h["plus_dispatches"] == h["units"]
                 and b["rows"] == h["rows"] == h["units"]
                 and b["success"] and h["success"])
        out["comparisons"].append({
            "units": h["units"],
            "equal_work": equal,
            "old_total_ms": b["elapsed_ms"], "new_total_ms": h["elapsed_ms"],
            "old_avg_ms_per_unit": b["avg_ms_per_unit"],
            "new_avg_ms_per_unit": h["avg_ms_per_unit"],
            "old_dom_calls": b["dom_calls"], "new_dom_calls": h["dom_calls"],
            "speedup_x": round(b["elapsed_ms"] / h["elapsed_ms"], 2) if h["elapsed_ms"] else None,
            "dom_reduction_pct": round(100.0 * (b["dom_calls"] - h["dom_calls"])
                                       / float(b["dom_calls"]), 1) if b["dom_calls"] else None,
            "plus_dispatches": h["plus_dispatches"],
            "verified_units": h["verified_units"],
        })
    return out


if __name__ == "__main__":
    raise SystemExit(main())
