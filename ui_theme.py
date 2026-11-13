"""Central design system for the CGHS Automation Console.

PRESENTATION ONLY.  This module defines colour, type and widget styling.  It
contains no business logic, performs no I/O and must never import from the
``cghs`` package - a styling change can therefore never alter extraction,
code resolution, quantities, enhancement behaviour or timing.

THE APPLICATION NAME, WINDOW TITLE, EXECUTABLE NAME AND PRODUCT IDENTITY ARE
NOT DEFINED HERE AND ARE NOT CHANGED BY THIS MODULE.

------------------------------------------------------------------------
PALETTE - the three brand colours, used exactly as specified
------------------------------------------------------------------------
    ICE SILVER       #DDE3E8   backgrounds, surfaces, panels, workspace
    DEEP AUBERGINE   #3A0F34   sidebar, navigation, brand, primary buttons
    MARIGOLD         #EAAA00   active selection, highlight, focus, emphasis

Everything else is a neutral grey, a readable dark text tone, or a muted
semantic status colour chosen to sit calmly beside those three.  The previous
neon-on-black console palette is retired; the legacy ``NEON_*`` names are kept
as aliases so no widget reference breaks, but they now resolve to the
professional palette.
"""

# ---------------------------------------------------------------------------
# brand
# ---------------------------------------------------------------------------
ICE_SILVER = "#DDE3E8"
DEEP_AUBERGINE = "#3A0F34"
MARIGOLD = "#EAAA00"

# ---------------------------------------------------------------------------
# surfaces - layered from the page background upward
# ---------------------------------------------------------------------------
BG = ICE_SILVER                 #: application background / workspace
SURFACE = "#FFFFFF"             #: cards, panels, tables
SURFACE_RAISED = "#F4F7F9"      #: headers, toolbars, table headers
SURFACE_HOVER = "#E7ECF0"       #: hover state on a light surface
SURFACE_ACTIVE = "#D2DAE1"      #: pressed / selected light surface
SURFACE_SUNKEN = "#C9D3DA"      #: wells, log background, track

#: sidebar / navigation shades, derived from the brand aubergine
SIDEBAR = DEEP_AUBERGINE
SIDEBAR_HOVER = "#4D1746"
SIDEBAR_ACTIVE = "#5C1D54"
SIDEBAR_BORDER = "#2A0A26"

# ---------------------------------------------------------------------------
# borders
# ---------------------------------------------------------------------------
BORDER = "#C2CCD4"
BORDER_STRONG = "#A9B6C0"
BORDER_ACTIVE = MARIGOLD

# ---------------------------------------------------------------------------
# text
# ---------------------------------------------------------------------------
TEXT_PRIMARY = "#1C2126"        #: body text on light surfaces
TEXT_SECONDARY = "#55606B"      #: labels, secondary information
TEXT_MUTED = "#7E8A95"          #: hints, disabled, timestamps
TEXT_ON_DARK = "#F2EFF1"        #: text on the aubergine sidebar
TEXT_ON_DARK_MUTED = "#C3AFBE"  #: secondary text on aubergine
TEXT_ON_ACCENT = "#2A1A00"      #: text on marigold
TEXT_ON_PRIMARY = "#F7F4F6"     #: text on aubergine buttons

# ---------------------------------------------------------------------------
# semantic status - muted, professional, never neon
# ---------------------------------------------------------------------------
STATUS_OK = "#1F7A4D"
STATUS_WARN = "#A8700A"
STATUS_ERROR = "#A32638"
STATUS_INFO = "#2A5E8C"
STATUS_PENDING = TEXT_MUTED

STATUS_OK_WASH = "#E3F1EA"
STATUS_WARN_WASH = "#FBF0D8"
STATUS_ERROR_WASH = "#F8E4E7"
STATUS_INFO_WASH = "#E4EDF5"
ACCENT_WASH = "#FCF2D6"         #: marigold at low intensity

# ---------------------------------------------------------------------------
# semantic tokens - what widgets should reference
# ---------------------------------------------------------------------------
BRAND = DEEP_AUBERGINE          #: brand/identity accents, section headers
ACCENT = MARIGOLD               #: selection, focus, emphasis
SURFACE_PANEL = SURFACE         #: a plain card/panel face
SURFACE_OVERLAY = SURFACE_RAISED  #: an overlay sitting above a panel
HIGHLIGHT_WASH = ACCENT_WASH    #: marigold at low intensity


# ---------------------------------------------------------------------------
# legacy aliases - kept so existing widget code keeps working unchanged
# ---------------------------------------------------------------------------
NEON_CYAN = DEEP_AUBERGINE
NEON_BLUE = STATUS_INFO
NEON_GREEN = STATUS_OK
NEON_YELLOW = MARIGOLD
NEON_RED = STATUS_ERROR
NEON_PURPLE = DEEP_AUBERGINE
CYAN_DIM = SIDEBAR_HOVER
BLUE_DIM = STATUS_INFO
GREEN_DIM = STATUS_OK
YELLOW_DIM = STATUS_WARN
RED_DIM = STATUS_ERROR
PURPLE_DIM = SIDEBAR_HOVER
WASH_CYAN = ACCENT_WASH
WASH_BLUE = STATUS_INFO_WASH
WASH_GREEN = STATUS_OK_WASH
WASH_YELLOW = STATUS_WARN_WASH
WASH_RED = STATUS_ERROR_WASH
WASH_PURPLE = ACCENT_WASH
SEL_CYAN = MARIGOLD
SEL_CYAN_SOFT = ACCENT_WASH
STATUS = STATUS_INFO
PRESENTATION = True

# ---------------------------------------------------------------------------
# typography
# ---------------------------------------------------------------------------
FONT_STACK = "'Segoe UI', 'Inter', 'Helvetica Neue', Arial, sans-serif"
FONT_MONO = "'Cascadia Mono', 'Consolas', 'SF Mono', 'Menlo', monospace"

FONT_SIZE_XS = "10px"
FONT_SIZE_SM = "11px"
FONT_SIZE_MD = "12px"
FONT_SIZE_LG = "14px"
FONT_SIZE_XL = "17px"

RADIUS_SM = "4px"
RADIUS_MD = "6px"
RADIUS_LG = "8px"


# ---------------------------------------------------------------------------
# helpers used by individual widgets
# ---------------------------------------------------------------------------
def section_header(accent=None):
    """Styling for a small uppercase section title."""
    accent = accent or TEXT_SECONDARY
    return (
        "font-size: %s; font-weight: 700; color: %s; letter-spacing: 1px; "
        "text-transform: uppercase; background: transparent; border: none;"
        % (FONT_SIZE_SM, accent))


def accent_button(colour=None):
    """A filled call-to-action button."""
    colour = colour or DEEP_AUBERGINE
    on_colour = TEXT_ON_ACCENT if colour == MARIGOLD else TEXT_ON_PRIMARY
    return (
        "QPushButton {"
        " background: %s; color: %s; border: none; border-radius: %s;"
        " padding: 9px 18px; font-size: %s; font-weight: 600;"
        " letter-spacing: 0.3px; }"
        "QPushButton:hover { background: %s; }"
        "QPushButton:pressed { background: %s; }"
        "QPushButton:disabled { background: %s; color: %s; }"
        % (colour, on_colour, RADIUS_MD, FONT_SIZE_MD,
           SIDEBAR_HOVER if colour == DEEP_AUBERGINE else "#D59B00",
           SIDEBAR_ACTIVE if colour == DEEP_AUBERGINE else "#C08C00",
           SURFACE_ACTIVE, TEXT_MUTED))


def badge(colour=None, wash=None):
    """A small status pill."""
    colour = colour or STATUS_INFO
    wash = wash or STATUS_INFO_WASH
    return (
        "background: %s; color: %s; border: 1px solid %s;"
        " border-radius: %s; padding: 2px 9px; font-size: %s;"
        " font-weight: 700; letter-spacing: 0.4px;"
        % (wash, colour, colour, RADIUS_SM, FONT_SIZE_XS))


def status_colour(state):
    """Map a connection/processing state to its semantic colour."""
    state = (state or "").upper()
    if state in ("CONNECTED", "READY", "TREATMENT PLAN READY", "COMPLETED",
                 "SUCCESS", "VERIFIED"):
        return STATUS_OK
    if state in ("CONNECTION LOST", "CHECK FAILED", "FAILED", "ERROR",
                 "CONNECTION_FAILED"):
        return STATUS_ERROR
    if state in ("CDP ENDPOINT VERIFIED", "CHROME PROCESS AVAILABLE",
                 "SELENIUM ATTACHED", "PORTAL TAB FOUND", "PATIENT VERIFIED"):
        # Real, proven progress - but NOT yet "everything is ready".
        return STATUS_INFO
    if state in ("NOT STARTED", "STARTING", "CHECKING CONNECTION",
                 "CONNECTION SLOW", "RECONNECTING", "PORTAL TAB NOT FOUND",
                 "PATIENT VERIFICATION REQUIRED", "OPERATOR ACTION REQUIRED",
                 "RECONCILIATION_REQUIRED"):
        return STATUS_WARN
    return TEXT_SECONDARY


def app_stylesheet():
    """The single global stylesheet for the whole console."""
    return """
    QWidget {
        background: %(bg)s;
        color: %(text)s;
        font-family: %(font)s;
        font-size: %(md)s;
    }

    QFrame#Card, QFrame[card="true"] {
        background: %(surface)s;
        border: 1px solid %(border)s;
        border-radius: %(radius_lg)s;
    }

    QFrame#Sidebar {
        background: %(sidebar)s;
        border: none;
        border-right: 1px solid %(sidebar_border)s;
    }
    QFrame#Sidebar QLabel { color: %(on_dark)s; background: transparent; }

    QFrame#Header {
        background: %(sidebar)s;
        border: none;
        border-bottom: 1px solid %(sidebar_border)s;
    }
    QFrame#Header QLabel { color: %(on_dark)s; background: transparent; }

    QLabel { background: transparent; border: none; color: %(text)s; }
    QLabel[muted="true"] { color: %(muted)s; }

    /* ---------------- buttons ---------------- */
    QPushButton {
        background: %(surface)s;
        color: %(text)s;
        border: 1px solid %(border_strong)s;
        border-radius: %(radius_md)s;
        padding: 8px 16px;
        font-size: %(md)s;
        font-weight: 600;
    }
    QPushButton:hover   { background: %(hover)s; border-color: %(marigold)s; }
    QPushButton:pressed { background: %(active)s; }
    QPushButton:disabled{ background: %(active)s; color: %(muted)s;
                          border-color: %(border)s; }

    QPushButton[primary="true"] {
        background: %(aubergine)s; color: %(on_primary)s; border: none;
    }
    QPushButton[primary="true"]:hover   { background: %(sidebar_hover)s; }
    QPushButton[primary="true"]:pressed { background: %(sidebar_active)s; }
    QPushButton[primary="true"]:disabled{ background: %(active)s;
                                          color: %(muted)s; }

    QPushButton[accent="true"] {
        background: %(marigold)s; color: %(on_accent)s; border: none;
    }
    QPushButton[accent="true"]:hover   { background: #D59B00; }
    QPushButton[accent="true"]:pressed { background: #C08C00; }

    /* ---------------- inputs ---------------- */
    QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextEdit {
        background: %(surface)s;
        border: 1px solid %(border_strong)s;
        border-radius: %(radius_md)s;
        padding: 6px 10px;
        color: %(text)s;
        selection-background-color: %(marigold)s;
        selection-color: %(on_accent)s;
    }
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus,
    QPlainTextEdit:focus, QTextEdit:focus {
        border: 1px solid %(marigold)s;
    }
    QComboBox::drop-down { border: none; width: 22px; }
    QComboBox QAbstractItemView {
        background: %(surface)s;
        border: 1px solid %(border_strong)s;
        selection-background-color: %(accent_wash)s;
        selection-color: %(text)s;
        outline: none;
    }

    /* ---------------- lists & tables ---------------- */
    QListWidget, QTableWidget, QTreeWidget {
        background: %(surface)s;
        border: 1px solid %(border)s;
        border-radius: %(radius_md)s;
        gridline-color: %(border)s;
        alternate-background-color: %(raised)s;
        outline: none;
    }
    QListWidget::item, QTableWidget::item {
        padding: 6px 8px;
        border-bottom: 1px solid %(border)s;
    }
    QListWidget::item:hover, QTableWidget::item:hover {
        background: %(hover)s;
    }
    QListWidget::item:selected, QTableWidget::item:selected {
        background: %(accent_wash)s;
        color: %(text)s;
        border-left: 3px solid %(marigold)s;
    }
    QHeaderView::section {
        background: %(raised)s;
        color: %(secondary)s;
        border: none;
        border-bottom: 1px solid %(border_strong)s;
        border-right: 1px solid %(border)s;
        padding: 8px;
        font-size: %(sm)s;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }
    QTableCornerButton::section { background: %(raised)s; border: none; }

    /* ---------------- tabs ---------------- */
    QTabWidget::pane {
        border: 1px solid %(border)s;
        border-radius: %(radius_md)s;
        background: %(surface)s;
        top: -1px;
    }
    QTabBar::tab {
        background: transparent;
        color: %(secondary)s;
        padding: 9px 18px;
        margin-right: 2px;
        border: none;
        border-bottom: 2px solid transparent;
        font-weight: 600;
    }
    QTabBar::tab:hover     { color: %(text)s; }
    QTabBar::tab:selected  { color: %(aubergine)s;
                             border-bottom: 2px solid %(marigold)s; }

    /* ---------------- progress ---------------- */
    QProgressBar {
        background: %(sunken)s;
        border: none;
        border-radius: %(radius_sm)s;
        height: 8px;
        text-align: center;
        color: %(text)s;
    }
    QProgressBar::chunk {
        background: %(marigold)s;
        border-radius: %(radius_sm)s;
    }

    /* ---------------- scrollbars ---------------- */
    QScrollBar:vertical {
        background: transparent; width: 11px; margin: 2px;
    }
    QScrollBar::handle:vertical {
        background: %(border_strong)s; border-radius: 5px; min-height: 28px;
    }
    QScrollBar::handle:vertical:hover { background: %(secondary)s; }
    QScrollBar:horizontal {
        background: transparent; height: 11px; margin: 2px;
    }
    QScrollBar::handle:horizontal {
        background: %(border_strong)s; border-radius: 5px; min-width: 28px;
    }
    QScrollBar::handle:horizontal:hover { background: %(secondary)s; }
    QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
    QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

    /* ---------------- misc ---------------- */
    QCheckBox, QRadioButton { spacing: 7px; color: %(text)s; }
    QCheckBox::indicator, QRadioButton::indicator {
        width: 15px; height: 15px;
        border: 1px solid %(border_strong)s;
        background: %(surface)s;
        border-radius: 3px;
    }
    QCheckBox::indicator:checked, QRadioButton::indicator:checked {
        background: %(marigold)s; border-color: %(marigold)s;
    }
    QToolTip {
        background: %(aubergine)s;
        color: %(on_primary)s;
        border: 1px solid %(sidebar_border)s;
        border-radius: %(radius_sm)s;
        padding: 5px 9px;
    }
    QMenu {
        background: %(surface)s;
        border: 1px solid %(border_strong)s;
        border-radius: %(radius_md)s;
        padding: 4px;
    }
    QMenu::item { padding: 6px 22px; border-radius: %(radius_sm)s; }
    QMenu::item:selected { background: %(accent_wash)s; color: %(text)s; }
    QSplitter::handle { background: %(border)s; }
    """ % {
        "bg": BG, "surface": SURFACE, "raised": SURFACE_RAISED,
        "hover": SURFACE_HOVER, "active": SURFACE_ACTIVE,
        "sunken": SURFACE_SUNKEN,
        "sidebar": SIDEBAR, "sidebar_hover": SIDEBAR_HOVER,
        "sidebar_active": SIDEBAR_ACTIVE, "sidebar_border": SIDEBAR_BORDER,
        "border": BORDER, "border_strong": BORDER_STRONG,
        "text": TEXT_PRIMARY, "secondary": TEXT_SECONDARY, "muted": TEXT_MUTED,
        "on_dark": TEXT_ON_DARK, "on_accent": TEXT_ON_ACCENT,
        "on_primary": TEXT_ON_PRIMARY,
        "aubergine": DEEP_AUBERGINE, "marigold": MARIGOLD,
        "accent_wash": ACCENT_WASH,
        "font": FONT_STACK, "sm": FONT_SIZE_SM, "md": FONT_SIZE_MD,
        "radius_sm": RADIUS_SM, "radius_md": RADIUS_MD, "radius_lg": RADIUS_LG,
    }
