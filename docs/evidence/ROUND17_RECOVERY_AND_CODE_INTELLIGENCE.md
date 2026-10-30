# Round 17 — Code recovery, compound engine, Chrome attach diagnosis

Baseline `abfecfd` (tag `round16-stability-upgrade`). Full traces:
`docs/evidence/round17_code_traces.txt`.

---

## 0. Honesty statement

| Item | Status |
|---|---|
| "The latest update introduced regressions" | **Partly NOT REPRODUCED.** Two of the reported symptoms were real and are fixed. Others I could not reproduce and did not "fix" speculatively — see §6. |
| UI buttons not responding | **NOT REPRODUCED — PyQt5 is not installable in this Linux sandbox.** I audited the bindings statically (all present and correctly wired, worker→UI via `pyqtSignal`). I did **not** change UI code. |
| Chrome attach slowness | **Root cause identified and fixed for the dead-endpoint case** (the dominant one). I could not run real Chrome here, so the healthy-path timing claim is unverified. |
| Live portal | `LIVE_PORTAL = NOT_VERIFIED`. No authorised live run. |
| Windows EXE | **NOT BUILT** — Linux, no PyInstaller/PyQt5. Not faked. |
| Scanned-PDF / OCR pass (Phase 3) | **NOT IMPLEMENTED.** See §6 — adding an OCR dependency was out of scope for an evidence-driven round and no scanned bill exists to prove it against. |

**Three rules you locked in earlier rounds were reversed in this round. Each was
reversed only after I showed you the evidence and you explicitly authorised it.**

---

## 1. The headline defect: ₹70,200 of ventilator charges was being dropped

Bill 39538 contains **26 rows** reading `VENTILATOR CHARGES FULL DAY` at ₹2,700,
which the PDF wraps across three lines as:

```
CGHS-C
C003-20
25
```

i.e. `CGHS-CC003-2025`. The parser read this as the raw alias `C003`, which sat in
a hard-coded blacklist:

```python
_NEVER_RESOLVED_ALIASES = frozenset({"C003"})
```

whose stated reason was *"inventing CC003 is forbidden — a code nobody ever
defined."* **That reason is now factually false.** The active master registry
defines `CC003` at sr_no 1146 — `Ventilator charges (Per day)` — and the row's
amount (₹2,700) equals the registry rate exactly.

### Why reversing it is safe now, and was not then

The blacklist was written when `VALID_CODES` was **wildcard-generated** (`CC001…CC100`),
so *every* invented `CCxxx` validated. That is no longer true, and I verified it
before changing anything:

```
VALID_CODES == set(CGHS_CODE_REGISTRY)  ->  True
len(VALID_CODES) == 1998
"CC100" in VALID_CODES                  ->  False
CC family size                          ->  14 real codes
```

Registry membership is now real evidence. A test pins this precondition
(`test_valid_codes_carry_no_wildcards_the_precondition_for_this_round`).

### The fix — the existing mechanism, not a new one

C003 moved **out of the blacklist and into `LOCKED_CONTEXT_MAPPINGS`**, the
same-row-evidence table that already governs C004/C008/C010/C011/C012/C014:

```python
"C003": ("CC003", re.compile(r'VENTILATOR'), "Ventilator charges (Per day)"),
```

So CC003 resolves **only** with ventilator evidence in the same row. A
`FRESH FROZEN PLASMA … CGHS-C C003` row still goes to REVIEW — proven by test.
No new parser, no blanket acceptance, no per-code blacklist.

---

## 2. NI/NU and the hand-maintained family list

`CGHS-NI 001` (3 × `EEG ROUTINE` @ ₹900, registry `NI001` = *Electroencephalogram
(EEG)* @ 900) could never resolve — not because a rule rejected it, but because
`LOCKED_NUMERIC_CATEGORIES` was a **hand-written subset of 10 families** that simply
omitted `NI` and `NU`. Silent under-recognition by omission.

It is now **derived from the registry**:

```python
LOCKED_NUMERIC_CATEGORIES = frozenset(CGHS_CODE_FAMILIES | {"WC", "ST", "OT"})
```

This can never make an unregistered code executable — composition is still gated on
`candidate in VALID_CODES` — it can only stop a **real** code from being lost.
`CGHS-NI 999` still reviews (tested).

This is what made all five of your named codes resolve. None of them is hard-coded;
all five resolve dynamically through the registry (traces in §A of the trace file).

---

## 3. Compound engine — prefix carry no longer depends on line wrapping

The project already carried the family prefix for the real bill's wrapped
`CGHS-L B042+0 43+044` → `LB042`/`LB043`/`LB044`. But the identical expression
written **without** the wrap resolved differently:

| input | before | after |
|---|---|---|
| `CGHS-L B042+0 43+044- 2025` | LB042, LB043, LB044 | unchanged |
| `CGHS-LB042+043+044-2025` | LB042, **REVIEW, REVIEW** | LB042, LB043, LB044 |
| `CGHS-LB269+270-2025` | LB269, **REVIEW** | LB269, LB270 |
| `CGHS-LB068+076+126-2025` | LB068, **REVIEW, REVIEW** | LB068, LB076, LB126 |
| `CGHS-LB269+LB270-2025` | both | unchanged |
| `CGHS-LB067+LB075+LB126-2025` | all three | unchanged |
| `CGHS-LB269+998-2025` | LB269 + review | **unchanged — LB998 still reviews** |

The special case that parked bare components at REVIEW was removed, so they fall
through to the **same** `_resolve_component` path the first component already uses —
one resolution path, still registry-gated. Component count is not hard-coded (2, 3,
and 4+ all work), and `LB` is not hard-coded as the only family.

---

## 4. Phase 1 — why Chrome attachment was slow

`PortalSession.attach` handed the debugger address **straight to
`webdriver.Chrome()`**, with no check that anything was listening. When Chrome is not
in debugging mode, Selenium resolves a driver binary and runs its own connection and
timeout logic before failing with a generic `WebDriverException` — and `attach`
repeated that **3 times with backoff**. The operator paid that entire cost, three
times, to be told nothing actionable.

**Fix:** `probe_debugger_endpoint()` — one local socket round trip to
`/json/version` before Selenium is involved. A dead endpoint now fails in
milliseconds with an accurate message; a healthy one costs low single-digit
milliseconds and also reports the browser version for the version-match check.
It never starts a browser — the operator's manual debugging workflow is unchanged.

Combined with round 16's `[SESSION-TIMING]` counters (`endpoint_discovery_ms`,
`cdp_connect_ms`, `session_validate_ms`, `context_ready_ms`), a slow attach can now
be attributed to a stage instead of guessed at.

**Honest limit:** this fixes the *dead/unreachable endpoint* case. If your slowness
happens with a healthy endpoint, the new stage timings will show which stage owns it —
I could not reproduce that case without real Chrome.

---

## 5. Results

### Bill 39538 — recovered, with zero drift

```
CC003 = 26   Ventilator charges (Per day) @2700   (was: dropped to REVIEW)
NI001 =  3   Electroencephalogram (EEG)   @900    (was: dropped to REVIEW)

occurrences     342 -> 371      distinct codes 55 -> 57
rows to review  117 ->  88
```

All locked invariants unchanged: `CN002=87 CC001=29 CC002=108 BC002=3 PT004=19
PT005=53 CI003=3 CI001=4`.

### Regression suite

**770 collected · 762 passed · 0 failed · 8 skipped** (baseline 752 + 18 new; the 8 skips are the same pre-existing ones as at baseline).
Environment: PyMuPDF 1.28.2, selenium 4.50.0, pytest 9.1.1. Node regression: N/A
(Python-only project).

Nine previously-passing tests failed after the change. I examined each rather than
rewriting them to go green:

- **7 encoded the three rules you authorised reversing** — updated, with the
  no-evidence and unregistered-component cases **kept** so every safety property is
  still proven.
- **2 were stale**: `test_an_unregistered_token_is_preserved_for_review_not_manufactured`
  was parametrised on `BC002`/`CT001`, which the *synthetic* registry lacked but the
  1998-record master list **defines** (Packed Red Cell @1550, Single drug Chemotherapy
  @1530). Asserting those must be discarded would mean throwing away real CGHS codes.
  I re-pointed the safety test at genuinely absent tokens (`BC099`, `CT099`, `NI999`,
  `LB998`) and added a positive test for the registered pair — the proof is preserved,
  not deleted.

### Performance (same bill, same host, interleaved, 15 runs)

| | base `abfecfd` | round 17 |
|---|---|---|
| `parse_document` min (ms) | 121.52 / 124.51 | 122.62 / 122.57 |
| `parse_document` median (ms) | 131.56 / 143.46 | 158.33 / 136.37 |

No measurable change; differences are inside the baseline's own run-to-run spread.
The extra 29 occurrences cost nothing because they reuse the existing single parse.

---

## 6. What I did NOT do, and why

1. **No UI changes.** PyQt5 is not installable here, so I could not reproduce
   "buttons not responding". Static audit found all controls bound
   (`upload_btn`/`start_btn`/`stop_btn` → slots), worker on `QThread`, UI updates via
   `pyqtSignal` — no thread-unsafe UI calls. Changing working UI code on an
   unreproduced report would risk breaking a console that works.
   **One real smell worth your attention:** `start_batch()` guards double-start only
   by disabling the button, and overwrites `self.thread` each run. If your "doesn't
   reliably start" is intermittent, that is where I would instrument next — but I
   will not patch it blind.
2. **No OCR pass.** Adding an OCR engine is a new dependency and a major version
   surface, and there is no scanned bill in the repo to prove it against. Reported as
   a gap rather than shipped unverified.
3. **No speculative "exhaustive rescan" rewrite.** The coverage audit (§7) showed the
   existing pipeline already sees every page; the misses were **resolution** bugs, not
   coverage bugs. Rebuilding extraction would have been churn.

---

## 7. Coverage audit method (Phase 3/7)

I de-wrapped the entire PDF (`\s+` removed), extracted every `CGHS-XXNNN` token, and
diffed that set against what the engine resolved:

```
raw de-wrapped CGHS tokens in the PDF : 51 distinct
engine resolved                        : 57 distinct
valid registry codes present but NOT extracted : 2  -> CC003, NI001
tokens that look like codes but are not in the registry : 0
```

After the fix that gap is **zero**. This is the check to re-run on any new bill.

---

## 8. Modified files

| file | why |
|---|---|
| `cghs/rules.py` | C003 blacklist → evidence rule; family list derived from registry; compound prefix carry |
| `cghs/session.py` | `probe_debugger_endpoint()` + pre-flight check in `attach` |
| `tests/test_cghs_code_rules.py` | +11 new; 7 authorised reversals updated; 2 stale tests re-pointed |
| `tests/test_real_format_bills.py` | 39538 invariant now asserts the recovered CC003=26 / NI001=3 |
| `tests/test_tab_frame_safety.py` | +3 endpoint-probe tests |
| `docs/evidence/round17_code_traces.txt` | generated traces for all five codes + compounds |

---

## 9. Build / rollback

**Build (Windows):** `pip install -r requirements.txt` then `BUILD_WINDOWS.cmd`.
Verified statically: entry point `app.py` → `CGHSParsingEngine` and `BatchRunner` →
`ProcedureSelector`; spec collects all `cghs` submodules; no legacy duplicates.
**Rollback:** `git checkout abfecfd` (tag `round16-stability-upgrade`). The existing
production EXE is untouched.

## 10. Known limitations

1. `LIVE_PORTAL = NOT_VERIFIED`; no live portal run.
2. UI defect unreproduced (no PyQt5); UI code unchanged.
3. Healthy-path Chrome attach timing unmeasured (no real Chrome).
4. No OCR / scanned-PDF support.
5. `WINDOWS_RUNTIME` unverified; PyQt5 and pyinstaller remain floors, not pins.
6. The five named codes were verified against the **registry** and against
   realistic rows; only CC003 occurs in the one real bill available (39538).
   NU004/NI007/CI016/NU001 are **not present in that bill** and could not be
   verified against real-bill text.
