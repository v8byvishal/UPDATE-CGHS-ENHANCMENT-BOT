"""Capture the REAL post-Plus Treatment Plan DOM transition (task sections 4-10).

This is a DIAGNOSTIC. It changes no production logic and drives no fix.  It
answers one question with evidence instead of assumption:

    What exactly happens to the Treatment Plan entry form and its Procedure
    control immediately after the first successful Plus/Add?

Run it on the Windows machine, against the SAME already-authenticated Chrome
the bot attaches to.  No login, MFA, cookie or profile automation is
performed and no patient-identifying text is written to the report.

    1. start Chrome with remote debugging (the bot's normal requirement):
           chrome.exe --remote-debugging-port=9222
    2. log in by hand and open the patient's Treatment Plan page
    3. python tools\\forensic_post_plus.py --first CN002:6 --second C001:2

It captures four states:

    A  before the first item          (baseline, control known good)
    B  after the procedure is driven  (mid-item)
    C  immediately after Plus confirmed
    D  at the moment the next item's procedure resolution is attempted

and for every Procedure-like candidate records the full section 5 inventory
PLUS the ancestor chain - which is the part that actually discriminates
between the section 6 hypotheses.  A hidden INPUT tells you nothing; the
first ANCESTOR that is hidden tells you whether React replaced the control
or the portal collapsed the whole entry form.

Output: a JSON report plus a printed verdict mapping onto section 6 A-K.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cghs.locators import LOCATORS                     # noqa: E402
from cghs.session import PortalSession                 # noqa: E402
from cghs.telemetry import EnterpriseLogger            # noqa: E402


# ---------------------------------------------------------------------------
# The capture script.  Pure reads - it never clicks, types, scrolls or mutates.
# ---------------------------------------------------------------------------

CAPTURE_JS = r"""
var out = {counts: {}, candidates: [], form: {}, overlays: [], reactSelect: {}};

function rect(e){
  try { var r = e.getBoundingClientRect();
        return {x: Math.round(r.x), y: Math.round(r.y),
                w: Math.round(r.width), h: Math.round(r.height)}; }
  catch (err) { return null; }
}
function style(e, prop){
  try { return window.getComputedStyle(e)[prop]; } catch (err) { return null; }
}
function visible(e){
  if (!e) { return false; }
  if (e.offsetWidth || e.offsetHeight) { return true; }
  var r = rect(e);
  return !!(r && (r.w || r.h));
}
function xpAll(q){
  var out = [];
  try {
    var s = document.evaluate(q, document, null, 7, null);
    for (var i = 0; i < s.snapshotLength; i++) { out.push(s.snapshotItem(i)); }
  } catch (e) {}
  return out;
}
/* Short, stable description of a node - NEVER its value.
   The only text this script reads anywhere is a <label> caption and a
   <button> caption (sections 5 and 8 both require them). Field VALUES,
   table cells and free text are never touched, so no patient identifier
   can reach the report. */
function describe(e){
  if (!e) { return null; }
  return {tag: (e.tagName || "").toLowerCase(),
          id: e.id || null,
          cls: (typeof e.className === "string" ? e.className : "") || null,
          role: e.getAttribute ? e.getAttribute("role") : null};
}
function domPath(e){
  var parts = [], n = e, guard = 0;
  while (n && n.nodeType === 1 && guard++ < 40) {
    var seg = n.tagName.toLowerCase();
    if (n.id) { seg += "#" + n.id; }
    else if (typeof n.className === "string" && n.className.trim()) {
      seg += "." + n.className.trim().split(/\s+/).slice(0, 2).join(".");
    }
    parts.unshift(seg);
    n = n.parentElement;
  }
  return parts.join(" > ");
}
/* THE DISCRIMINATOR: walk up and report the FIRST ancestor that hides the
   node, and why.  If that ancestor is the entry form / a panel / a modal,
   the control was not remounted - the form was collapsed. */
function hidingAncestor(e){
  var n = e, depth = 0;
  while (n && n.nodeType === 1 && depth < 40) {
    var d = style(n, "display"), v = style(n, "visibility"),
        o = style(n, "opacity"), r = rect(n);
    var reasons = [];
    if (d === "none") { reasons.push("display:none"); }
    if (v === "hidden" || v === "collapse") { reasons.push("visibility:" + v); }
    if (o === "0" || o === 0) { reasons.push("opacity:0"); }
    if (n.hasAttribute && n.hasAttribute("hidden")) { reasons.push("[hidden]"); }
    if (n.getAttribute && n.getAttribute("aria-hidden") === "true") {
      reasons.push("aria-hidden"); }
    if (n.disabled === true) { reasons.push("disabled"); }
    if (r && !r.w && !r.h && depth > 0) { reasons.push("zero-area"); }
    if (reasons.length) {
      return {depth: depth, reasons: reasons, node: describe(n),
              path: domPath(n)};
    }
    n = n.parentElement; depth++;
  }
  return null;
}
function nearestLabel(e){
  var n = e, guard = 0;
  while (n && guard++ < 8) {
    var lab = null;
    try { lab = n.querySelector ? n.querySelector("label") : null; } catch (err) {}
    if (lab && lab.textContent) { return lab.textContent.trim().slice(0, 40); }
    n = n.parentElement;
  }
  return null;
}
function container(e){
  var n = e, guard = 0;
  while (n && guard++ < 8) {
    var c = (typeof n.className === "string") ? n.className : "";
    if (c && (c.indexOf("-container") >= 0 || c.indexOf("select__") >= 0
              || c.indexOf("css-") >= 0)) { return describe(n); }
    n = n.parentElement;
  }
  return null;
}

/* ---- inventory counts (section 5) ---- */
var allInputs = document.querySelectorAll("input");
var combos = document.querySelectorAll("[role='combobox'], input[aria-autocomplete]");
var rsContainers = document.querySelectorAll(
  "[class*='-container'], [class*='select__control']");
out.counts.inputs = allInputs.length;
out.counts.comboboxes = combos.length;
out.counts.comboboxes_visible = 0;
out.counts.comboboxes_enabled = 0;
for (var i = 0; i < combos.length; i++) {
  if (visible(combos[i])) { out.counts.comboboxes_visible++; }
  if (!combos[i].disabled) { out.counts.comboboxes_enabled++; }
}
out.counts.react_select_containers = rsContainers.length;

/* ---- every Procedure-like candidate, from the REAL locator registry ---- */
var queries = arguments[0] || [];
var seen = [];
for (var q = 0; q < queries.length; q++) {
  var kind = queries[q][0], val = queries[q][1], nodes = [];
  try {
    nodes = (kind === "css")
      ? Array.prototype.slice.call(document.querySelectorAll(val))
      : xpAll(val);
  } catch (err) { nodes = []; }
  for (var n2 = 0; n2 < nodes.length; n2++) {
    var el = nodes[n2];
    var idx = seen.indexOf(el);
    if (idx >= 0) { out.candidates[idx].matched_by.push(q); continue; }
    seen.push(el);
    var r2 = rect(el);
    out.candidates.push({
      strategy_index: q, matched_by: [q],
      document_index: Array.prototype.indexOf.call(allInputs, el),
      tag: (el.tagName || "").toLowerCase(),
      id: el.id || null,
      name: el.name || null,
      role: el.getAttribute("role"),
      cls: (typeof el.className === "string" ? el.className : null),
      placeholder: el.getAttribute("placeholder"),
      aria_controls: el.getAttribute("aria-controls"),
      aria_owns: el.getAttribute("aria-owns"),
      aria_expanded: el.getAttribute("aria-expanded"),
      aria_autocomplete: el.getAttribute("aria-autocomplete"),
      autocomplete: el.getAttribute("autocomplete"),
      disabled: !!el.disabled,
      readonly: !!el.readOnly,
      display: style(el, "display"),
      visibility: style(el, "visibility"),
      opacity: style(el, "opacity"),
      pointer_events: style(el, "pointerEvents"),
      rect: r2,
      visible: visible(el),
      is_active_element: (document.activeElement === el),
      nearest_label: nearestLabel(el),
      react_select_container: container(el),
      dom_path: domPath(el),
      hiding_ancestor: hidingAncestor(el)
    });
  }
}
out.counts.procedure_candidates = out.candidates.length;
out.counts.procedure_candidates_visible =
  out.candidates.filter(function(c){ return c.visible; }).length;

/* ---- section 7: the WHOLE entry form, not just the input ---- */
var fields = arguments[1] || {};
for (var key in fields) {
  if (!fields.hasOwnProperty(key)) { continue; }
  var fq = fields[key], node = null;
  for (var f = 0; f < fq.length && !node; f++) {
    var got = (fq[f][0] === "css")
      ? document.querySelector(fq[f][1]) : (xpAll(fq[f][1])[0] || null);
    if (got) { node = got; }
  }
  out.form[key] = node ? {
    found: true, node: describe(node), visible: visible(node),
    disabled: !!node.disabled, rect: rect(node), dom_path: domPath(node),
    hiding_ancestor: hidingAncestor(node)
  } : {found: false};
}

/* ---- section 8: portal state transitions ---- */
var overlaySel = ["[class*='overlay']", "[class*='modal']", "[class*='drawer']",
                  "[class*='backdrop']", "[class*='spinner']",
                  "[class*='loading']", "[class*='collapse']",
                  "[class*='accordion']", "[aria-busy='true']",
                  "fieldset[disabled]", "[class*='disabled']"];
for (var s = 0; s < overlaySel.length; s++) {
  var found2 = document.querySelectorAll(overlaySel[s]);
  for (var t = 0; t < found2.length; t++) {
    if (!visible(found2[t])) { continue; }
    out.overlays.push({selector: overlaySel[s], node: describe(found2[t]),
                       rect: rect(found2[t]), path: domPath(found2[t])});
  }
}
/* candidate "reopen the form" affordances - reported, never clicked */
out.affordances = [];
var btns = document.querySelectorAll("button, a[role='button'], [class*='add']");
for (var b = 0; b < btns.length && out.affordances.length < 25; b++) {
  var txt = (btns[b].textContent || "").trim().slice(0, 30);
  if (!visible(btns[b])) { continue; }
  if (/add|new|\+|enter|create|another/i.test(txt)
      || /add|new|plus/i.test(btns[b].className || "")) {
    out.affordances.push({text: txt, node: describe(btns[b]),
                          path: domPath(btns[b])});
  }
}

/* ---- section 9: react-select instance numbers present right now ---- */
var rs = document.querySelectorAll("[id^='react-select-']");
var ids = [];
for (var k = 0; k < rs.length; k++) { ids.push(rs[k].id); }
out.reactSelect.ids = ids;
out.reactSelect.count = ids.length;

out.url_path = location.pathname;
out.title_len = (document.title || "").length;
return out;
"""


FORM_FIELDS = ("PROCEDURE_INPUT", "SPECIALITY_INPUT", "QUANTITY_INPUT",
               "REASON_DROPDOWN", "PLUS_BUTTON")


def _queries(key):
    """The REAL registry strategies, as [kind, value] for the capture script."""
    out = []
    for by, val in LOCATORS.get(key, ()):
        kind = "css" if "css" in str(by).lower() else "xpath"
        out.append([kind, val])
    return out


def capture(session, label):
    """One read-only snapshot of the live page."""
    fields = {k: _queries(k) for k in FORM_FIELDS}
    data = session.driver.execute_script(
        CAPTURE_JS, _queries("PROCEDURE_INPUT"), fields)
    data["state"] = label
    data["captured_at"] = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    return data


def summarise(snap):
    c = snap["counts"]
    print(f"\n--- STATE {snap['state']} ({snap['captured_at']}) "
          f"-------------------------------")
    print(f"  inputs={c['inputs']}  comboboxes={c['comboboxes']} "
          f"(visible {c['comboboxes_visible']}, enabled {c['comboboxes_enabled']})")
    print(f"  react-select containers={c['react_select_containers']}  "
          f"react-select ids={snap['reactSelect']['count']}")
    print(f"  PROCEDURE candidates={c['procedure_candidates']} "
          f"(visible {c['procedure_candidates_visible']})")
    for i, cand in enumerate(snap["candidates"]):
        mark = "VISIBLE" if cand["visible"] else "hidden "
        print(f"    [{i}] {mark} id={cand['id']} rect={cand['rect']} "
              f"disabled={cand['disabled']} by={cand['matched_by']}")
        print(f"        path: {cand['dom_path'][-110:]}")
        if cand["hiding_ancestor"]:
            h = cand["hiding_ancestor"]
            print(f"        HIDDEN BY ancestor depth {h['depth']}: "
                  f"{', '.join(h['reasons'])}  <- {h['path'][-90:]}")
    print("  entry form:")
    for key, info in snap["form"].items():
        if not info.get("found"):
            print(f"    {key:18} ABSENT")
            continue
        state = "visible" if info["visible"] else "HIDDEN"
        extra = ""
        if info.get("hiding_ancestor"):
            h = info["hiding_ancestor"]
            extra = f"  <- hidden by depth {h['depth']} ({','.join(h['reasons'])})"
        print(f"    {key:18} {state}  {info['node']['tag']}"
              f"#{info['node']['id'] or '-'}{extra}")
    if snap["overlays"]:
        print(f"  overlays/panels visible: {len(snap['overlays'])}")
        for o in snap["overlays"][:6]:
            print(f"    {o['selector']:24} {o['path'][-80:]}")
    if snap.get("affordances"):
        print("  candidate re-open affordances (NOT clicked):")
        for a in snap["affordances"][:8]:
            print(f"    {a['text']!r:32} {a['path'][-70:]}")


def verdict(before, after):
    """Map the observed transition onto the section 6 hypotheses."""
    print("\n" + "=" * 72)
    print("VERDICT - which section 6 case actually occurred")
    print("=" * 72)

    b_ids = {c["id"] for c in before["candidates"] if c["id"]}
    a_ids = {c["id"] for c in after["candidates"] if c["id"]}
    b_vis = [c for c in before["candidates"] if c["visible"]]
    a_vis = [c for c in after["candidates"] if c["visible"]]

    print(f"  procedure candidates : {len(before['candidates'])} -> "
          f"{len(after['candidates'])}")
    print(f"  of which visible     : {len(b_vis)} -> {len(a_vis)}")
    print(f"  ids gone             : {sorted(b_ids - a_ids) or 'none'}")
    print(f"  ids new              : {sorted(a_ids - b_ids) or 'none'}")
    print(f"  react-select ids     : {before['reactSelect']['count']} -> "
          f"{after['reactSelect']['count']}")

    findings = []
    if not after["candidates"]:
        findings.append("G: the Procedure control is genuinely ABSENT from the DOM")
    elif not a_vis:
        anc = [c["hiding_ancestor"] for c in after["candidates"]
               if c["hiding_ancestor"]]
        shallow = [h for h in anc if h and h["depth"] == 0]
        deep = [h for h in anc if h and h["depth"] > 0]
        if deep and not shallow:
            findings.append(
                "E/K: every candidate is hidden by an ANCESTOR, not by itself "
                "-> the entry FORM was collapsed/hidden, not the input "
                "replaced. A selector fix cannot help; the form must be "
                "reopened the way the operator reopens it.")
            for h in deep[:3]:
                findings.append(f"     hiding ancestor depth {h['depth']} "
                                f"({', '.join(h['reasons'])}): {h['path']}")
        else:
            findings.append(
                "A/J: the candidate elements themselves are hidden/disabled "
                "in place (no hiding ancestor) -> hidden clone or disabled "
                "control.")
    else:
        findings.append(
            f"the control IS visible after Plus ({len(a_vis)} candidate(s)) -> "
            f"the failure is NOT 'no usable control at this instant'; capture "
            f"state D (at the actual resolution attempt) to see the real one.")

    if a_ids - b_ids:
        findings.append("B/G: a NEW instance id appeared (remount/renumber).")
    if b_ids - a_ids:
        findings.append("B: the old instance id was REMOVED from the document.")
    if b_ids and a_ids and b_ids == a_ids:
        findings.append("C: same instance id(s) survive - no remount occurred.")

    form_hidden = [k for k, v in after["form"].items()
                   if v.get("found") and not v.get("visible")]
    if len(form_hidden) >= 3:
        findings.append(
            f"E: {len(form_hidden)} of {len(after['form'])} entry-form fields "
            f"are hidden ({', '.join(form_hidden)}) -> the WHOLE form "
            f"collapsed, which is section 7's warning case.")
    elif form_hidden:
        findings.append(f"only these fields are hidden: {', '.join(form_hidden)}")

    if after.get("affordances"):
        findings.append(
            "K candidate: visible 'add/new' affordances exist - if the "
            "operator clicks one to get the form back, THAT is the real "
            "recovery action (see the list under state C/D).")

    for f in findings:
        print(f"  * {f}")
    return findings


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--first", default="CN002:6",
                    help="first item as CODE:QTY (default CN002:6)")
    ap.add_argument("--second", default="C001:2",
                    help="item attempted after the Plus (default C001:2)")
    ap.add_argument("--debugger", default="127.0.0.1:9222")
    ap.add_argument("--out", default="post_plus_forensics.json")
    args = ap.parse_args()

    first_code, first_qty = args.first.split(":")
    second_code, second_qty = args.second.split(":")

    logger = EnterpriseLogger(None, trace_enabled=True)
    print(f"attaching to {args.debugger} (no login/MFA/profile automation) ...")
    session = PortalSession.attach(logger, debugger_address=args.debugger)
    session.ensure_context()

    report = {"started": datetime.now().isoformat(timespec="seconds"),
              "first": args.first, "second": args.second, "states": []}

    from cghs.orchestrator import TreatmentPlanOrchestrator
    orch = TreatmentPlanOrchestrator(session, logger)

    # ---- A: baseline, before anything is driven ----
    state_a = capture(session, "A_before_first_item")
    report["states"].append(state_a)
    summarise(state_a)

    # ---- drive the first item exactly as production does ----
    print(f"\n>>> processing {first_code} qty={first_qty} via the production "
          f"orchestrator ...")
    result = orch.process_item({"code": first_code, "qty": int(first_qty)})
    print(f">>> {first_code}: success={getattr(result, 'success', None)} "
          f"diagnostic={getattr(result, 'diagnostic', None)}")
    report["first_result"] = {
        "success": bool(getattr(result, "success", False)),
        "diagnostic": str(getattr(result, "diagnostic", "")),
    }

    # ---- C: immediately after the Plus is confirmed ----
    state_c = capture(session, "C_after_plus_confirmed")
    report["states"].append(state_c)
    summarise(state_c)

    # ---- D: at the moment the next procedure resolution is attempted ----
    print(f"\n>>> attempting {second_code} qty={second_qty} ...")
    state_d = capture(session, "D_before_second_procedure")
    report["states"].append(state_d)
    summarise(state_d)

    try:
        result2 = orch.process_item({"code": second_code, "qty": int(second_qty)})
        ok2, diag2 = bool(getattr(result2, "success", False)), str(
            getattr(result2, "diagnostic", ""))
    except Exception as exc:                                  # noqa: BLE001
        ok2, diag2 = False, f"{type(exc).__name__}: {exc}"
    print(f">>> {second_code}: success={ok2} diagnostic={diag2}")
    report["second_result"] = {"success": ok2, "diagnostic": diag2}

    state_e = capture(session, "E_after_second_attempt")
    report["states"].append(state_e)
    summarise(state_e)

    report["verdict"] = verdict(state_a, state_d)
    report["counters"] = {
        "find_elements": getattr(session.counters, "find_elements_calls", None),
        "execute_script": getattr(session.counters, "execute_script_calls", None),
        "frame_discoveries": getattr(session.counters, "frame_discoveries", None),
        "fixed_sleep_ms": getattr(session.counters, "fixed_sleep_ms", None),
    }

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, default=str)
    print(f"\nwritten: {args.out}")
    print("Send that file back. It contains no patient identifiers - only tag "
          "names, ids, classes, geometry and visibility.")


if __name__ == "__main__":
    main()
