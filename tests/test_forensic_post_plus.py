"""The post-Plus forensic instrument must discriminate the section 6 cases.

This tests the DIAGNOSTIC's own logic - that given a DOM transition it names
the right hypothesis - so the operator can trust the verdict it prints on the
live portal.  It is explicitly NOT a claim that the live bug is fixed.
"""

from __future__ import annotations

import pytest

from tools.forensic_post_plus import FORM_FIELDS, _queries, verdict


def _candidate(**kw):
    base = {"id": None, "visible": True, "rect": {"x": 0, "y": 0, "w": 200, "h": 30},
            "disabled": False, "matched_by": [0], "dom_path": "div > input",
            "hiding_ancestor": None}
    base.update(kw)
    return base


def _snap(candidates, form=None, affordances=None, rs_ids=0, state="X"):
    return {
        "state": state, "captured_at": "00:00:00.000",
        "candidates": candidates,
        "counts": {"inputs": 10, "comboboxes": 4, "comboboxes_visible": 3,
                   "comboboxes_enabled": 4, "react_select_containers": 4,
                   "procedure_candidates": len(candidates),
                   "procedure_candidates_visible":
                       len([c for c in candidates if c["visible"]])},
        "form": form or {k: {"found": True, "visible": True,
                             "node": {"tag": "input", "id": None},
                             "hiding_ancestor": None} for k in FORM_FIELDS},
        "overlays": [], "affordances": affordances or [],
        "reactSelect": {"ids": [], "count": rs_ids},
    }


def _said(findings, needle):
    return any(needle in f for f in findings)


# ---------------------------------------------------------------------------
# the registry is the single source of candidates (section 12)
# ---------------------------------------------------------------------------

def test_the_instrument_probes_the_real_locator_registry():
    from cghs.locators import LOCATORS
    for key in FORM_FIELDS:
        assert len(_queries(key)) == len(LOCATORS[key])


def test_css_and_xpath_strategies_are_both_carried():
    kinds = {kind for kind, _ in _queries("PROCEDURE_INPUT")}
    assert kinds == {"css", "xpath"}, (
        "the capture must not silently drop CSS strategies the resolver uses")


# ---------------------------------------------------------------------------
# section 6 discrimination
# ---------------------------------------------------------------------------

def test_case_G_control_genuinely_absent(capsys):
    found = verdict(_snap([_candidate(id="react-select-5-input")]), _snap([]))
    assert _said(found, "G:"), found


def test_case_E_whole_form_collapsed_by_an_ancestor(capsys):
    """Every candidate hidden by a DEEP ancestor = the form collapsed."""
    hidden_by_form = {"depth": 4, "reasons": ["display:none"],
                      "node": {"tag": "div", "id": "entry-form"},
                      "path": "div#entry-form"}
    after = _snap(
        [_candidate(id="react-select-5-input", visible=False,
                    hiding_ancestor=hidden_by_form)],
        form={k: {"found": True, "visible": False,
                  "node": {"tag": "input", "id": None},
                  "hiding_ancestor": hidden_by_form} for k in FORM_FIELDS},
    )
    found = verdict(_snap([_candidate(id="react-select-5-input")]), after)
    assert _said(found, "E/K:"), found
    assert _said(found, "entry FORM was collapsed"), found
    assert _said(found, "entry-form"), "the hiding ancestor must be named"


def test_case_E_reports_how_much_of_the_form_is_hidden():
    hidden = {"depth": 3, "reasons": ["display:none"],
              "node": {"tag": "div", "id": "panel"}, "path": "div#panel"}
    after = _snap([_candidate(visible=False, hiding_ancestor=hidden)],
                  form={k: {"found": True, "visible": False,
                            "node": {"tag": "input", "id": None},
                            "hiding_ancestor": hidden} for k in FORM_FIELDS})
    found = verdict(_snap([_candidate()]), after)
    assert _said(found, f"{len(FORM_FIELDS)} of {len(FORM_FIELDS)} entry-form"), found


def test_case_A_hidden_clone_in_place_is_not_reported_as_a_collapsed_form():
    """Hidden ON ITSELF (depth 0) is a clone, not a collapsed form."""
    after = _snap([_candidate(id="react-select-5-input", visible=False,
                              hiding_ancestor={"depth": 0,
                                               "reasons": ["display:none"],
                                               "node": {"tag": "input", "id": "x"},
                                               "path": "input#x"})])
    found = verdict(_snap([_candidate(id="react-select-5-input")]), after)
    assert _said(found, "A/J:"), found
    assert not _said(found, "E/K:"), "a self-hidden input is not a form collapse"


def test_case_B_remount_reports_both_the_new_and_the_removed_id():
    found = verdict(
        _snap([_candidate(id="react-select-5-input")]),
        _snap([_candidate(id="react-select-9-input")]))
    assert _said(found, "B/G:"), found
    assert _said(found, "B:"), found


def test_case_C_same_instance_survives():
    found = verdict(_snap([_candidate(id="react-select-5-input")]),
                    _snap([_candidate(id="react-select-5-input")]))
    assert _said(found, "C:"), found


def test_case_K_affordances_are_reported_but_never_clicked():
    after = _snap(
        [_candidate(visible=False,
                    hiding_ancestor={"depth": 2, "reasons": ["display:none"],
                                     "node": {"tag": "div", "id": "f"},
                                     "path": "div#f"})],
        affordances=[{"text": "Add New", "node": {"tag": "button", "id": "add"},
                      "path": "button#add"}])
    found = verdict(_snap([_candidate()]), after)
    assert _said(found, "K candidate"), found


def test_a_still_visible_control_is_not_reported_as_the_failure():
    """If the control is visible at capture time, say so - do not invent a cause."""
    found = verdict(_snap([_candidate(id="a")]), _snap([_candidate(id="a")]))
    assert _said(found, "the control IS visible after Plus"), found


# ---------------------------------------------------------------------------
# the instrument must not mutate the portal (section 17)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("forbidden", [
    ".click(", ".sendKeys", ".value =", "dispatchEvent", "removeChild",
    "setAttribute", "classList.remove", "style.display",
])
def test_capture_script_never_mutates_the_dom(forbidden):
    from tools.forensic_post_plus import CAPTURE_JS
    assert forbidden not in CAPTURE_JS, (
        f"the forensic capture must be read-only, found {forbidden!r}")


def test_capture_script_never_reads_a_field_value():
    from tools.forensic_post_plus import CAPTURE_JS
    assert ".value" not in CAPTURE_JS, "field values are patient data"
