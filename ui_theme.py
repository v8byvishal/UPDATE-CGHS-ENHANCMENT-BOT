"""Centralised visual theme for the desktop UI - BLACK + NEON.

PRESENTATION ONLY.  This module contains colour tokens and Qt stylesheet
strings and nothing else: no business rules, no portal logic, no automation,
no state.  It deliberately imports nothing from PyQt5 (or from ``cghs``) so
it stays trivially importable and testable.

Surface hierarchy (task section 7) - each level stays distinguishable:

    BG  #04050a   application background, the deepest black
     |
    SURFACE  #0a0c12        docked panels
     |
    SURFACE_RAISED  #11141c  cards sitting on a panel
     |
    BORDER  #1d2230         hairline separation
     |
    NEON                    accents only - never whole panels

Semantics (task section 6):

    CYAN / BLUE  -> active, primary, running
    GREEN        -> success, verified
    AMBER        -> warning, review, pending
    RED / PINK   -> error, failed
    PURPLE       -> enhancement, secondary accent
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# tokens
# ---------------------------------------------------------------------------

#: application background - near-black, very slightly cool
BG = "#04050a"
#: docked panel
SURFACE = "#0a0c12"
#: raised card on a panel
SURFACE_RAISED = "#11141c"
#: hover fill for rows / list items / nav entries
SURFACE_HOVER = "#171b25"
#: pressed / selected fill
SURFACE_ACTIVE = "#1c2130"

#: hairline border
BORDER = "#1d2230"
#: border of a hovered or focused container
BORDER_ACTIVE = "#2c3444"

#: primary copy - bright, high contrast
TEXT_PRIMARY = "#e9edf6"
#: secondary copy - cool gray, still comfortably readable on BG
TEXT_SECONDARY = "#a3adc2"
#: de-emphasised copy - captions, hints
TEXT_MUTED = "#747f95"
#: text on a neon fill
TEXT_ON_ACCENT = "#04050a"

# -- neon accents -----------------------------------------------------------
NEON_CYAN = "#00e5ff"
NEON_BLUE = "#4d9fff"
NEON_GREEN = "#2fe08a"
NEON_YELLOW = "#ffb637"
NEON_RED = "#ff4d6d"
NEON_PURPLE = "#b06cff"

#: dim variants, for borders and de-emphasised accents
CYAN_DIM = "#0b7f93"
GREEN_DIM = "#1b7a4e"
YELLOW_DIM = "#8a6320"
RED_DIM = "#8f2c3e"
PURPLE_DIM = "#5f3a8f"
BLUE_DIM = "#2a5a99"

#: very dark status washes - a tint of the accent over near-black, used as a
#: badge/row background.  Kept extremely dark so panels never read as neon.
WASH_CYAN = "#06161c"
WASH_GREEN = "#06170f"
WASH_YELLOW = "#191206"
WASH_RED = "#1a080c"
WASH_PURPLE = "#130a1e"
WASH_BLUE = "#081020"

#: translucent neon, for selection fills and soft glows
SEL_CYAN = "rgba(0, 229, 255, 0.14)"
SEL_CYAN_SOFT = "rgba(0, 229, 255, 0.07)"

FONT_STACK = "'Segoe UI', 'Inter', 'Roboto', Arial, sans-serif"
FONT_MONO = "'JetBrains Mono', 'Cascadia Mono', 'Consolas', monospace"


# ---------------------------------------------------------------------------
# semantic helpers
# ---------------------------------------------------------------------------

#: status name -> (foreground, wash background, border)
STATUS = {
    "active":   (NEON_CYAN,   WASH_CYAN,   CYAN_DIM),
    "running":  (NEON_CYAN,   WASH_CYAN,   CYAN_DIM),
    "success":  (NEON_GREEN,  WASH_GREEN,  GREEN_DIM),
    "verified": (NEON_GREEN,  WASH_GREEN,  GREEN_DIM),
    "warning":  (NEON_YELLOW, WASH_YELLOW, YELLOW_DIM),
    "review":   (NEON_YELLOW, WASH_YELLOW, YELLOW_DIM),
    "pending":  (TEXT_MUTED,  SURFACE_RAISED, BORDER),
    "error":    (NEON_RED,    WASH_RED,    RED_DIM),
    "failed":   (NEON_RED,    WASH_RED,    RED_DIM),
    "blocked":  (NEON_RED,    WASH_RED,    RED_DIM),
    "secondary": (NEON_PURPLE, WASH_PURPLE, PURPLE_DIM),
}


def badge(status: str) -> str:
    """Qt stylesheet for a small status pill."""
    fg, bg, border = STATUS.get(status, STATUS["pending"])
    return (f"color: {fg}; background: {bg}; border: 1px solid {border};"
            f" border-radius: 9px; padding: 3px 10px;"
            f" font-size: 11px; font-weight: 700; letter-spacing: 0.6px;")


def section_header(accent: str = NEON_CYAN) -> str:
    """One consistent caption style for every panel section.

    Headings stay cool gray - neon is reserved for the thin accent rule, so
    the eye is drawn to DATA and STATUS rather than to six differently
    coloured titles.
    """
    return (f"color: {TEXT_SECONDARY}; background: transparent;"
            f" border: none; border-left: 2px solid {accent};"
            f" padding-left: 9px; margin-top: 2px;"
            f" font-size: 11px; font-weight: 700; letter-spacing: 1.4px;")


def accent_button(accent: str, dim: str) -> str:
    """A restrained neon button: tinted surface, neon text and border."""
    return f"""
        QPushButton {{
            background: {SURFACE_RAISED};
            color: {accent};
            border: 1px solid {dim};
            border-radius: 8px;
            padding: 10px 20px;
            font-size: 12px;
            font-weight: 700;
            letter-spacing: 0.8px;
        }}
        QPushButton:hover {{
            background: {SURFACE_HOVER};
            border: 1px solid {accent};
            color: {accent};
        }}
        QPushButton:pressed {{
            background: {SURFACE_ACTIVE};
        }}
        QPushButton:disabled {{
            background: {SURFACE};
            color: {TEXT_MUTED};
            border: 1px solid {BORDER};
        }}
    """


# ---------------------------------------------------------------------------
# the application stylesheet
# ---------------------------------------------------------------------------

def app_stylesheet() -> str:
    """Global QSS.

    Covers every widget that does not carry its own inline stylesheet, so
    scrollbars, menus, tooltips, dialogs and headers all pick up the theme
    instead of falling back to the platform default (which, on a black
    window, is what makes controls vanish).
    """
    return f"""
    QWidget {{
        background-color: {BG};
        color: {TEXT_PRIMARY};
        font-family: {FONT_STACK};
        font-size: 12px;
    }}

    /* A bare QLabel must not paint the app background over a panel -
       without this, every unstyled label punches a darker rectangle into
       whatever surface it sits on. */
    QLabel {{
        background: transparent;
    }}

    QToolTip {{
        background: {SURFACE_RAISED};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER_ACTIVE};
        padding: 6px 9px;
        border-radius: 6px;
    }}

    /* ---- buttons -------------------------------------------------- */
    QPushButton {{
        background: {SURFACE_RAISED};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER};
        border-radius: 8px;
        padding: 9px 18px;
        font-size: 12px;
        font-weight: 600;
    }}
    QPushButton:hover {{
        background: {SURFACE_HOVER};
        border: 1px solid {BORDER_ACTIVE};
    }}
    QPushButton:pressed {{
        background: {SURFACE_ACTIVE};
    }}
    QPushButton:focus {{
        border: 1px solid {NEON_CYAN};
        outline: none;
    }}
    QPushButton:disabled {{
        background: {SURFACE};
        color: {TEXT_MUTED};
        border: 1px solid {BORDER};
    }}

    /* ---- inputs --------------------------------------------------- */
    QLineEdit, QComboBox, QSpinBox, QPlainTextEdit {{
        background: {SURFACE};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER};
        border-radius: 7px;
        padding: 8px 11px;
        selection-background-color: {SEL_CYAN};
        selection-color: {TEXT_PRIMARY};
    }}
    QLineEdit:hover, QComboBox:hover {{
        border: 1px solid {BORDER_ACTIVE};
    }}
    QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{
        border: 1px solid {NEON_CYAN};
        background: {SURFACE_RAISED};
    }}
    QLineEdit[error="true"] {{
        border: 1px solid {NEON_RED};
    }}
    QLineEdit:disabled, QComboBox:disabled {{
        background: {BG};
        color: {TEXT_MUTED};
        border: 1px solid {BORDER};
    }}
    QComboBox::drop-down {{
        border: none;
        width: 22px;
    }}
    QComboBox::down-arrow {{
        width: 0; height: 0;
        border-left: 4px solid transparent;
        border-right: 4px solid transparent;
        border-top: 5px solid {TEXT_SECONDARY};
        margin-right: 8px;
    }}
    QComboBox QAbstractItemView {{
        background: {SURFACE_RAISED};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER_ACTIVE};
        selection-background-color: {SEL_CYAN};
        selection-color: {NEON_CYAN};
        outline: none;
        padding: 4px;
    }}

    /* ---- tables --------------------------------------------------- */
    QTableWidget, QTableView {{
        background: {SURFACE};
        alternate-background-color: {SURFACE_RAISED};
        gridline-color: {BORDER};
        border: 1px solid {BORDER};
        border-radius: 10px;
        font-size: 12px;
    }}
    QHeaderView::section {{
        background: {SURFACE_RAISED};
        color: {TEXT_SECONDARY};
        font-weight: 700;
        font-size: 11px;
        letter-spacing: 0.8px;
        padding: 10px 8px;
        border: none;
        border-bottom: 1px solid {CYAN_DIM};
    }}
    QTableWidget::item {{
        padding: 7px 6px;
        border-bottom: 1px solid {BORDER};
    }}
    QTableWidget::item:hover {{
        background: {SURFACE_HOVER};
    }}
    QTableWidget::item:selected {{
        background: {SEL_CYAN};
        color: {TEXT_PRIMARY};
    }}
    QTableCornerButton::section {{
        background: {SURFACE_RAISED};
        border: none;
    }}

    /* ---- text / logs ---------------------------------------------- */
    QTextEdit {{
        background: {SURFACE};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER};
        border-radius: 10px;
        selection-background-color: {SEL_CYAN};
    }}

    /* ---- progress -------------------------------------------------- */
    QProgressBar {{
        background: {SURFACE};
        border: 1px solid {BORDER};
        border-radius: 7px;
        text-align: center;
        color: {TEXT_SECONDARY};
        font-size: 11px;
        font-weight: 700;
    }}
    QProgressBar::chunk {{
        background: {NEON_CYAN};
        border-radius: 6px;
    }}

    /* ---- scrollbars ------------------------------------------------ */
    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {BORDER_ACTIVE};
        border-radius: 5px;
        min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {CYAN_DIM};
    }}
    QScrollBar:horizontal {{
        background: transparent;
        height: 10px;
        margin: 2px;
    }}
    QScrollBar::handle:horizontal {{
        background: {BORDER_ACTIVE};
        border-radius: 5px;
        min-width: 30px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: {CYAN_DIM};
    }}
    QScrollBar::add-line, QScrollBar::sub-line {{
        height: 0; width: 0; border: none; background: none;
    }}
    QScrollBar::add-page, QScrollBar::sub-page {{
        background: none;
    }}

    /* ---- containers ------------------------------------------------ */
    QScrollArea {{
        background: transparent;
        border: none;
    }}
    QSplitter::handle {{
        background: {BORDER};
    }}
    QSplitter::handle:hover {{
        background: {CYAN_DIM};
    }}

    /* ---- panels (object names already present in the UI) ----------- */
    QFrame#topBar {{
        background: {SURFACE};
        border: 1px solid {BORDER};
        border-radius: 12px;
    }}
    QFrame#leftPanel, QFrame#rightPanel {{
        background: {SURFACE};
        border: 1px solid {BORDER};
        border-radius: 14px;
    }}

    /* ---- dialogs ---------------------------------------------------- */
    QMessageBox {{
        background: {SURFACE};
    }}
    QMessageBox QLabel {{
        color: {TEXT_PRIMARY};
        background: transparent;
    }}
    """
