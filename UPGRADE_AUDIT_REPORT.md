# UPGRADE AUDIT REPORT — Round 18

**Application:** CGHS Automation Console — Vishal Singh Chauhan
**Executable:** `CGHS_Enhancement_Bot`
**Identity:** UNCHANGED. The app was **not** renamed. "GovClaim Studio" appears
nowhere in the source; a regression test enforces this.

| Item | Value |
|---|---|
| Baseline / rollback commit | **`2674aa4`** (tag `round17-code-recovery`) |
| Branch | `arena/01a10125-update-cghs-enhancment-bot` |
| Test suite | **822 collected · 814 passed · 8 skipped · 0 failed** (see Corrective Round section) |
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
| Full regression | **PASS** — 814 passed, 0 failed |
| Performance | **PASS** — no regression, output identical |

## UNRESOLVED / NOT DONE
1. **Windows build & EXE packaging** — cannot run here.
2. **UI rendering and 100/125/150% scaling** — cannot run here.
3. **Live portal verification**, incl. real disconnect-recovery and duplicate-Plus behaviour against the real portal.
4. **§3.3's full 13-stage timing display** — session-layer stage timings exist and are logged, but surfacing all thirteen in the UI could not be validated without PyQt5; not claimed as delivered.
5. **§3.5 auto-reconnect** — the existing fail-closed protections (patient revalidation, duplicate detection, commit verification) remain untouched and intact; no new automatic reconnection was added, because it cannot be proven safe without a live portal. Fail-closed behaviour is preserved rather than traded for automatic recovery.

## ROLLBACK
`git reset --hard 2674aa4` (tag `round17-code-recovery`) restores the pre-round-18 state.

---
---

# ROUND 18 — CORRECTIVE UPDATE (FIX 1–8)

Eight independently reported defects. Each was **verified against real source
before being changed**, and each has regression tests that **failed before the
fix**.

## VERIFIED FINAL TEST RESULT

| | |
|---|---|
| Command | `python3 -m pytest -p no:cacheprovider -q` |
| Total collected | **822** |
| Passed | **814** |
| Failed | **0** |
| Skipped | **8** |
| New tests this round | **41** (`tests/test_round18_corrective.py`) |
| Packaged files | **66** |
| Archive SHA-256 | recorded in `CGHS_AUTOMATION_SPEED_HARDENED_BUILD_FINAL.zip.sha256` |

**On the previous discrepancy (FIX 8):** the earlier report stated
*770 passed / 11 skipped*. That was a snapshot taken **before the archive was
rebuilt** — three packaging tests skip when the ZIP is absent. The run after
the rebuild gave 773 passed / 8 skipped. The report quoted the earlier number
and the log the later one. The counts above are from a **complete rerun after
all corrective fixes**, and the "8 skipped" figure is the steady state with the
archive present. The 8 remaining skips are environment-gated (no PyQt5, no
Windows, no live portal) — **none is skipped to manufacture a pass**.

*A report that ships inside the archive cannot state that archive's own hash
(adding the hash changes the hash). The authoritative value is in the
`.sha256` sidecar, generated after the final build.*

## DEFECTS FIXED

### FIX 1 — ChromeStatusProbe was permanently killed by STOP AUTOMATION
* **Root cause:** `stop_batch()` called `chrome_probe.stop()` + `wait(2000)`. A stopped `QThread` cannot be revived by clearing a flag, so `batch_finished()`'s `set_batch_running(False)` resumed nothing. Chrome monitoring died for the rest of the session. *(This was my own error in the previous round — the shutdown block landed in `stop_batch()`, the first textual match, instead of `closeEvent()`.)*
* **Fix:** real **pause/resume**. `pause()`, `resume()`, `stop()` and both reads are guarded by a **`QMutex`**, because the flags are written from the UI thread and read from the worker. `start_batch()` pauses, `batch_finished()` resumes (on completion **and** cancellation), `stop_batch()` no longer touches the probe. `stop()` is now called in exactly one place: shutdown.
* **Speed:** while paused the probe issues **no socket call at all** — strictly outside the per-item hot path.
* **Tests:** 5, incl. a real lock-based pause/resume round trip.

### FIX 2 — Unsafe shutdown
* **Root cause:** `closeEvent()` waited 3000 ms for the worker, then accepted unconditionally; it never stopped the probe. A running `QThread` could be destroyed.
* **Fix:** ordered shutdown — latch `_shutting_down` (so `start_batch()` refuses), request graceful worker cancellation, join with a **15 s** budget, let `PortalSession.close()` stop the driver service, then stop and join the probe, and only then accept. If the worker is still busy the close is **ignored rather than forced** — a half-finished portal mutation is worse than a slow exit. **No `terminate()`, no `quit()`.**
* **Tests:** 5, incl. an **AST-based** check that no `terminate()`/`quit()` call exists (prose about avoiding them can't fool it).

### FIX 3 — Three surviving neon/black RGBA panels
* **Root cause:** the previous colour test only matched **hex**, so `rgba(...)` slipped through at lines 618, 797, 841 — exactly as reported.
* **Fix:** `rgba(10,12,18,0.88)` → `#FFFFFF`; `rgba(10,12,18,0.70)` → `#F4F7F9`; `rgba(0,229,255,0.14)` → `#FCF2D6`. The retired `NEON_*` aliases were also removed from widget code in favour of semantic tokens (`ui_theme.BRAND`, `ui_theme.STATUS_OK`). **Zero `rgb()`/`rgba()` and zero `NEON_*` remain in `app.py`.**
* **Hardened test:** now detects hex **and** rgb/rgba **and** old neon names **and** dark console panels **by luminance** (< 40), in any notation. The approved-dark set is limited to the aubergine brand family and readable text tones, so a genuine dark leftover cannot return.

### FIX 4 — Launcher and application could disagree on the port
* **Root cause:** the launcher accepted `9333` but Python had `127.0.0.1:9222` frozen in `DEFAULT_DEBUGGER_ADDRESS`.
* **Fix:** one configuration source. `resolve_debugger_port()` / `resolve_debugger_address()` in `cghs/session.py` read the shared **`CGHS_CHROME_PORT`**, default **9222**, validating 1–65535 and **raising on anything invalid** rather than silently falling back. `attach()` resolves it when no address is given. The launcher resolves the same variable in the same order and, when given a port argument, runs `setx CGHS_CHROME_PORT` so the application follows. `validate_debugger_address()` **rejects non-loopback hosts**.
* **Tests:** 6, incl. default, custom, six invalid values, loopback enforcement, and launcher/application agreement.

### FIX 5 — Launcher called a bare TCP connect "READY"
* **Root cause:** `ENDPOINT_UP` was set from a TCP connect alone; the `/json/version` call was cosmetic and its failure ignored.
* **Fix:** READY now requires TCP reachable **→ HTTP 200 → valid JSON → Chrome/Chromium identity → `webSocketDebuggerUrl` matching `wss?://`**. Success prints **`CHROME DEVTOOLS READY`**; failure prints **`CONNECTION FAILED`** and **`exit /b 1`**. Invalid port arguments exit 3.

### FIX 6 — Python CDP validation was a substring scan
* **Root cause:** `probe_debugger_endpoint()` regex-searched the raw response for `"Browser"`, so an HTTP 500 error page or truncated body containing that word was accepted.
* **Fix:** layered validation — loopback host, valid port, **HTTP status line parsed and required 2xx**, `Content-Length` completeness check, **strict `json.loads`**, dict shape, Chrome identity, and DevTools WebSocket metadata. Bounded connect/read timeouts via a single deadline. Each failure names the real problem.
* **Tests:** 8 against **real local HTTP servers** — valid Chrome, HTTP 500, malformed JSON, truncated body, non-Chrome JSON service, missing WebSocket metadata, wrong browser identity, dead port.

### FIX 7 — Dishonest status and a misleading timing label
* **Fix:** `CONNECTION_STAGES` makes the ladder explicit: CHROME PROCESS AVAILABLE → CDP ENDPOINT VERIFIED → SELENIUM ATTACHED → PORTAL TAB FOUND → PATIENT VERIFIED → TREATMENT PLAN READY. The probe can only prove the second rung, so the UI now shows **`CDP ENDPOINT VERIFIED`**, never `CONNECTED`. The `session_validate_ms` log label timed only Python wrapper construction — the log now reads **`wrapper_init=`**. No stage timing is fabricated; only measured values are shown.
* A test asserts no probe/port call was added to `process_item()` (the hot path).

## PRESERVATION CHECK — all protected files byte-identical to `2a24de4`

```
UNCHANGED  cghs/parsing.py      UNCHANGED  cghs/orchestrator.py
UNCHANGED  cghs/rules.py        UNCHANGED  cghs/dom.py
UNCHANGED  cghs/locators.py     UNCHANGED  cghs/tabs.py
```

`NETWORK_DELAY`: **0 diffs**. Parser output on the real bill `39538.pdf` is
**identical** (sha256 `dcdad30a7e0e5a65…`, 57 items / 371 occurrences).
Because the protected modules are byte-identical, the enhancement path is
unchanged by construction and any run-to-run timing variation is measurement
noise, not a code effect.

## MODIFIED-FILE MANIFEST (corrective round)

| File | Change |
|---|---|
| `app.py` | probe pause/resume + QMutex; safe ordered shutdown; 3 rgba panels; NEON aliases → semantic tokens; honest status slot |
| `cghs/session.py` | port config + validation; `CONNECTION_STAGES`; strict CDP validation; honest timing label |
| `ui_theme.py` | semantic tokens (`BRAND`, `ACCENT`, …); `status_colour()` understands the stage ladder |
| `START_CGHS_CHROME.cmd` | shared port resolution + validation; real DevTools validation before READY; non-zero exit codes |
| `tests/test_round18_corrective.py` | **new** — 41 regression tests |
| `UPGRADE_AUDIT_REPORT.md` | corrected counts; this section |

## STILL NOT VERIFIED (unchanged, and not claimed)
* **Windows build / EXE** — BLOCKED, no Windows host or PyInstaller.
* **UI rendering and 100/125/150% scaling** — BLOCKED, PyQt5 not installable here. The QThread fixes are verified statically and via pure-Python lifecycle execution, **not** by running the GUI.
* **Live portal** — `LIVE_PORTAL = NOT_VERIFIED`.
* The launcher is verified by **structural analysis only**; it has not been executed on Windows.

---
---

# ROUND 18 — FINAL CORRECTIVE PATCH (defects 1–3)

Three independently reported defects. Each verified against real source
before change; each has regression tests that **failed pre-fix**. Application
name, window title, executable and branding unchanged.

## FIX 1 — QThread shutdown was still unsafe

**Defect (confirmed verbatim):** `closeEvent()` ran

```python
if not probe.wait(self.SHUTDOWN_WAIT_MS):
    self.log("Chrome monitor did not stop in time")
self.chrome_probe = None
event.accept()
```

A probe that missed the deadline produced a log line, then the reference was
dropped and the close **accepted anyway** — destroying a live `QThread`.

**Root cause:** the timeout result was computed and discarded. There was no
path from "a thread is still running" to "refuse to close".

**Fix:** sequencing extracted into **`app_shutdown.plan_shutdown()`** — pure
Python, no PyQt import, so the failure branch is *executable in tests* rather
than inferred from source. It returns a `ShutdownDecision`
(`accept`, `clear_probe`, `stage`, `reason`, `keep_shutdown_latch`).

* Worker settled **first** (only it can be mid-transaction), probe second.
* Any thread still alive → `accept=False`, `clear_probe=False`, `event.ignore()`, **return immediately**.
* References are **preserved** on failure — dropping one would leak the thread and lose the only handle able to join it on a retry.
* `_shutting_down` **stays latched** while a close is pending, so no new batch can start mid-shutdown; a second close succeeds once the thread exits.
* No `terminate()`, no `quit()`, operator's Chrome never closed. All helpers are exception-safe: shutdown cannot itself raise.

**Tests (9)** executing the real logic: both stopped · probe refuses to stop ·
worker still running (probe untouched) · worker done but probe running ·
second close attempt succeeds · idle app · latch retained · no force-terminate
· `closeEvent` delegates to the tested logic.

## FIX 2 — Launcher returned success for a foreign service

**Defect (confirmed):** the existing-port branch printed advice and ran
`exit /b 0` at line 120 **whatever held the port** — including a non-Chrome
service.

**Fix:** a single `:VERIFY_DEVTOOLS` subroutine, called by **both** the
already-running and newly-launched paths (requirement: no duplicated
validation). It returns a tri-state:

| Result | Meaning | Operator sees | Exit |
|---|---|---|---|
| `0` | HTTP 200 + valid JSON + Chrome identity + `ws://` DevTools URL | `CHROME DEVTOOLS READY` | **0** |
| `2` | an HTTP/TCP service answered but is **not** Chrome | `PORT CONFLICT — NOT CHROME` | **4** |
| `1` | no usable answer | `CONNECTION FAILED` | **1** |

Chrome not found → **2**; invalid port → **3**. The launcher never reports
Chrome ready without passing the full check, and never silently continues.

## FIX 3 — Custom port did not reliably reach Python

**Defect (confirmed):** `setx` does not update an already-open Command
Prompt, and the sync block sat *after* the existing-port branch's early
`exit /b 0` — so the launcher could use 9333 while Python stayed on 9222.

**Fix:** one persistent shared configuration file.

```json
{ "host": "127.0.0.1", "port": 9222 }
```

* Windows `%LOCALAPPDATA%\CGHS-Enhancement-Bot\chrome_config.json`; elsewhere `~/.local/share/CGHS-Enhancement-Bot/chrome_config.json`. Overridable via `CGHS_CHROME_CONFIG`.
* The launcher **writes it before any connection check** (requirement 4), so the early-exit path can no longer skip synchronisation.
* The **file is authoritative over `CGHS_CHROME_PORT`**, precisely because a stale exported variable is the failure mode being fixed. It is re-read on every attach, so a change between runs is picked up.
* `probe_debugger_endpoint()` and `PortalSession.attach()` both resolve it; a hardcoded `9222` in `execute_item()` was removed.
* Invalid port, non-object JSON, corrupt JSON and non-loopback host all **raise a `ValueError` naming the file** — never a silent fallback.
* The operator never edits Python to change the port.

**Tests (20+)**: default 9222 · custom 9333 · custom 9444 · file beats stale
env · config changed between runs · 7 invalid port values · corrupt file ·
non-object file · non-loopback host · source reporting · launcher writes the
same filename · launcher writes before probing · no module hardcodes a port.

## PROTECTED MODULES

```
UNCHANGED  cghs/parsing.py   UNCHANGED  cghs/dom.py
UNCHANGED  cghs/rules.py     UNCHANGED  cghs/tabs.py
UNCHANGED  cghs/locators.py
CHANGED    cghs/orchestrator.py  (+5/-1)
```

**The one protected-module change, and why it was required.** FIX 3
requirement 8 states *"No hardcoded 9222 elsewhere overrides it."*
`execute_item()` had `debugger_address: str = "127.0.0.1:9222"`, which would
override the shared configuration. The change is the signature default →
`Optional[str] = None` plus three docstring lines. **No logic, no DOM
sequence, no dispatch behaviour altered**; callers passing an explicit
address are unaffected.

`NETWORK_DELAY`: **0 diffs.** Parser output on `39538.pdf` **bit-identical** —
sha256 `dcdad30a7e0e5a65…`, **57 items / 371 occurrences**, matching the
previous baseline exactly. Because `parsing.py`/`rules.py`/`locators.py` are
byte-identical, the enhancement path is unchanged by construction.

## THREE PREVIOUS-ROUND TESTS RETARGETED (disclosed, not weakened)

The patch deliberately moved two implementations, so three tests that
asserted the *old internals* were retargeted. Each still enforces the same
guarantee or a stronger one; **no assertion was softened and none deleted**:

| Test | Before | After |
|---|---|---|
| `test_2_close_event_stops_and_joins_the_status_probe` | `.stop()`/`.wait(` inline in `closeEvent` | delegation to `plan_shutdown` **plus** the stop/join asserted in `app_shutdown.py` |
| `test_2_close_event_prevents_new_work_and_waits_for_the_worker` | worker `.wait(` inline | latch in `closeEvent` **plus** `_wait(worker, …)` in `app_shutdown.py` |
| `test_4_launcher_and_application_share_the_same_setting` | launcher mentions `CGHS_CHROME_PORT` | **stronger** — launcher writes the exact filename Python reads |

## VERIFIED FINAL TEST RESULT

| | |
|---|---|
| Command | `python3 -m pytest -p no:cacheprovider -q` |
| Total collected | **860** |
| Passed | **852** |
| Failed | **0** |
| Skipped | **8** |
| New tests this patch | **38** (`tests/test_round18_final.py`) |
| Packaged files | **68** |

The 8 skips are environment-gated (no PyQt5, no Windows, no live portal).
**None is skipped to manufacture a pass.**

## NOT VERIFIED ON WINDOWS — stated plainly

* **`START_CGHS_CHROME.cmd` has never been executed.** There is no Windows host and no `cmd.exe` here. Its branch structure, exit codes and single-verification-routine property are verified by **static analysis of the real file only**. The PowerShell/batch behaviour itself is **NOT VERIFIED**.
* **The GUI has never been rendered.** PyQt5 is not installable here. FIX 1 is verified by executing the extracted decision logic with stand-in threads — real branch execution, but **not** a running Qt event loop. Window behaviour, 100/125/150 % scaling: **NOT VERIFIED**.
* **Windows EXE build:** **BLOCKED** (no PyInstaller/Windows).
* **`LIVE_PORTAL` = NOT_VERIFIED.** No live portal test was performed and no live success is claimed.
* The config-file path under `%LOCALAPPDATA%` is exercised on Linux via the `CGHS_CHROME_CONFIG` override; the literal Windows path is **NOT VERIFIED**.
