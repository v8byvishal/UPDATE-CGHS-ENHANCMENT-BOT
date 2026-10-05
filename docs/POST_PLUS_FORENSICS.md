# Post-Plus Procedure control — forensic status

**`LIVE_POST_PLUS_DOM_REPRODUCTION = NOT_VERIFIED`**
**`LIVE_FIX = NOT_VERIFIED`**

This environment is headless Linux with no portal credentials and no Windows
Chrome. The live DOM transition has **not** been captured, so no fix is
claimed. What follows is (1) an honest audit of what the repository actually
contains, (2) what the two live runs *prove* on their own, and (3) the
instrument to capture the rest.

---

## 1. Audit of the claimed previous fix (task section 11)

Every symbol a previous report claimed is genuinely present in the source —
this was verified by grep against the working tree, not taken from the report:

| symbol | present | file |
|---|---|---|
| `PROCEDURE_INPUT` (10 strategies, 4 arbitrated) | yes | `cghs/locators.py` |
| `SmartDOMResolver` | yes | `cghs/dom.py` |
| `control_state()` | yes | `cghs/controllers.py` |
| `_PROBE_QUERIES` (`[kind, value]`, CSS + XPath) | yes | `cghs/dom.py` |
| `AmbiguousControlException` | yes | `cghs/dom.py` |
| `GEOMETRY_VERIFIED_CONTROLS` | yes | `cghs/dom.py` |
| `_resolved_identity` / `_exclude_foreign_controls` | yes | `cghs/dom.py` |
| one controlled recovery + `STOPPED_SHARED_PORTAL_...` | yes | `cghs/orchestrator.py` |
| `TX-PROCEDURE-CONTROL-UNAVAILABLE` ≠ `TX-PORTAL-CONTEXT-LOST` | yes | `cghs/txstate.py` |

So the artifact does contain the previous work. **It did not fix the live
bug**, and the sections below explain why the hypothesis behind it was wrong.

---

## 2. What the live logs prove on their own

The live line is:

```
'PROCEDURE_INPUT' matched only non-interactable elements (hidden or zero-area)
```

That message is raised at exactly one place — `SmartDOMResolver.locate()`'s
last resort — and only when **both** of these hold:

* `matched_any == True` — at least one registered strategy matched at least
  one element, and
* every matched element failed `is_displayed()` in the two preceding passes.

### 2.1 This rules the previous hypothesis OUT

The fix shipped earlier assumed **remount + renumber** (section 6 cases B/G):
React replaces the Procedure input and gives it a new
`react-select-N` id. Four of the ten registered strategies are
label-relative, position-independent and instance-independent:

```
//label[…procedure…]/following::div[contains(@class,'-container')]//input
//label[…procedure…]/following::input[@role='combobox']
```

If a **new visible** Procedure input existed anywhere after the Procedure
label, one of those two would have matched it and `is_displayed()` would have
been `True` — the control would have been returned and the item would have
succeeded.

It did not. Every candidate was **not displayed**. A pure remount/renumber
therefore **cannot** explain the live failure, and neither can
"old hidden clone + new visible clone": there was no visible clone.

### 2.2 What it is consistent with

An element is not displayed when **it or any ancestor** has `display:none`,
`visibility:hidden`, or zero area. Since the label-relative strategies
matched, the *Procedure label is still in the document*. The evidence is
therefore most consistent with:

* **E** — the entry form became hidden/collapsed, or
* **K** — a portal action is required to reopen the entry form, or
* **J** — the control is present but disabled/covered.

Section 7 of the task warns about exactly this: *a selector-only fix is wrong
if the entire form is being replaced or hidden.* That is the likely reason
three rounds of selector work did not help.

### 2.3 Why "Treatment Plan context = VERIFIED" is not a contradiction

This looked contradictory and is not. Frame validation calls the compact
probe, whose `ctx.procedure` is:

```js
procedure: !!firstVisible(Q.PROCEDURE_INPUT || [])
```

and `firstVisible()` **falls back to a non-visible element**:

```js
function firstVisible(queries){
  for (…) { if (vis(els[j])) { return els[j]; } }   // prefer visible
  for (…) { if (e2.length) { return e2[0]; } }      // FALLBACK: first match,
  return null;                                      //   visible or not
}
```

So `ctx.procedure` is `true` when the control is merely **present**, while
the Selenium resolver demands it be **usable**. Both log lines are true at
once, and together they say:

> the Treatment Plan page is still loaded, and every Procedure control on it
> is hidden.

This is a second, narrower instance of the section 12 probe/resolver
mismatch — not the *universe* of strategies this time (that was aligned
earlier) but the *visibility semantics*.

**It has deliberately not been changed.** Making `firstVisible()` strict
would make frame validation fail whenever the form is collapsed, which would
trigger frame rediscovery and resurrect precisely the
`TX-PORTAL-CONTEXT-LOST` cascade that section 2 requires be preserved. The
correct change depends on what the capture shows, so it waits for evidence.

---

## 3. What has NOT been proven

* whether the entry form is hidden, collapsed, disabled, re-parented or
  replaced;
* which **ancestor** hides it, and with which property;
* whether the portal requires an operator action (an "Add"/"New" affordance,
  an accordion, a tab) to bring the form back;
* whether `react-select` instance numbers change at all.

No production code has been modified for this task. `cghs/` is byte-identical
to the previous commit.

---

## 4. The instrument

`tools/forensic_post_plus.py` captures the transition. It is **read-only**:
tests assert the capture script contains no `click`, `sendKeys`, `value =`,
`dispatchEvent`, `removeChild`, `setAttribute` or `style.display`, and never
reads a field value — the only text it reads is a `<label>` caption and a
`<button>` caption, so no patient identifier can reach the report.

### Run it

```bat
chrome.exe --remote-debugging-port=9222
:: log in by hand, open the patient's Treatment Plan page
python tools\forensic_post_plus.py --first CN002:6 --second C001:2
```

It attaches through the production `PortalSession.attach` path (no login, MFA,
cookie or profile automation), drives the two items with the production
orchestrator, and captures four states:

| state | when |
|---|---|
| A | before the first item |
| C | immediately after Plus confirmed |
| D | immediately before the next procedure resolution |
| E | after the second attempt |

### What it records

Per Procedure-like candidate, from the **real locator registry** (all ten
strategies, CSS and XPath alike): document index, tag, id, name, role, class,
placeholder, `aria-controls` / `-owns` / `-expanded` / `-autocomplete`,
autocomplete, disabled, readonly, display, visibility, opacity,
pointer-events, bounding rect, `is_displayed`, active-element relation,
nearest label, nearest React-Select container, full DOM path — and the
decisive field:

```
hiding_ancestor: {depth, reasons, node, path}
```

the **first ancestor that hides the node and why**. A hidden input tells you
nothing; the ancestor that hides it tells you whether React replaced the
control (depth 0) or the portal collapsed the form (depth > 0).

It also records the whole entry form (Procedure, Speciality, Quantity,
Reason, Plus) per section 7, visible overlays/modals/accordions/disabled
fieldsets per section 8, every `react-select-*` id present per section 9, and
the inventory counts from section 5.

### What it concludes

It prints a verdict mapping the observed transition onto section 6 A–K, and
names the hiding ancestor by DOM path. `tests/test_forensic_post_plus.py`
(19 tests) proves the verdict logic discriminates the cases — including that
a self-hidden input (depth 0) is reported as a clone and **not**
misreported as a collapsed form.

Candidate "reopen the form" affordances are **listed, never clicked**
(section 8: do not invent an action).

### Send back

`post_plus_forensics.json`. With the `hiding_ancestor` chain from state D the
root cause is decidable, and the fix can then be the minimum change that
addresses the real transition — including, if it turns out an operator action
is required, making the single controlled recovery perform *that* real
transition instead of re-running the same locator (section 16).
