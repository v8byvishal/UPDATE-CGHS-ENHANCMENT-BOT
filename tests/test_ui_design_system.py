"""ROUND 18 - PHASE 6: the design system, and proof the redesign is
PRESENTATION ONLY (identity, controls and behaviour unchanged).

These tests are static: PyQt5 cannot be installed on the CI container, so
no widget is instantiated.  Every assertion reads real source.
"""
import re
from pathlib import Path

import ui_theme

REPO = Path(__file__).resolve().parents[1]
APP = (REPO / "app.py").read_text(encoding="utf-8", errors="replace")
THEME = (REPO / "ui_theme.py").read_text(encoding="utf-8", errors="replace")

ICE_SILVER = "#DDE3E8"
DEEP_AUBERGINE = "#3A0F34"
MARIGOLD = "#EAAA00"

#: Colours the design system is allowed to use: the three brand colours plus
#: neutral greys, readable text tones and muted semantic status colours.
ALLOWED = {
    ICE_SILVER, DEEP_AUBERGINE, MARIGOLD,
    "#FFFFFF", "#F4F7F9", "#E7ECF0", "#D2DAE1", "#C9D3DA",
    "#4D1746", "#5C1D54", "#2A0A26",
    "#C2CCD4", "#A9B6C0",
    "#1C2126", "#55606B", "#7E8A95",
    "#F2EFF1", "#C3AFBE", "#2A1A00", "#F7F4F6",
    "#1F7A4D", "#A8700A", "#A32638", "#2A5E8C",
    "#E3F1EA", "#FBF0D8", "#F8E4E7", "#E4EDF5", "#FCF2D6",
    "#D59B00", "#C08C00",
}


def _hexes(text):
    return {m.upper() for m in re.findall(r"#[0-9a-fA-F]{6}", text)}


def test_the_three_brand_colours_are_the_base_of_the_system():
    assert ui_theme.ICE_SILVER == ICE_SILVER
    assert ui_theme.DEEP_AUBERGINE == DEEP_AUBERGINE
    assert ui_theme.MARIGOLD == MARIGOLD
    assert ui_theme.BG == ICE_SILVER, "workspace background must be ice silver"
    assert ui_theme.SIDEBAR == DEEP_AUBERGINE, "sidebar must be aubergine"
    assert ui_theme.BORDER_ACTIVE == MARIGOLD, "focus/active must be marigold"


def test_no_colour_outside_the_approved_system_is_used():
    for name, text in (("ui_theme.py", THEME), ("app.py", APP)):
        stray = _hexes(text) - ALLOWED
        assert not stray, f"{name} uses non-system colours: {sorted(stray)}"


def test_the_retired_neon_palette_is_gone():
    for neon in ("#00e5ff", "#b06cff", "#2fe08a", "#ff4d6d", "#ffb637",
                 "#04050a", "#0a0c12", "#e9edf6", "#a3adc2"):
        assert neon not in APP.lower(), f"neon colour {neon} still in app.py"


def test_the_whole_stylesheet_is_centralised_and_covers_every_widget():
    sheet = ui_theme.app_stylesheet()
    for widget in ("QPushButton", "QLineEdit", "QComboBox", "QTableWidget",
                   "QHeaderView", "QTabBar", "QProgressBar", "QScrollBar",
                   "QToolTip", "QMenu", "QCheckBox", "QListWidget"):
        assert widget in sheet, f"{widget} has no centralised styling"
    assert MARIGOLD in sheet and DEEP_AUBERGINE in sheet


def test_the_theme_never_imports_business_logic():
    """A styling change must never be able to alter extraction or timing."""
    assert not re.search(r"^\s*(import|from)\s+cghs", THEME, re.MULTILINE)


# ----------------------------------------------------------------- identity
def test_the_application_identity_is_unchanged():
    assert 'setWindowTitle("CGHS Automation Console - Vishal Singh Chauhan")' in APP
    spec = (REPO / "CGHS_Enhancement_Bot.spec").read_text(
        encoding="utf-8", errors="replace")
    assert "name='CGHS_Enhancement_Bot'" in spec
    for banned in ("GovClaim", "Gov Claim", "GOVCLAIM"):
        assert banned not in APP, f"the app was renamed to {banned!r}"
        assert banned not in THEME, f"the theme renamed the app to {banned!r}"


def test_every_control_and_handler_is_preserved():
    """The redesign may not delete a button, field, table or signal."""
    for control in ("self.start_btn", "self.stop_btn", "self.upload_btn",
                    "self.queue_table", "self.results_table", "self.log_box",
                    "self.automation_status_label", "self.batch_status_label",
                    "self.results_count_label", "self.chrome_status_label",
                    "self.system_online_label", "self.header_user_label"):
        assert control in APP, f"control {control} was removed by the redesign"
    for handler in ("start_batch", "handle_dropped_files", "batch_finished",
                    "closeEvent", "update_chrome_status"):
        assert f"def {handler}" in APP, f"handler {handler} was removed"
