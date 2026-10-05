# CGHS Billing Suite — Automation Speed + Safety Hardening

**Branch:** `arena/01a10125-update-cghs-enhancment-bot`
**Baseline commit:** `c3ccdf32e2170891fad9b150c850053461c85a25`
**Target runtime:** Windows, PyInstaller one-file, PyQt5, Chrome attached over CDP `127.0.0.1:9222`

This document records what was wrong, what changed, what was measured, and —
just as importantly — what could **not** be verified in this environment.

Status vocabulary used throughout: `PASS`, `FAIL`, `ENVIRONMENT_BLOCKED`,
`NOT_YET_VERIFIED`, `RECONCILIATION_REQUIRED`.

---

## 1. The headline correctness defect

The baseline treated an **unknown** portal outcome as a **successful** one.

```python
# baseline app.py, lines 1843-1856
self.logger.warning(f"[TX-UNKNOWN-ASSUMED-COMMITTED] {tx_id} assuming committed")
self._mark_state(code, unit_idx, "COMMITTED")
...
return True                      # <- counted as success, bill marked complete
```

If the Plus click dispatched but the row could not be read back within the
polling window — a slow portal, a virtualised grid, a re-render, a dropped
CDP frame — the automation **declared success anyway**. Two different real
failures hide behind that line:

1. the row *was* written and the operator is told "done" without proof; or
2. the row was *not* written and the patient's bill is silently short.

Both end in a wrong bill, and neither is visible in the log as a problem.

### What it does now

Unknown is now a **terminal, operator-gated state**, never a success:

| Situation | Baseline | Now |
|---|---|---|
| Row verified exactly (code + qty + identity) | `COMMITTED` → success | `COMMITTED` → success |
| Commit window elapsed, nothing readable | `COMMITTED` → **success** | `RECONCILIATION_REQUIRED`, frozen |
| Click raised a transport error after dispatch | retried | `RECONCILIATION_REQUIRED`, frozen |
| Click rejected *before* dispatch (provable) | retried | retry granted **once**, proof required |
| Row appeared with the wrong quantity | partially tolerated | `ROW_QTY_MISMATCH` → frozen |
| Row appeared for a different code | tolerated | `ROW_CODE_MISMATCH` → frozen |

A frozen transaction:

* never increments a success counter,
* never permits a second physical Plus,
* is written to the transaction journal with its diagnostic code,
* surfaces in the batch summary as `items_reconciliation_required`,
* and stops the patient's remaining queue rather than piling writes onto an
  unknown portal state.

The only retry that still exists is the one the state machine can **prove** is
safe: the click was rejected by the browser before the event reached the page
(`ElementClickInterceptedException` and friends), the dispatch token was never
consumed, and a bounded settle window confirms the table did not change.

---

## 2. The Plus state machine

Every Plus is now a transaction with an explicit state:

```
PREPARED ──► DUPLICATE_PROVEN                    (row already present: 0 clicks)
   │
   ├──► FAILED_BEFORE_DISPATCH                   (0 clicks, safe to retry)
   │
   └──► DISPATCH_INTENT ──► DISPATCHED ──► WAITING_FOR_COMMIT ──► COMMITTED
             │                   │                    │
             │                   └────────────────────┴──► RECONCILIATION_REQUIRED
             └──► FAILED_AFTER_DISPATCH / CANCELLED
```

Three distinct facts that the baseline conflated are now separate:

| Fact | Meaning |
|---|---|
| **dispatch intent** | we are about to click; a one-shot token is issued |
| **click returned** | Selenium's `click()` came back without raising |
| **commit verified** | the portal table contains the exact expected row |

`DISPATCH_INTENT` is recorded **before** the click, so a process death between
intent and return still leaves evidence that a mutation may exist. The
`DispatchLedger` holds one token per transaction id; `begin_dispatch()`
consumes it, and a second attempt raises `DuplicateDispatchBlocked` and
increments `duplicate_plus_attempts_blocked`.

**Invariant, enforced by test and by fuzzing:** *no transaction id ever
produces two physical Plus mutations.*

---

## 3. Root-cause fixes

Line numbers refer to the baseline `app.py`.

| # | Defect | Root cause | Fix |
|---|---|---|---|
| 1 | Unknown counted as success | 1843‑1856 | terminal `RECONCILIATION_REQUIRED` |
| 2 | `DISPATCHED` set before clicking | 1473‑1515 | split `begin_dispatch()` / `confirm_browser_click()` |
| 3 | Blocked duplicate returned success | 1786‑1800 | `DUPLICATE_PROVEN` + counter, zero clicks |
| 4 | Outer reconcile flipped failures to success | 2126‑2137 | exact `row_matches_code` + quantity-delta proof |
| 5 | `locate_all` returned duplicates | 988‑999 | element-identity dedupe |
| 6 | Fuzzy code matching | ~1155 | word-boundary match: `C001` ≠ `CC001`, `LB012` ≠ `LB0121` |
| 7 | Build intermediates committed, incl. a `struct.pyc` that shadowed the stdlib | repo root | removed + `.gitignore` |
| 8 | Absence asserted before the async commit window | verifier | bounded settle window; any mutation ⇒ UNKNOWN ⇒ freeze |
| 9 | Reason failure mislabelled `SPECIALITY_LOCKED` | reason path | `REASON_MISSING` after one permitted re-sync |
| 10 | Browser disconnect laundered into "control not found" | resolver/waiter | disconnects re-raised, never swallowed |
| 11 | No proof the open plan belongs to the queued patient | tab layer | `PatientNotSwitched` STOP before any write |
| 12 | All click failures treated alike | click path | rejection-class may prove absence; transport-class ⇒ freeze |
| 13 | `except` subclass ordered after superclass | handlers | ordering corrected; specific first |
| 14 | iframe-hosted Treatment Plans rejected at tab level | tab layer | `TabEvidence.plan_candidate` |

Fixes 11 and 14 were found **by the new tests**, not by reading the code.

---

## 4. Performance

### 4.1 Where the time went

Profiling the baseline against a deterministic portal double produced this
cost inventory:

* `wait_for_idle()` — a **15 second** default budget — ran at the head of the
  Procedure, Quantity, Procedure-Name, Amount and Reason controls, **and again
  inside every 0.08 s iteration** of `RowCodeQtyVerifier.execute`. Each call is
  one `execute_script`, one `find_elements`, an `is_displayed()` per spinner
  and a `sleep(0.05)`.
* `ensure_frame()` (1038‑1049) performed a **full iframe scan per item**, after
  the frame was already known.
* Commit verification re-read the **entire table** on every poll, so cost grew
  with the number of rows already added — the batch got slower as it went.
* Fixed sleeps: dropdown poll 6 s @ 0.08 s, speciality ≤ 5 s @ 0.08 s,
  `CDPDOMObserver.wait_for_mutation` @ 0.05 s.

### 4.2 What changed

| Area | Baseline | Now |
|---|---|---|
| Browser attach | per patient | **one** per batch process |
| Treatment Plan discovery | per item | once, cached + revalidated |
| Frame binding | `ensure_frame()` per item | cached; re-bound only on proven loss |
| Locator strategy | rediscovered per control per item | session-scoped cache, invalidated on staleness |
| Table read | full table, repeatedly | one compact `execute_script` probe, documented DOM fallback |
| Verification | full-table rescan | **targeted**: only rows after the pre-click count |
| Waiting | fixed sleeps | event/condition-driven adaptive poll |
| `wait_for_idle` | 15 s gate per micro-operation | cheap common path; the gate is the exception |

No verification, retry, reconciliation or safety gate was removed to achieve
this. The mutation sequence inside one patient's Treatment Plan remains
strictly serial and deterministic — **no parallel portal mutations**.

### 4.3 Measured before/after

Both implementations drive the **same** portal double with the **same** plan.
The harness refuses to report a speedup unless both sides produced identical
outcomes (`equal_work=true`: same completions, same physical Plus clicks).

Reproduce with:

```
python3 tools/benchmark_hotpath.py --all --repeat 3 --baseline-repeat 1 --out docs/perf
```

Environment: Python 3.11.2, Linux, 2 CPUs, 2026‑10‑03. Raw data:
`docs/perf/benchmark_hotpath.json`.

| items | profile | baseline ms | hardened ms | speedup | baseline DOM | hardened DOM | DOM reduction |
|---:|---|---:|---:|---:|---:|---:|---:|
| 10 | fast | 116 307.4 | 311.7 | **373×** | 43 922 | 637 | **98.5 %** |
| 27 | fast | 311 531.7 | 843.0 | **370×** | 194 236 | 1 725 | **99.1 %** |
| 10 | medium | 117 547.0 | 3 076.6 | **38×** | 43 982 | 747 | **98.3 %** |

DOM round trips per item — the latency-independent figure:

| items | baseline DOM/item | hardened DOM/item |
|---:|---:|---:|
| 10 | 4 392.2 | 63.7 |
| 27 | 7 193.9 | 63.9 |

**The important number is not the speedup, it is the shape.** The baseline's
per-item cost *grows with batch size* (4 392 → 7 194 DOM calls per item going
from 10 to 27 items) because verification rescanned the whole table. The
hardened path is **flat**:

| items | elapsed ms | ms/item | DOM/item | frame discoveries | full tab scans | fixed sleep ms |
|---:|---:|---:|---:|---:|---:|---:|
| 27 | 849.3 | 31.45 | 63.89 | 1 | 1 | 0.0 |
| 84 | 2 672.0 | 31.81 | 63.96 | 1 | 1 | 0.0 |
| 200 | 6 568.2 | 32.84 | 63.98 | 1 | 1 | 0.0 |

O(n²) became O(n). One attach, one discovery, one tab scan, zero fixed
sleeping, at every batch size.

### 4.4 Honest reading of these numbers — `NOT_YET_VERIFIED` on live

The portal double answers instantly, so the baseline's **fixed sleeps dominate
its wall clock**; a real portal with real network latency will not show 370×.
The defensible, latency-independent claims are:

* DOM round trips per item: **≈ 98–99 % fewer**,
* per-item cost no longer grows with batch size,
* fixed sleeping on the happy path: **0 ms** (asserted by test),
* one CDP attach / one discovery / one tab scan per batch (asserted by test).

Live Chrome and live CGHS/NHA portal timings are **NOT_YET_VERIFIED** — this
sandbox has no browser and no portal access.

---

## 5. Telemetry

Per item: `run_id`, `bill_id`, `final_code`, `quantity`, `state`, `stage`,
`elapsed_ms`, `dom_calls`, `cache_hits`, `cache_misses`, verification outcome,
reconciliation outcome, `diagnostic`.

Per batch (`BatchPerformanceSummary.as_dict()`, emitted on `performance_signal`):
`batch_elapsed_ms`, `items_total`, `items_completed`, `items_failed`,
`items_reconciliation_required`, `items_skipped`, `avg_item_ms`, `p50_item_ms`,
`p95_item_ms`, `total_dom_calls`, `total_execute_script_calls`,
`full_tab_scans`, `frame_discoveries`, `fixed_sleep_ms`, `retries`,
`duplicate_plus_attempts_blocked`.

`duplicate_plus_attempts_blocked` is the one to watch: it must stay at 0 in a
healthy batch, and any non-zero value means the ledger stopped a second
mutation that the old code would have performed.

---

## 6. Business rules — pinned by tests, with one declared change

### 6.0 Parser findings from the section-10 evidence

The brief supplied observed values for two real bills. Reconstructing the
documented department structure surfaced a defect and a second, weaker
suspicion. They were treated **differently on purpose**, according to how
strong the evidence was.

#### FIXED — `extract_consumables_total` read the wrong number (CNSU100)

The baseline scanned one alternating regex with `finditer`:

```python
r'Dept\s*Sub\s*Total\s*:?\s*([\d,]+\.\d{2})|([\d,]+\.\d{2})\s*Dept\s*Sub\s*Total'
```

`finditer` matches at the **earliest** position. When a line-item amount sat
immediately above a `Dept Sub Total :` label — the ordinary two-column PDF
extraction — the value-first branch matched that **row** amount and consumed
the label, so the real subtotal that followed was never read.

Two consequences, both real:

* `extract_dept_subtotal` (feeding **DRUG100**) and `extract_consumables_total`
  (feeding **CNSU100**) returned *different values for the same text*.
* The same bill totalled differently depending only on how the PDF extracted:

| layout | baseline CNSU100 | fixed |
|---|---:|---:|
| label-first (`Dept Sub Total : 6,486.50`) | 3,586.50 | **8,993.90** |
| value-first (`6,486.50 Dept Sub Total`) | 8,993.90 | 8,993.90 |

The decisive point: **the baseline itself computes 8,993.90** via its
value-first path. That is the intended semantics, so label-first was simply
wrong. The label is now anchored first and the amount resolved around it —
label-first, then value-first — exactly the precedence `extract_dept_subtotal`
already applied. Every unambiguous layout is **byte-for-byte unchanged**.

This is the **only** permitted deviation from the baseline rules. It is
registered in `DECLARED_RULE_DEVIATIONS` with its reason, and
`test_rules_module_is_a_verbatim_copy_of_the_baseline_block` fails on any
*undeclared* change to any rule function.

> **Operator action:** CNSU100 may now be higher on bills that previously hit
> the ambiguous layout. This is a correction, not an inflation — but confirm
> against one real bill before relying on it.

#### OPEN FINDING — `extract_dept_subtotal` can lose a department (DRUG100)

`extract_dept_subtotal` returns **0.00** for a department whose subtotal is
value-first when another department follows. The header regex
`[A-Za-z][A-Za-z\s]*\(\s*999311\s*\)` lets `[A-Za-z\s]*` span newlines, so the
next "header" is matched starting at `Dept Sub Total\n\nOT Pharmacy(999311)`
instead of at `OT Pharmacy`. The slice is cut before its own subtotal.

**Deliberately not fixed.** Status `NOT_YET_VERIFIED`. Unlike the defect above
there is no internal oracle: the baseline yields 0.00 on every path, so
"fixing" it would mean *choosing* a billing number with no evidence — and the
locked mappings must not change because a fixture suggests something. The
reproducer is kept executable as a strict `xfail`
(`test_FINDING_dept_subtotal_loses_value_first_departments`), so the day it is
addressed the suite says so. Resolving it needs the real PDFs.

## 6.1 Business rules — unchanged, and pinned by tests

These were **not** touched. `tests/test_parser_rules.py` loads the *original*
module from the baseline commit and asserts the new code agrees with it
row-for-row:

* **CC001** ← counted ICU Room Rent rows.
* **WC001** ← counted qualifying ward rows (AC multibeds + single + other).
* **CN002** = `icu_count × 3 + ward_count × 2`, a locked derivation.
  A raw CN002 consultation row in the bill is **rejected**, not trusted.
* **CC002** only when `OXYGEN` appears in the row (24/12/1, summed).
* **DRUG100** = IP + OT Pharmacy subtotals; **CNSU100** ← consumables total.
* Portal mapping: `DRUG100` → `drugs(DRGU100-None)`, `CNSU100` →
  `consumables(CNSU100-None)` — locked, not inferred from fixtures.
* `Patient Payable`, `Grand Total` and `Payer Payable` are excluded from
  department subtotals.
* Aggregation happens **after** normalisation.

No case-specific amount was promoted to a rule. No final billing result was
invented for bill 40343.

---

## 7. Tests

| File | Tests | Covers |
|---|---:|---|
| `tests/test_plus_state_machine.py` | 35 | correctness + the full enumerated failure list |
| `tests/test_performance_caching.py` | 17 | session reuse, caches, DOM budgets, zero fixed sleep |
| `tests/test_tab_frame_safety.py` | 11 | tab/frame/patient-identity safety |
| `tests/test_parser_rules.py` | 66 | differential against the baseline rules + declared-deviation gate |
| `tests/test_stress.py` | 9 | 27 / 84 / 200 items × fast / medium / slow |
| `tests/test_property_fuzz.py` | 64 | seeded random portals + invariants |
| `tests/test_packaging.py` | 10 | canonical ownership, no duplicate engines, artifact |
| `tests/test_real_format_bills.py` | 23 | 40343 / 39078 evidence, layout independence, honesty guards |

Notable properties asserted rather than hoped for:

* An AST check (not a grep) proves no `ASSUMED-COMMITTED` marker survives and
  that `window_handles` is **never** subscripted anywhere in the codebase.
* Two valid patient tabs ⇒ `AmbiguousPatientTab` naming both handles, **zero**
  Plus clicks, `FAILED_BEFORE_DISPATCH`.
* Locked-quantity 3 ⇒ exactly 3 clicks, stopping at the first unverified unit.
* A code that commits is never re-clicked on a re-run of the same plan.
* A portal whose table cannot be read **at all** can never produce a success —
  every item freezes for the operator. (Found by fuzz seeds 5, 11 and 31.)

### Not verified here

* **Real-format bill regression (40343, 39078, 40337, D1–D5): `ENVIRONMENT_BLOCKED`.**
  No such PDFs exist in the repository, and a Google Drive search returned
  none. Two things stand in for them in `tests/test_real_format_bills.py`:
  the documented section-10 values (patient, IP/bill number, OT Consumables
  6,486.50 / OT Pharmacy 447.70 for 40343, OT Consumables 2,507.40 for 39078,
  Room Rent ICU and Single composition) are reconstructed in the department
  layout the parser is specified to read and asserted exactly; and
  `_find_bill()` picks up the real PDFs automatically the moment they are
  dropped into the repo, at which point 8 currently-skipped tests activate.
  What this proves is that the parser handles the documented structure — not
  that a real PyMuPDF extraction produces that exact layout, which is why the
  status stays ENVIRONMENT_BLOCKED rather than PASS.
  No final billing result is asserted for 40343; the brief says none is
  proven, and `test_no_final_result_is_invented_for_40343` enforces that no
  module hardcodes a case identity or a case amount.
* **Live portal execution: `NOT_YET_VERIFIED`** — no Chrome, no display, no
  portal credentials in this environment, and credential automation is
  forbidden by the brief regardless.
* **Windows EXE build: `NOT_YET_VERIFIED`** — PyInstaller targets Windows; this
  is Linux. The spec file now collects the `cghs` submodules so the frozen
  build will not fail at runtime with `ModuleNotFoundError`, but that is a
  code-level fix, not an executed build.

---

## 8. Layout

```
app.py                      PyQt5 UI + DiagnosticEngine + thin BatchAutomationThread
cghs/
  locators.py               locator + CGHS code registry
  telemetry.py              logger, perf counters, adaptive poller, batch summary
  rules.py                  pure CGHS text rules (verbatim from baseline)
  parsing.py                PDF -> plan items (lazy PyMuPDF)
  dom.py                    compact probe, cached resolution, targeted waits
  txstate.py                Plus state machine, dispatch ledger, journal
  tabs.py                   tab discovery with evidence + ambiguity stop
  session.py                one attach, one verified cached context
  controllers.py            one class per portal control
  orchestrator.py           deterministic item sequence + batch runner
tools/
  benchmark_hotpath.py      measured baseline vs hardened comparison
  build_package.py          build AND reopen-verify the delivery ZIP
docs/
  AUTOMATION_PERFORMANCE_HARDENING.md
  perf/benchmark_hotpath.json
```

`app.py` went from 3 247 lines to 1 329, with CRLF endings preserved. It
defines **no** automation class; `tests/test_packaging.py` fails the build if
any engine class is ever defined in two places.

---

## 10. Locked quantity — one selection, N verified units

### 10.1 The requirement

A code whose portal quantity field is **locked** (read-only) cannot be billed by
typing `30` into the quantity box. The portal only accepts one unit per Add, so
a required quantity of 30 means **30 physical Plus dispatches, each verified**.

The baseline did that correctly but expensively: for every one of the 30 units it
re-selected the procedure, re-synchronised the speciality, re-picked the reason
and re-scanned the whole table. 30 units cost 30 procedure selections, 30
speciality syncs, 30 reason selections and 30 full-table scans.

### 10.2 What changed

`TreatmentPlanOrchestrator._process_locked_quantity` now drives the first unit
through the full proven flow and then, before each subsequent unit, issues **one**
`StageContextProbe.read()` — a single `execute_script` that returns the live
`procedure`, `speciality`, `reason`, `quantity` and `plus` state in one
round-trip. Each stage is re-driven **only if the probe proves it is no longer
ready**:

| stage | reuse predicate (`cghs/controllers.py: StageContext`) |
|---|---|
| procedure | `exact_code_in_text(value, code) or portal_target in value` — the *same* predicate `ProcedureSelector._verify_selection` uses, so "still selected" and "selection verified" can never disagree |
| speciality | value present and not a placeholder |
| reason | control absent, or value already starts with `OTHER` |

Three properties make this safe rather than merely fast:

1. **Reuse is observed, never assumed.** No probe evidence ⇒ no reuse.
2. **`probed=False` forces every predicate to `False`.** A portal that does not
   support the compact probe (`ProbeUnsupported`) re-drives the full proven flow
   for every unit. Such a portal loses the speed-up and keeps every gate.
3. **Speciality loss forces a procedure re-select.** The portal *derives* the
   speciality from the procedure, so syncing a dropped speciality on its own
   deadlocks. `_restore_stages` therefore sets `needs_procedure = True` whenever
   the speciality must be restored.

Per-unit verification is unchanged: every unit still gets its own transaction id,
its own ledger authorisation, its own `MutationWatch`, and its own targeted
commit verification. The invariant **one physical Plus per transaction id** is
asserted per unit, not per item.

### 10.3 Measured — `GP001`, quantity locked

Hardened engine, deterministic portal double:

| units | total ms | ms/unit | DOM calls | DOM/unit | proc sel | spec sync | reason sel | Plus | verified | fixed sleep ms |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 31.3 | 31.28 | 61 | 61.0 | 1 | 1 | 1 | 1 | 1 | 0 |
| 10 | 126.9 | 12.69 | 160 | 16.0 | 1 | 1 | 1 | 10 | 10 | 0 |
| 30 | 334.0 | 11.13 | 380 | 12.7 | 1 | 1 | 1 | 30 | 30 | 0 |
| 84 | 909.1 | 10.82 | 974 | 11.6 | 1 | 1 | 1 | 84 | 84 | 0 |
| 200 | 2211.1 | 11.05 | 2250 | 11.2 | 1 | 1 | 1 | 200 | 200 | 0 |

Against the original implementation, **equal work enforced** (the comparison is
discarded unless both runs dispatch exactly `units` times, leave exactly `units`
rows and both report success):

| units | old ms | new ms | speed-up | old DOM | new DOM | DOM | Plus | equal work |
|---:|---:|---:|---:|---:|---:|---:|---:|:--:|
| 1 | 1797.1 | 31.3 | **57.4×** | 202 | 61 | **−69.8%** | 1 | TRUE |
| 10 | 2977.5 | 126.9 | **23.5×** | 2110 | 160 | **−92.4%** | 10 | TRUE |
| 30 | 5620.2 | 334.0 | **16.8×** | 13890 | 380 | **−97.3%** | 30 | TRUE |

The headline case — **`GP001`, required quantity 30** — is
**5620 ms → 334 ms
(16.8×) and 13890 → 380
DOM calls (−97.3%)**, while still producing
**30 Plus dispatches, 30 portal rows and 30 individually verified units**.
Procedure selections, speciality syncs and reason selections fall from 30 each to
**1 each**.

Per-unit cost is **flat**: 12.7 ms/unit at 10 units and
11.1 ms/unit at 200. The baseline's DOM cost per unit
*grows* (202 → 211 →
463 calls/unit at 1/10/30 units) because it rescans the
full table once per unit — O(n²) in the quantity. The hardened reader is
probe-first and targeted, so the curve is O(n).

Reproduce with:

```
python3 tools/benchmark_hotpath.py --locked --out docs/perf
```

Raw results: `docs/perf/benchmark_locked_quantity.json`.

### 10.4 Honesty caveats

* The portal double answers instantly. **The wall-clock multiples are flattered
  by the baseline's fixed sleeps.** The latency-independent and therefore
  defensible findings are the DOM-call reduction, the 30→1 stage-drive
  reduction, and the flat per-unit curve.
* **I cannot determine whether the real portal clears the procedure, speciality
  or reason after an Add.** The baseline re-selects unconditionally, which is
  defensive, not evidence. The engine is therefore probe-driven and is correct
  under *both* behaviours; the fake models all five combinations
  (`reset_procedure_after_add`, `reset_speciality_after_add`,
  `reset_reason_after_add`) and each is covered by a test. Real-portal
  confirmation is **NOT_YET_VERIFIED**.
* Amount-based codes (`DRUG100`, `CNSU100`) are returned **before** the locked
  branch is reached and can never be split into units;
  `test_amount_based_codes_are_never_split_into_units` pins this.

### 10.5 A fidelity bug found in the test double

While benchmarking, the baseline appeared to deliver only **half** the required
quantity (15 of 30). The cause was the double, not the portal: `FakeElement`
minted a fresh element id on every construction, so the same logical row
returned through two locator strategies looked like two different nodes. Real
Selenium returns the *same* element id for the same DOM node, which is what
`SmartDOMResolver._identity()` dedupes on. Two fixes were applied to
`tests/support/fake_portal.py`:

* row elements now carry a **stable** `eid` (`row-0`, `row-1`, …);
* the double renders exactly **one** row layout, because the two registered
  `TABLE_ROWS` strategies target structurally different markup
  (`//table//tbody//tr` vs `//div[treatment-grid]//div[row]`) and cannot both
  match a real DOM. The `overlapping_row_locators` fault remains, as the
  explicit opt-in that models nested markup and exercises the dedupe path.

The underlying production fix is real and already shipped (root cause #5,
`locate_all` deduplication). The conditional risk is worth stating plainly: **on
any DOM where both row strategies match, the original `locate_all` — which
`extend`s across every strategy with no dedupe — double-counts every row, and
the locked-quantity guard then stops at half the required quantity.** The
hardened resolver dedupes by element identity and is immune. Whether such a DOM
exists on the live portal is **NOT_YET_VERIFIED**.

## 11. React-Select: the procedure was typed, not selected

### 11.1 The report and the root cause

The operator reported that the bot typed a procedure code into the portal but
the portal did not accept it until a human pressed **Enter** by hand.

The cause is not a missing keystroke. The portal's procedure control is
**React-Select** (`#react-select-5-input`, `role="combobox"`,
`aria-controls="react-select-5-listbox"`), and React-Select keeps only the
**search text** in that input: the moment an option is committed it **clears
the input** and renders the chosen label in a sibling `singleValue` node.

The engine verified selection by reading `input.value`. Against React-Select
that check is not merely weak, it is **inverted**:

| actual state | `input.value` | pre-fix verdict |
|---|---|---|
| typed, nothing selected | `"GP001"` | **PASS** (wrong - nothing was selected) |
| genuinely selected | `""` | **FAIL** (wrong - the selection was correct) |

Both halves are reproduced in `docs/evidence/react_select_defect_repro.txt`,
generated by running the **unmodified** engine from `8402c1e` against a
faithful React-Select double. That is the whole defect: a correct selection
was rejected, and a non-selection was reported as `PROCEDURE_VERIFIED`.

### 11.2 What changed

* **`PROBE_JS` now reports committed state.** `ctl()` gained `selected` (the
  `singleValue` label, walked up to 6 ancestors) and `combobox`. Still one
  round trip; no new DOM calls.
* **One predicate, reading committed state.** `_selection_committed()` is the
  only judge of "is it selected". `_verify_selection()` is now a thin alias,
  so the two cannot drift. `StageContext.procedure_matches()` reads the same
  committed value. On a combobox the search text is **explicitly refused** as
  evidence; on a classic input, where `value` genuinely is the committed
  value, it is still honoured.
* **Enter is dispatched by Selenium**, not by the operator - but only after
  React's focused option has been walked onto the **exact** match using
  ArrowUp/ArrowDown, verified through `aria-activedescendant`. React-Select
  commits whatever option is focused, so pressing Enter blindly can select
  `GP001A` when `GP001` was asked for.
* **No JavaScript click is ever used to commit an option.** A JS
  `element.click()` fires only a `click` event; React-Select's Option listens
  on **mousedown**, so a JS click silently changes nothing while leaving the
  typed text behind looking like success. The fallback is a *real* Selenium
  click, which dispatches mousedown. `test_I2` pins `js_clicks == 0`.
* **Commit order follows the control type.** React-Select: Enter, then a real
  option click. Classic control: option click first (the proven baseline
  path), Enter as recovery - because on a classic input the typed text *is*
  the value, so trying Enter first would let typed text masquerade as a
  selection.
* **Option queries are scoped to the control's own listbox** via
  `aria-controls`, so a reason picker can never match a procedure option.
  Scoping is skipped on classic controls, where no second listbox can exist,
  which keeps the DOM cost identical to the baseline.
* **Live selectors preferred, fallbacks kept.** `#react-select-5-input`,
  `#react-select-4-input`, `#react-select-7-input`, `#noofdays` and
  `div[id^='react-select-'][id*='-option-']` are now the *first* strategies in
  the registry; every pre-existing strategy remains behind them.

### 11.3 Speciality: derived by the portal, verified here, never guessed

The portal derives the speciality from the procedure. `verify_expected()`
waits for that derivation and returns one of `AUTO_VERIFIED`,
`AUTO_UNVERIFIED`, `MISSING` or `WRONG`.

The judgement uses `resolve_expected_speciality()`, which contains **only the
four prefixes the operator supplied from the live portal** - `LB`→Laboratory,
`RI`→Radiology, `CN`→Consultation, `BL`→Blood. There is **no CGHS
speciality registry in this repository**, so the table is deliberately
incomplete and must not be extended to make a test pass. An unresolvable code
returns `None`, which is `AUTO_UNVERIFIED`/`REVIEW_REQUIRED` territory - a
human decides. `test_L2` pins that `GP001` resolves to `None` and is never
guessed.

The procedure→speciality coupling fix from section 10 still applies: a dropped
speciality forces a procedure re-select, because the portal derives one from
the other and syncing alone deadlocks.

### 11.4 Measured - React-Select DOM, locked quantity

Every number is **read back from the double's own state after the run**, never
asserted in advance:

| units | ms | ms/unit | Plus | rows | verified | proc | spec | reason | reuses | DOM | stale | sleep ms |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 3.3 | 3.29 | 1 | 1 | 1 | 1 | 1 | 1 | 0 | 86 | 0 | 0 |
| 10 | 4.8 | 0.48 | 10 | 10 | 10 | 1 | 1 | 1 | 27 | 176 | 0 | 0 |
| 30 | 16.1 | 0.54 | 30 | 30 | 30 | 1 | 1 | 1 | 87 | 376 | 0 | 0 |
| 84 | 97.7 | 1.16 | 84 | 84 | 84 | 1 | 1 | 1 | 249 | 916 | 0 | 0 |
| 200 | 617.3 | 3.09 | 200 | 200 | 200 | 1 | 1 | 1 | 597 | 2076 | 0 | 0 |

`GP001` quantity **30** ⇒ **30 Plus dispatches, 30 rows, 30 individually
verified units**, with **1** procedure selection, **1** speciality, **1**
reason, **87 observed stage reuses**, **0 ms fixed
sleep**, and the locked `#noofdays` field **never written**
(`quantity_field_written = false` at every size).

Reproduce: `python3 tools/benchmark_hotpath.py --react --out docs/perf`
Raw: `docs/perf/benchmark_react_select.json`.

### 11.5 Tests that fail against the old code

`tests/test_react_select_procedure.py` adds 33 tests covering section 13 A-P.
Checked out against the pre-fix `cghs/` from `8402c1e`, **16 of them fail**;
all 33 pass after the fix. The two most direct are
`test_REGRESSION_typed_text_is_never_accepted_as_a_selection` (the false PASS)
and `test_REGRESSION_a_real_commit_is_recognised_even_though_the_input_is_empty`
(the false FAIL).

### 11.6 Honesty caveats

* The React-Select double is built from the operator's DOM capture plus
  documented React-Select v5 behaviour. **It is not the live portal.**
  Real-portal confirmation of this fix is **NOT_YET_VERIFIED**.
* Wall-clock figures come from a double that answers instantly. The
  defensible findings are the DOM-call counts, the 1/1/1 stage drives and the
  zero fixed sleep - not the millisecond totals.
* Per-unit cost rises above ~84 units (0.54 ms/unit at 30, 3.09 at 200)
  because commit verification still scans the row set. That is a real
  O(n) effect and is reported rather than hidden.
* A **bug in my own fix** was caught by these tests: the commit-order loop
  compared bound methods with `is`, which is never true, so the keyboard path
  never ran and both attempts were the option click. Fixed and pinned by
  `test_I`.
* The Add control is dispatched with a JS click by the existing
  `PlusButtonController`, which is correct for an ordinary React `onClick`.
  Were the live Add ever mousedown-only, the existing `MutationWatch` +
  `CommitVerifier` gates would observe no row and raise
  `RECONCILIATION_REQUIRED` rather than report success - it fails safe.

## 12. The unit-19 cascade: a dead control reported as a dead context

### 12.1 What the operator saw

A 34-code batch. Nine codes succeeded. `GP001` locked quantity 30 began and
units 1-18 committed. At unit 19:

```
element not interactable
[FRAME-CACHE] invalidated
[BROWSER] GP001 ... element not interactable
FAILED_BEFORE_DISPATCH   TX-PORTAL-CONTEXT-LOST
```

...then the same four lines for every one of the 24 remaining codes - each
preceded by `[CONTEXT] verified Treatment Plan`. The engine declared the
context lost and then immediately proved it was fine, **25 times**.

That contradiction is the whole bug. **The frame was never invalid.**

### 12.2 Three defects, none of them code-specific

**1. Classification.** `ElementNotInteractableException` is a subclass of
`WebDriverException`. `process_item` caught `NoSuchElement` and
`StaleElement` ahead of `WebDriverException` - but not this one - so a
perfectly present control that merely could not be typed into fell into the
clause that calls `invalidate_context()` and reports
`TX-PORTAL-CONTEXT-LOST`. An **element** fault was reported as a **context**
fault, and the wrongly dumped tab/frame/locator caches guaranteed every later
item repeated the same doomed lookup.

**2. Resolver.** `SmartDOMResolver.locate()` ended with, in effect, *"return
the first match, hidden or not, and cache the strategy that produced it."*
React does not remove a remounted control - it leaves the old input in the
document, hidden - so `#react-select-5-input` still matched. The last resort
handed back that hidden clone **and poisoned the strategy cache with it**, so
every retry re-found it. `_click()` swallowed the resulting exception and
fell back to a JS click; `send_keys` then raised for real.

**3. No shared-control concept.** `BatchRunner` ran all 24 remaining codes
against the identical broken control, converting one portal fault into 25
"code failures" and burying the real cause.

### 12.3 Proof, not inference

`docs/evidence/cascade_repro_prefix.txt` reproduces it against the engine as
committed: **25x `TX-PORTAL-CONTEXT-LOST`, 25x frame-cache invalidation, 25x
`CONTEXT verified Treatment Plan`**, with units 1-18 committed first.

The portal model contains only operator-reported conditions: React remounts
the procedure control after N Adds leaving a hidden clone behind; the portal
clears the procedure after every Add (which is *why* the engine re-selects it
each unit, and why the fault first appears at unit 19); and `find_elements`
returns hidden elements, as real Selenium does.

### 12.4 The fix

| area | change |
|---|---|
| classification | `ElementNotInteractableException` caught **before** `WebDriverException` as `TX-PROCEDURE-CONTROL-UNAVAILABLE`. Tab, frame and locator caches are left **untouched**. |
| resolver | For `INTERACTIVE_CONTROLS` the last resort refuses a non-interactable element instead of returning and caching it. **ABSENT** (`NoSuchElement`) and **PRESENT-BUT-HIDDEN** (`ElementNotInteractable`) stay distinguishable, so optional controls still behave. |
| locators | Instance-independent strategies anchored on each control's **own** react-select container, so `react-select-5` → `react-select-9` is found without hardcoding either id. The `[1]` on the container means it can never reach speciality or reason. |
| reacquisition | `_acquire_interactable()` + a targeted retry on stale/non-interactable **while typing**. The element is dropped; the session is not. |
| cascade | One controlled recovery, then a decision. Re-finding the Treatment Plan is **not** accepted as proof the procedure control works. |
| frame validation | `_cheap_validate()` demanded only `ctx["inputs"] > 0`; any iframe with any input passed. It now requires **2 of 6** Treatment Plan signatures (heading, procedure, speciality, quantity, reason, Plus), all from the same single probe. |

### 12.5 Measured - the reproduced 30-code batch

| | status | done | failed | pending | shared faults | frame rediscoveries | DOM calls | fixed sleep |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| **pre-fix** | PARTIAL | 7 | **23** | 0 | n/a | **25** | - | 0 ms |
| control never returns | `STOPPED_SHARED_PORTAL_CONTEXT_FAILURE` | 7 | **1** | **22** | 1 | 1 | 1205 | 0 ms |
| control remounts OK | `COMPLETED` | 30 | 0 | 0 | 0 | 1 | 4064 | 0 ms |

23 code-failures become **1 real failure plus 22 preserved as `pending`** -
not attempted, and not blamed. `TX-PORTAL-CONTEXT-LOST` and frame-cache
invalidations both drop from 25 to **0**. When the remount leaves a usable
control, all 30 codes complete with **1** frame discovery.

No fixed sleep was added anywhere: the recovery is condition-driven.
Raw data: `docs/perf/cascade_recovery.json`.

### 12.6 Tests

`tests/test_portal_control_cascade.py` - 20 tests covering section 10 A-H
plus the section 9 frame-validation hardening. **16 fail against the pre-fix
engine**; all 20 pass after.

### 12.7 Honesty caveats

* **Not reproduced on the live portal.** This sandbox is headless Linux with
  no CDP target, so the §1 live capture (URL, tab handle, frame chain, active
  element, candidate rects) **could not be performed**. The reproduction is a
  deterministic model that emits the operator's log sequence line for line;
  the specific trigger at unit 19 on the real DOM remains
  **NOT_YET_VERIFIED**.
* What *is* proven independently of the trigger: once any
  `ElementNotInteractableException` reaches `process_item`, the pre-fix code
  **must** dump the frame cache, **must** report `TX-PORTAL-CONTEXT-LOST`,
  and **must** attempt every remaining item. That is visible in the control
  flow, not just in the model.
* `MIN_SIGNATURES = 2` is a judgement call. One signature was too weak
  (a stray "Treatment Plan" heading); requiring all six would reject
  legitimate pages where the Plus control is not yet rendered.
* The PT004/PT005 post-Plus reconciliation finding is a **different** defect
  and has deliberately not been touched.

## 13. Post-Plus: driving the REAL current Procedure control

### The live evidence

    CN002  procedure -> speciality -> quantity -> Plus dispatched
                                               -> Plus confirmed -> COMPLETED
    C001   TYPE_PROCEDURE -> CONTROL-UNAVAILABLE
           one controlled recovery
           Treatment Plan context is VALID
           Procedure control is still not interactable
           STOP shared portal control; remaining 9 items PENDING

The cascade protection from section 12 did exactly what it should: it named
the CONTROL, kept the frame, recovered once, then stopped and left the
untried codes `PENDING` rather than inventing nine failures.  The defect is
upstream of all of that - the engine could not find the control that was
sitting on the screen.

### Root cause

After the Plus, React re-renders the Procedure field.  The spent
react-select container is left in the document, hidden, and the replacement
is mounted immediately **after** it with a new instance number.  Every
registered strategy was anchored either to an INSTANCE or to a POSITION:

| strategy | after the re-render it resolves to |
|---|---|
| `#react-select-5-input` | the spent input |
| `input[...aria-controls*='react-select-5']` | the spent input |
| `//label[...procedure...]/following::div[...-container...][1]//input` | the spent **container** |
| `//label[...procedure...]/following::input[1]` | the spent input |
| `//*[@formcontrolname='procedure']//input` | nothing (the portal is React) |
| `//input[contains(@id,'procedure')]` | nothing - `react-select-9-input` does not contain the word |

So the live control was never a candidate.  Every selector still matched the
hidden clone, which is why `locate()` reported "present but not
interactable" rather than "absent" - and why one recovery could not help:
re-running the same position-anchored lookup produced the same dead element.
The `[1]` is the whole defect in one character.

### The fix

1. **Position independence** (section 8).  Two strategies were added that
   carry no positional predicate: every react-select container after the
   Procedure label, and every combobox input after it.  Nothing anywhere
   names an instance number.
2. **Arbitration** (section 11).  Those strategies are deliberately broad -
   they also match Speciality and Reason - so a candidate produced by any
   label-relative strategy is checked against the neighbouring controls
   before it is accepted.  Zero candidates is `CONTROL_UNAVAILABLE`, one is
   used, and more than one raises `AmbiguousControlException` instead of
   silently returning `candidates[0]`.
3. **Real interactability** (section 9).  `_usable()` reads geometry for the
   procedure control, so a zero-area clone that still reports
   `is_displayed() == True` is stepped over and the walk continues to a live
   sibling, instead of being handed back and failing at `send_keys`.
4. **One probe universe** (section 7).  `_PROBE_QUERIES` kept only the XPath
   strategies, so the compact probe answered questions about a strictly
   smaller candidate set than the resolver searched.  Entries are now
   `[kind, value]` and the probe evaluates CSS and XPath alike: one
   authoritative definition of "the current Procedure control".

### Why arbitration is free on the happy path

Asking the browser "which elements are the Speciality and Reason controls?"
costs round trips, and doing it on every resolution pushed a single item
from 78 to 85 DOM calls - straight through the 80-call budget.

The resolver instead **remembers the identity of each control it has already
driven** (`_resolved_identity`), so recognising a neighbour is a set lookup.
The browser is only asked when that free check still leaves more than one
candidate standing, which only happens on a re-render.  Procedure-exclusive
strategies keep first-match-wins and are not arbitrated at all.

| scenario | metric | before | after |
|---|---|---|---|
| single item (budget 80) | DOM calls | 78 | **78** |
| 27 codes | find_elements / execute_script | 1339 / 677 | **1339 / 677** |
| GP001 locked qty30 | find_elements / execute_script | 532 / 404 | **532 / 404** |
| all | fixed_sleep_ms | 0 | **0** |
| all | frame_discoveries | 1 | **1** |
| CN002 qty6 -> C001 qty2 | Plus clicks / rows | 6 / 6 | **8 / 8** |

That last row is the fix: before, C001 never ran at all.

### What was deliberately NOT changed

`orchestrator.py`, `controllers.py`, `txstate.py`, `session.py`, `tabs.py`,
`rules.py`, `parsing.py` and the UI are byte-identical.  The recovery stays
at exactly one attempt, `PENDING` preservation is untouched, Plus and the
dispatch ledger are untouched, and `TX-PROCEDURE-CONTROL-UNAVAILABLE`
remains distinct from `TX-PORTAL-CONTEXT-LOST`.

Ambiguity is reported as `AmbiguousControlException`, a **subclass** of
`ElementNotInteractableException`.  That was chosen over a new
`Diagnostic` member so every existing caller keeps treating it as an ELEMENT
fault with the frame and tab caches intact; introducing a new diagnostic
would have meant editing the recovery path this task forbids touching.

### Status

`tests/test_post_plus_procedure_control.py` is 31 tests covering A-L; **23
of them fail against the pre-fix engine**.  The eight that pass before and
after are the section 12 guarantees (frame cache preserved, one recovery,
`PENDING` preservation, locked-quantity integrity) - they are there to prove
the fix did not weaken them.

`LIVE_PORTAL_VERIFICATION = NOT_YET_VERIFIED`.  The root cause is proven
from the strategy registry and reproduced against the React double, not from
a captured live DOM: this environment is headless Linux with no portal
credentials, so sections 4, 5, 6 and 19 (live capture, live A-E snapshots,
live Windows runs) cannot be executed here and must be run by the operator.

## 9. Final report (task section 17)

| # | Item | Value |
|---|---|---|
| 1 | Baseline commit | `c3ccdf32e2170891fad9b150c850053461c85a25` |
| 2 | New commit | see `git log -1` on `arena/01a10125-update-cghs-enhancment-bot` |
| 3 | Root-cause bugs found | 14 automation (§3) + 1 fixed parser defect + 1 open parser finding (§6.0) |
| 4 | Files changed | 51 vs baseline (+12.3k / −74.2k lines) |
| 5 | Performance changes | §4.2 — one attach/discovery/tab scan per batch, cached frame + locator strategy, compact probes, targeted verification, event-driven waits |
| 6 | Before/after measured | §4.3 — 370× wall clock and 99.1 % fewer DOM calls at 27 items; flat per-item cost to 200 items |
| 7 | Test matrix | §7 — **235 tests**: 226 passed, 8 skipped (ENVIRONMENT_BLOCKED), 1 strict xfail (declared open finding). Green in the workspace **and** from the extracted ZIP. |
| 8 | Known blockers | real bill PDFs, live portal, Windows/EXE runtime |
| 9 | Artifact path | `CGHS_AUTOMATION_SPEED_HARDENED_BUILD_FINAL.zip` (repo root) |
| 10 | Artifact SHA-256 | recorded in `docs/perf/packaging_verification.json` for the build being shipped |
| 11 | Live portal verification | `NOT_YET_VERIFIED` |
| 12 | Windows runtime verification | `NOT_YET_VERIFIED` |

### Gate status

| Gate | Status | Basis |
|---|---|---|
| A — source / canonical ownership | `PASS` | AST proof no engine class or module-level function is defined twice; `app.py` defines none |
| B — correctness | `PASS` | unknown ⇒ `RECONCILIATION_REQUIRED`; exactly-one-Plus invariant held under 64 fuzz cases |
| C — performance | `PASS` | measured, equal-work enforced, reproducible via `tools/benchmark_hotpath.py` |
| D — tests | `PASS` | 235 tests, exit 0 in the workspace and from the extracted archive; 8 skipped are reported `ENVIRONMENT_BLOCKED`, 1 strict `xfail` is a declared open finding |
| E — packaging | `PASS` | ZIP reopened, extracted, hashed per file, test suite re-collected from the extract |

### Status vocabulary audit

Nothing unknown is reported as `PASS`. The three unverifiable areas are named
explicitly and carry `ENVIRONMENT_BLOCKED` or `NOT_YET_VERIFIED`; the open
parser finding carries `NOT_YET_VERIFIED` and an executable reproducer rather
than a silent code change.
