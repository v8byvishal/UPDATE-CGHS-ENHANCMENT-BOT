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
from tests.support.harness import item, make_runner, patient          # noqa: E402
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
    args = parser.parse_args()

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


if __name__ == "__main__":
    raise SystemExit(main())
