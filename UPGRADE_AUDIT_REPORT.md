# UPGRADE AUDIT REPORT — Round 18

**Application:** CGHS Automation Console — Vishal Singh Chauhan
**Executable:** `CGHS_Enhancement_Bot`
**Identity:** UNCHANGED. The app was **not** renamed. "GovClaim Studio" appears
nowhere in the source; a regression test enforces this.

| Item | Value |
|---|---|
| Baseline / rollback commit | **`2674aa4`** (tag `round17-code-recovery`) |
| Branch | `arena/01a10125-update-cghs-enhancment-bot` |
| Test suite | **781 collected · 770 passed · 11 skipped · 0 failed** |
| Parser output vs baseline | **IDENTICAL** (sha256 `fefb2311…b8019`, 57 items / 371 occurrences) |
| `LIVE_PORTAL` | **NOT_VERIFIED** — no authorised live portal run |
| Windows EXE build | **BLOCKED** — Linux container, no PyInstaller, no PyQt5 |
| UI runtime verification | **BLOCKED** — PyQt5 not installable here (static verification only) |

---

## PHASE 0 — Source audit, findings verified against real code

Your six suspected findings were each checked against the actual source
rather than assumed. Five confirmed, one partially confirmed.

| # | Your finding | Verdict | Evidence in source |
|---|---|---|---|
| A | Launcher uses a debug port with no dedicated profile | **CONFIRMED** | `BUILD_WINDOWS.cmd:146` told the operator to run Chrome with only a debugging-port flag |
| B | Chrome 136+ needs a non-default profile for remote debugging | **CONFIRMED** (consequence of A) | On current Chrome the port silently never opens |
| C | A timer flips status to ONLINE without checking anything | **CONFIRMED verbatim** | `app.py:901` — `QTimer.singleShot(800, … "CHROME/CDP: ● ONLINE")` |
| D | Preflight exists but Selenium still does implicit work | **CONFIRMED** | `probe_debugger_endpoint()` ran, then `webdriver.Chrome(options=…)` still resolved a driver |
| E | Attach retries don't separate temporary from permanent faults | **CONFIRMED** | `except Exception` → sleep → retry, for every failure alike |
| F | Disconnect recovery / driver cleanup need work | **CONFIRMED — worst defect found** | `close()` only wrote a log line; it never stopped the driver service |

---

## FIXES APPLIED

Every entry: defect → root cause → file → correction → test → result.

### FIX 1 — Chrome 136+ cannot open a debugging port (connection never succeeds)
* **Defect:** operator followed `BUILD_WINDOWS.cmd`, Chrome started, bot waited forever on an endpoint that was never opened.
* **Root cause:** Chrome 136+ refuses remote debugging on the **default** user-data directory. The instructions supplied no dedicated profile.
* **Correction:** new **`START_CGHS_CHROME.cmd`** — auto-detects Chrome in all three standard install paths plus the App Paths registry key; starts it on a **persistent dedicated profile** at `%LOCALAPPDATA%\CGHS-Enhancement-Bot\ChromeProfile`; binds the port to **127.0.0.1 only**; detects an already-running automation profile and reports what is holding the port; detects port conflicts and offers an alternate port; confirms the endpoint actually answers before declaring READY. `BUILD_WINDOWS.cmd` now points at the launcher and explains why the old one-liner no longer works.
* **Safety:** never touches the operator's normal profile, copies nothing, automates no login, OTP, CAPTCHA or MFA. The operator signs in manually.
* **Result:** **PASS** (static/structural). Live Windows execution **NOT VERIFIED** — no Windows host.

### FIX 2 — Fabricated ONLINE status  *(§3.4)*
* **Defect:** the console displayed `CHROME/CDP: ● ONLINE` 800 ms after layout **whether or not Chrome existed**.
* **Root cause:** `app.py:901`, a literal set on a timer — a cosmetic placeholder reporting a state nothing had checked.
* **Correction:** removed. Added `ChromeStatusProbe(QThread)` which calls the **real** `probe_debugger_endpoint()` and emits `CONNECTED` / `NOT STARTED` / `CHECK FAILED` with the live detail string as a tooltip. Polling is **paused for the entire duration of a batch** (`set_batch_running(True)` on start, `False` on finish) and stopped/joined in `closeEvent`.
* **Speed guarantee:** the probe is **outside the per-item hot path** — no per-code connection check, no per-item heartbeat, no extra Selenium or DOM call.
* **Test:** `test_the_ui_never_fakes_an_online_status_on_a_timer` — **failed before the fix**, passes now.

### FIX 3 — Any HTTP service on the port was accepted as Chrome  *(§3.2)*
* **Defect:** the probe returned success for *any* response body, so an unrelated service on port 9222 was reported as a working Chrome, sending the operator to debug the wrong thing.
* **Root cause:** fallback branch `if body: return True, "DevTools endpoint responded"`.
* **Correction:** acceptance now requires a parsable `"Browser"` field in `/json/version` **and** a value matching Chrome/Chromium/Edge. Empty, corrupt and non-DevTools responses are each rejected with a distinct, actionable message.
* **Test:** `test_a_non_chrome_service_on_the_port_is_not_reported_as_chrome` runs a real local HTTP server that is not Chrome — **failed before the fix**, passes now.

### FIX 4 — Permanent faults retried until the budget ran out  *(§3.3, §3.5)*
* **Defect:** a ChromeDriver/Chrome version mismatch was retried with backoff, multiplying the operator's wait before reporting the same unavoidable error.
* **Root cause:** one blanket `except Exception` treating every failure as transient.
* **Correction:** added `is_transient_attach_error()`. Recognised-permanent signatures (driver/browser version mismatch, missing binary, driver not on PATH) **break immediately** with an explicit log. Anything unrecognised is still treated as transient and keeps its retries — deliberately conservative.
* **Test:** `test_a_permanent_attach_failure_is_not_retried` — **failed before the fix**, passes now.

### FIX 5 — Orphan `chromedriver.exe` on every batch  *(§3.6)*
* **Defect:** each connect/disconnect cycle leaked a `chromedriver.exe`; across repeated batches these accumulated for the life of the session.
* **Root cause:** `close()` contained **only a log statement**. It correctly avoided `quit()` (which would close the operator's own Chrome on a debugger-address session) but then stopped the driver service never at all.
* **Correction:** `close()` now stops the **driver service** — terminating the chromedriver child process — while still **never** calling `quit()`, so the operator's Chrome window stays open. Guarded by a `_closed` flag (idempotent) and never raises, so shutdown cannot fail.
* **Test:** `test_close_stops_the_driver_service_without_quitting_the_browser` asserts the service stopped **once**, `quit()` called **zero** times, and a second `close()` is a no-op — **failed before the fix**, passes now.

---

## PHASE 4 — Merged CGHS code investigation

**Outcome: already correct — no fabricated "fix" applied.**

The compound pipeline was traced end to end (PDF text → blocks → row
clustering → `CGHS` marker detection → compound detection → component
separation → registry validation → queue). Every form you listed was run
against the live resolver:

| Expression | Result today |
|---|---|
| `CGHS-LB269+LB270-2025` | LB269 + LB270 — both EXECUTABLE |
| `CGHS-LB042+043+044-2025` | LB042 + LB043 + LB044 — all EXECUTABLE |
| `CGHS-LB269+270-2025` | LB269 + LB270 — both EXECUTABLE |
| `CGHS-LB269 + LB270-2025` (spaces) | LB269 + LB270 — both EXECUTABLE |
| `CGHS-L-B042+043+044-2025` (hyphen) | LB042 + LB043 + LB044 — all EXECUTABLE |
| `CGHS-LB269+998` | LB269 EXECUTABLE + **REVIEW_REQUIRED** (LB998 is not in the registry — correctly not invented) |

Round 17's registry-driven prefix-carry already handles abbreviated
components, spacing and hyphenation. **Bare `LB269+LB270` with no `CGHS`
marker deliberately does not resolve** — `rules.py:874` requires the marker,
which is a false-positive guard, not a defect.

**Status: NOT REPRODUCIBLE.** No supplied form loses a registry-valid code.
Per your instruction, no correction was fabricated. If a real bill row is
still dropping a code, send that row (anonymised) and it will be traced
against this pipeline.

---

## PHASE 6 — Professional desktop UI redesign (presentation only)

**Exact palette, used as specified:**

| Role | Colour |
|---|---|
| Background, surfaces, panels, workspace | **ICE SILVER `#DDE3E8`** |
| Sidebar, nav, brand, primary buttons | **DEEP AUBERGINE `#3A0F34`** |
| Active selection, highlight, focus, emphasis | **MARIGOLD `#EAAA00`** |

* `ui_theme.py` rewritten as a complete **centralised design system**: layered surfaces, borders, readable dark text tones, muted semantic status colours, typography scale, radius scale, and one global stylesheet covering buttons, inputs, combos, lists, tables, headers, tabs, progress, scrollbars, checkboxes, tooltips, menus and splitters.
* **All 197 hardcoded neon literals removed from `app.py`** (35 distinct → 18, every one from the approved system). The old neon-on-black console palette is gone.
* **Legacy `NEON_*` names kept as aliases** pointing at the new palette, so no widget reference breaks.
* `ui_theme.py` **never imports `cghs`** — enforced by test, so a styling change can never alter extraction or timing.
* **Nothing removed:** every control (`start_btn`, `stop_btn`, `upload_btn`, `queue_table`, `results_table`, `log_box`, all status labels) and every handler (`start_batch`, `handle_dropped_files`, `batch_finished`, `closeEvent`) is asserted present by test.

**Honest limitation:** PyQt5 cannot be installed in this Linux container, so the
redesign is **statically verified only**. It has **not** been rendered,
screenshotted, or tested at 100% / 125% / 150% Windows scaling.
Scaling verification is **NOT VERIFIED**.

---

## PHASE 7 — Regression and performance

Parser output compared against baseline `2674aa4` on the real bill `39538.pdf`:

```
BASE 2674aa4   sha256 fefb2311374b079213a5e98c0c98757267c9a44cad9fae47f3d160b4147b8019   57 items / 371 occurrences
HEAD round18   sha256 fefb2311374b079213a5e98c0c98757267c9a44cad9fae47f3d160b4147b8019   57 items / 371 occurrences
```

**Byte-identical.** Timing (12 iterations, interleaved ×2):
base min 146.91 ms / median 155.53 ms · head min 137.57 ms / median 153.01 ms —
**no regression**; the difference sits inside normal spread.

No business logic was touched: no change to extraction, code identification,
enhancement rules, registry, speciality mapping, quantity selection,
procedure selection, network delay, DOM sequence, Plus dispatch, duplicate
protection, commit verification or batch order.

---

## STATUS SUMMARY — stated honestly

| Area | Status |
|---|---|
| Chrome 136+ dedicated-profile launcher | **PASS** (static) / live run NOT VERIFIED |
| Strict CDP endpoint identification | **PASS** (tested against a real non-Chrome server) |
| Honest connection status | **PASS** |
| Permanent-vs-transient classification | **PASS** |
| chromedriver lifecycle / no orphans | **PASS** (unit-verified) |
| Operator's Chrome never closed | **PASS** |
| Merged-code investigation | **NOT REPRODUCIBLE** — pipeline correct on all supplied forms |
| UI redesign, exact palette | **PASS** (static) |
| UI rendering & Windows scaling | **BLOCKED** — no PyQt5 |
| Windows EXE build | **BLOCKED** — no PyInstaller/Windows |
| Live portal | **NOT_VERIFIED** |
| Full regression | **PASS** — 770 passed, 0 failed |
| Performance | **PASS** — no regression, output identical |

## UNRESOLVED / NOT DONE
1. **Windows build & EXE packaging** — cannot run here.
2. **UI rendering and 100/125/150% scaling** — cannot run here.
3. **Live portal verification**, incl. real disconnect-recovery and duplicate-Plus behaviour against the real portal.
4. **§3.3's full 13-stage timing display** — session-layer stage timings exist and are logged, but surfacing all thirteen in the UI could not be validated without PyQt5; not claimed as delivered.
5. **§3.5 auto-reconnect** — the existing fail-closed protections (patient revalidation, duplicate detection, commit verification) remain untouched and intact; no new automatic reconnection was added, because it cannot be proven safe without a live portal. Fail-closed behaviour is preserved rather than traded for automatic recovery.

## ROLLBACK
`git reset --hard 2674aa4` (tag `round17-code-recovery`) restores the pre-round-18 state.
