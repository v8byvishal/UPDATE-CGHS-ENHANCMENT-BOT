# Round 16 — Stability Upgrade: CDP/procedure selection, compound codes, CI003

**Scope:** upgrade and harden the existing application. No rewrite, no redesign, no
removed functionality. Baseline `962b37b` (tag `audit-r15-defect-fix`).

---

## 0. Honesty statement — read this first

| Claim | Status |
|---|---|
| The project ZIP and console screenshot you referred to | **NOT PRESENT IN THE WORKSPACE.** A search of the whole sandbox found no `.zip`, `.png`, `.jpg` or `.webp`. This round worked from the **repository** (the source of truth) plus the console text quoted in your prompt. |
| Real CGHS portal behaviour | **`LIVE_PORTAL = NOT_VERIFIED`.** No authorised live run was performed. Every portal result below comes from a deterministic React-Select double. |
| Windows EXE | **NOT BUILT.** The environment is Linux with no PyInstaller and no PyQt5. Source + build instructions + verification results are shipped instead. Nothing was faked. |
| "This prevents all future selection failures" | **Not claimed.** The fix addresses one *proven* transient mechanism. |

---

## 1. Priority One — Chrome/CDP + procedure selection

### 1.1 Root cause — **PROVEN AND REPRODUCED VERBATIM**

`cghs/controllers.py::_await_exact_option` made **two independent observations of a
mutating DOM with no re-check between them**:

1. a readiness probe — one JS round trip that *counts* visible options;
2. a **separate** Selenium `locate_all("DROPDOWN_OPTIONS")` plus a per-element `.text`
   read.

When React re-renders in the gap between (1) and (2) — debounced/async option loading —
every node captured by (2) is detached. `texts` accumulates only non-empty strings and
`StaleElementReferenceException` was silently `continue`d, so the function reported an
empty menu for a menu that *was* populated:

```
Exact code match [CN002] not found among 0 options: []
```

`execute()` called it exactly **once**, with no retry, so the exception propagated and the
bill was marked PARTIAL. That is the incident in your console.

**Reproduction** (`/home/user/cghs_evidence/repro_r16_dropdown.py`, outside the repo):

| case | pre-fix | post-fix |
|---|---|---|
| A healthy portal | `True`, React holds `CN002` | `True`, React holds `CN002` |
| B re-render between probe and read | **`NoSuchElementException: Exact code match [CN002] not found among 0 options: []`**, React holds `''` | `True`, React holds `CN002`, **1 re-query** |
| C code genuinely absent | `Dropdown never opened for [CN002]` | unchanged |

Cases B and C already produced **different** messages. That distinction is the existing
taxonomy separating *transient* from *permanent*, and the fix preserves it.

### 1.2 What was NOT the cause

Endpoint discovery, CDP attach, session validation, tab/patient context and frame
resolution were all traced and were **working correctly** in the reproduced incident —
the console itself confirms patient and treatment-plan context were identified. They were
instrumented (§1.4), not changed.

### 1.3 The fix — bounded, failure-only recovery

In `_await_exact_option`, the single read was extracted into `_read_options_once`, which
now also reports `stale_seen` (a node vanished *while being read*). Recovery runs **only**
on the proven transient signature — *nothing readable at all*, or *a node went stale
mid-read*:

- **bounded twice over** — by the caller's timeout **and** by a hard cap of **3** re-reads;
- **context is reconfirmed first** — `verify_identity_unchanged()` must pass before the
  portal is re-read; if it cannot, the item stops with `PortalContextLost` and nothing is
  selected;
- **stale references are dropped** — `resolver.invalidate("DROPDOWN_OPTIONS")` re-queries
  the live DOM instead of reusing captured nodes;
- **no sleeps** — it waits on the *same* existing readiness condition;
- **exactness is untouched** — no lookalike, no first match, no partial match, ever;
- **a readable menu that simply lacks the code still fails on the first attempt**, so the
  "code not offered" outcome keeps its original speed;
- selection is still verified before quantity entry by the pre-existing `_verify_selection`;
- on final failure the item stops with a specific logged reason — the rest of the bill is
  untouched.

### 1.4 Instrumentation added

`PerfCounters`: `option_requeries`, `option_ready_ms`, `endpoint_discovery_ms`,
`cdp_connect_ms`, `session_validate_ms`, `context_ready_ms`.
`cghs/session.py` emits `[SESSION-TIMING]` for endpoint discovery, CDP connect, session
validation, context readiness and start→first-meaningful-operation. These only read the
clock around work that already happens — **no extra probe, no extra round trip**. Only a
*real* discovery is timed; the cached fast path is not, or the number would be meaningless.
No patient data, credentials or tokens are logged.

---

## 2. Priority Two — compound codes (`LB269+LB270`)

### Result: **NO PRODUCTION CHANGE — the existing implementation is already correct.**

Investigated before changing, as instructed. Verified against the live 1998-record
registry:

| input | result |
|---|---|
| `CGHS-LB269+LB270-2025` | `LB269` EXECUTABLE + `LB270` EXECUTABLE |
| `CGHS-L B269+LB270` | both EXECUTABLE |
| `CGHS-LB 269 + LB270-2025` (whitespace) | both EXECUTABLE |
| `CGHS-LB269+270-2025` (**incomplete**) | `LB269` EXECUTABLE + **`REVIEW_REQUIRED`** — no prefix invented |

The resolver already emits per-component `raw_expression`, `parent_expression`,
`component_index`, `component_count` and `source_row_text`, so both codes are represented
in the item model with their original source text preserved, and each keeps its own
registry rate and description (`LB269` Dengue NS1 Ag @360, `LB270` Dengue IgM and IgG @720).

The only genuine compound cell in evidence bill 39538 is
`CGHS-L B042+0 43+044- 2025` → `LB042` + `LB043` + `LB044`, all EXECUTABLE.

**9 lock-in regression tests** were added so a future edit cannot silently regress this.
No competing parser was introduced. Per the standing rule, no production code was changed
for an unproven defect.

---

## 3. Priority Three — CI003 Holter analysis per day

### 3.1 Defect — **PROVEN ON THE REAL BILL**

Evidence bill 39538, page 73, section `NON INVASIVE PROCEDURE`, the only CI003 occurrence:

```
6,750.00  0  1 ( ß )  HOLTER MONITORING (SP3) 72  HOURS STUDY (4220174 ) 1.00  3099477  6,750.00 CGHS-CI 003-202 5
```

Production emitted **`CI003 qty = 1`**. Master registry record:
`CGHSRecord(sr_no=1188, code='CI003', description='Holter analysis per day',
nabh_rate='2250', speciality='Cardiology Investigation')` — the directive's ₹2,250
**confirmed in the active config, not assumed**. ₹6,750 ÷ ₹2,250 = **3**.

### 3.2 The rule

`derive_amount_based_quantity()` in `cghs/rules.py`, gated by
`AMOUNT_DERIVED_QUANTITY_CODES = {"CI003"}`:

- the **rate** is read from the master registry (`nabh_rate`) — never hard-coded;
- the **amount** is the third column of the `qty ref amount` triple that
  `parse_row_quantity` already anchors on. That triple carries the line's **own reference
  number**, so it is item-level by construction — a package total, a department subtotal
  or a grand total cannot occupy it;
- `Decimal` throughout; commas, `₹` and spacing normalised;
- ₹2,250→1, ₹4,500→2, ₹6,750→3, ₹9,000→4.

**Rejects to review** (and **never** falls back to the printed quantity) when the amount is
missing, zero, negative, unreadable, ambiguous (two conflicting money values in one row),
not an exact whole multiple, or the derived quantity is out of range. An unverifiable rate
leaves existing behaviour untouched rather than guessing.

### 3.3 Blast radius — measured, not asserted

After the change, on the real bill: **`CI003 = 3`** (was 1). Control code **`CI001` = 4,
unchanged**. All 39538 invariants hold exactly:

```
CN002=87  CC001=29  CC002=108  BC002=3  PT004=19  PT005=53
```

The rule was placed **after** the baseline block in `rules.py` so that **no business-rule
function moved out of `app.py` was modified** — the byte-level parity guard
(`test_rules_module_is_a_verbatim_copy_of_the_baseline_block`) passes with **no new
declared deviation**. The guard was not weakened.

---

## 4. Modified-file inventory

| file | change | why |
|---|---|---|
| `cghs/controllers.py` | split `_read_options_once`; bounded failure-only recovery in `_await_exact_option` | P1 root cause |
| `cghs/telemetry.py` | 6 new counters | P1 instrumentation |
| `cghs/session.py` | `[SESSION-TIMING]` stage timings | P1 instrumentation |
| `cghs/rules.py` | `AMOUNT_DERIVED_QUANTITY_CODES`, `derive_amount_based_quantity`, `_row_item_amount`; header corrected | P3 |
| `cghs/parsing.py` | derived quantity consulted for amount-driven codes; unsafe rows → review | P3 |
| `tests/support/react_select_dom.py` | `detach_options_once_after_probe` (test-support only) | models the re-render race |
| `tests/test_react_select_procedure.py` | +8 tests | P1 |
| `tests/test_locked_quantity_engine.py` | +12 tests | P3 |
| `tests/test_cghs_code_rules.py` | +9 tests | P2 lock-in |
| `docs/perf/round16_dropdown_recovery.json` | new | measurements |

No UI, theme, layout, `tabs.py` or `app.py` changes. Nothing removed.

---

## 5. Regression results

**752 collected · 744 passed · 0 failed · 8 skipped** (baseline 723 + 29 new; the 8 skips are the same pre-existing ones as at baseline).
Environment: PyMuPDF 1.28.2, selenium 4.50.0, pytest 9.1.1 — the exact manifest pins.
Node regression: **N/A**, this is a Python-only project (no `package.json`).

The suite was **also run from the extracted ZIP** (`/tmp/zipcheck`): same result, and all
three fixes verified present in the artifact.

> A mid-round failure of `test_the_manifest_pins_the_versions_the_tests_actually_ran_against`
> was caused by the sandbox reinstalling **PyMuPDF 2.0** over the pinned 1.28.2. Per §2
> (no unproven major version bumps) the **environment was restored to the pin**, rather
> than rewriting the manifest to match a drifted environment.

Every new test for a *fixed* defect was **confirmed failing before the fix**. The 9 compound-code tests are lock-in tests for behaviour that was already correct (P2 needed no fix), and are honestly labelled as such rather than presented as defect proofs.
One test (`test_a_readable_menu_without_the_code_fails_immediately_without_requery`) was
written on a wrong premise — React-Select *filters by the typed token*, so an absent code
renders zero options and is the separate "never opened" case. The **test** was corrected to
use lookalikes that survive the filter; the production code was not bent to fit it.

---

## 6. Performance comparison — measured

Method: same bill, same host, same pinned environment; the pre-change tree is a
`git archive` of `962b37b` measured by the **same script**, BASE/AFTER interleaved twice.

| metric | base `962b37b` | round 16 | verdict |
|---|---|---|---|
| `parse_document(39538)` min / median ms | 122.35 / 146.43 · 122.65 / 123.14 | 123.41 / 144.35 · 121.32 / 146.00 | no measurable change |
| React-Select healthy path | 1.61 / 1.40 ms | 1.45 / 1.60 ms | noise |
| **`find_elements_calls` (healthy)** | **18** | **18** | **identical** |
| **`element_reads` (healthy)** | **14** | **14** | **identical** |
| `option_requeries` (healthy) | n/a | **0** | fast path untouched |

The identical round-trip counts are the strongest available evidence that no extra polling
was introduced: the recovery path is not entered on a healthy portal. Recovery costs **1**
re-query on the reproduced failure — a case that previously ended as PARTIAL.

These absolute numbers are **not** comparable with round 15's (51.33 ms); that was a
different host. Only the interleaved pairs above are meaningful.

---

## 7. Windows EXE / release

**Not built — and not faked.** Linux, no PyInstaller, no PyQt5.

Verified statically instead:
- entry point `app.py` (per `CGHS_Enhancement_Bot.spec` `Analysis(['app.py'])`) imports
  `CGHSParsingEngine`, `PortalSession` and `BatchRunner`;
- `BatchRunner` constructs `ProcedureSelector` — so the production entry point **does**
  reach both fixes;
- **zero** legacy duplicates of the fixed functions in `app.py`;
- the spec still collects all `cghs` submodules, so the updated modules ship;
- nothing was removed from the distribution.

**Build:** on a Windows host, `pip install -r requirements.txt` then `BUILD_WINDOWS.cmd`.
**Rollback:** `git checkout 962b37b` (tag `audit-r15-defect-fix`); the previous EXE is not
overwritten — keep it alongside and swap back if needed.

---

## 8. Known limitations

1. `LIVE_PORTAL = NOT_VERIFIED`. The fix is proven against a deterministic React double,
   not the real portal. **It is not production-verified.**
2. The recovery addresses one proven transient mechanism. Other causes of an empty option
   list (network stalls, portal-side errors) will still stop the item — by design.
3. The cap of 3 re-reads is a judgement bound, not a measured optimum.
4. No real Windows runtime verification; `WINDOWS_RUNTIME` remains unverified.
5. PyQt5 and pyinstaller remain `NOT_PROVEN` floors, not pins.
6. The uploaded ZIP/screenshot were never available; conclusions rest on the repository.
