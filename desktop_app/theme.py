"""Design tokens + QSS implementing desktop_app/DESIGN.md section 3.

Qt-free: the PyQt6 shell renders these tokens as a Qt stylesheet, while
headless tests assert the values stay in sync with the production spec.
"""

from __future__ import annotations

__all__ = [
    "ACCENTS",
    "PORCELAIN",
    "PORCELAIN_DARK",
    "TAB_DEFS",
    "normalize_accent",
    "normalize_theme",
    "qt_stylesheet",
]

#: Production palette (desktop_app/DESIGN.md 3.1). The ``PORCELAIN`` name is
#: kept for API stability; values follow the slate/indigo/lavender system.
PORCELAIN: dict[str, str] = {
    # Canvas & surfaces
    "bg_app": "#F5F5F7",
    "bg_surface": "#FFFFFF",
    "bg_subtle": "#F9FAFB",
    "bg_nav_active": "#F0EFFF",
    "bg_hover": "#F3F4F6",
    "bg_pressed": "#E9EBEF",
    "bg_disabled": "#E5E7EB",
    # Typography
    "text_heading": "#0F172A",
    "text_serif_hero": "#0A192F",
    "text_body": "#334155",
    "text_muted": "#64748B",
    "text_faint": "#94A3B8",
    "text_link": "#2563EB",
    # Borders
    "border_hairline": "#EAEAEF",
    "border_subtle": "#E2E8F0",
    "border_focus": "#6366F1",
    # Status tints
    "status_green_bg": "#ECFDF5",
    "status_green_text": "#059669",
    "status_purple_bg": "#F3E8FF",
    "status_purple_text": "#7C3AED",
    "status_blue_bg": "#EFF6FF",
    "status_blue_text": "#2563EB",
    "status_neutral_bg": "#F1F5F9",
    "status_neutral_text": "#64748B",
    "status_red_bg": "#FEF2F2",
    "status_red_text": "#DC2626",
    "status_amber_bg": "#FEF3C7",
    "status_amber_text": "#B45309",
    # Interactive accents
    "accent_primary": "#6366F1",
    "accent_primary_hover": "#4F46E5",
    "accent_pill_lavender": "#ECEBFA",
    "nav_active_text": "#4338CA",
}

#: Six production surfaces (desktop_app/DESIGN.md section 2).
#: Each entry: (tab_id, title, subtitle, core modules).

#: Slate-midnight dark palette: same token keys as :data:`PORCELAIN`
#: so every QSS rule below re-themes by switching the token source.
PORCELAIN_DARK: dict[str, str] = {
    "bg_app": "#0F172A",
    "bg_surface": "#1E293B",
    "bg_subtle": "#182339",
    "bg_nav_active": "#1E1B4B",
    "bg_hover": "#273349",
    "bg_pressed": "#334155",
    "bg_disabled": "#334155",
    "text_heading": "#F1F5F9",
    "text_serif_hero": "#F8FAFC",
    "text_body": "#CBD5E1",
    "text_muted": "#94A3B8",
    "text_faint": "#64748B",
    "text_link": "#60A5FA",
    "border_hairline": "#263449",
    "border_subtle": "#334155",
    "border_focus": "#818CF8",
    "status_green_bg": "#064E3B",
    "status_green_text": "#6EE7B7",
    "status_purple_bg": "#4C1D95",
    "status_purple_text": "#D8B4FE",
    "status_blue_bg": "#1E3A8A",
    "status_blue_text": "#93C5FD",
    "status_neutral_bg": "#334155",
    "status_neutral_text": "#CBD5E1",
    "status_red_bg": "#7F1D1D",
    "status_red_text": "#FCA5A5",
    "status_amber_bg": "#78350F",
    "status_amber_text": "#FCD34D",
    "accent_primary": "#818CF8",
    "accent_primary_hover": "#6366F1",
    "accent_pill_lavender": "#312E81",
    "nav_active_text": "#C7D2FE",
}

#: Accent overrides for the five swatches in Settings → Appearance.
#: Each accent ships a light and a dark token set; "Indigo" reproduces
#: the signature spec values exactly.
ACCENTS: dict[str, dict[str, dict[str, str]]] = {
    "Indigo": {
        "light": {
            "accent_primary": "#6366F1",
            "accent_primary_hover": "#4F46E5",
            "accent_pill_lavender": "#ECEBFA",
            "nav_active_text": "#4338CA",
            "bg_nav_active": "#F0EFFF",
            "border_focus": "#6366F1",
        },
        "dark": {
            "accent_primary": "#818CF8",
            "accent_primary_hover": "#6366F1",
            "accent_pill_lavender": "#312E81",
            "nav_active_text": "#C7D2FE",
            "bg_nav_active": "#1E1B4B",
            "border_focus": "#818CF8",
        },
    },
    "Lavender": {
        "light": {
            "accent_primary": "#8B5CF6",
            "accent_primary_hover": "#7C3AED",
            "accent_pill_lavender": "#F3E8FF",
            "nav_active_text": "#6D28D9",
            "bg_nav_active": "#F5F0FF",
            "border_focus": "#8B5CF6",
        },
        "dark": {
            "accent_primary": "#A78BFA",
            "accent_primary_hover": "#8B5CF6",
            "accent_pill_lavender": "#4C1D95",
            "nav_active_text": "#DDD6FE",
            "bg_nav_active": "#2E1065",
            "border_focus": "#A78BFA",
        },
    },
    "Sage": {
        "light": {
            "accent_primary": "#059669",
            "accent_primary_hover": "#047857",
            "accent_pill_lavender": "#D1FAE5",
            "nav_active_text": "#065F46",
            "bg_nav_active": "#ECFDF5",
            "border_focus": "#059669",
        },
        "dark": {
            "accent_primary": "#34D399",
            "accent_primary_hover": "#10B981",
            "accent_pill_lavender": "#064E3B",
            "nav_active_text": "#A7F3D0",
            "bg_nav_active": "#022C22",
            "border_focus": "#34D399",
        },
    },
    "Sky": {
        "light": {
            "accent_primary": "#2563EB",
            "accent_primary_hover": "#1D4ED8",
            "accent_pill_lavender": "#DBEAFE",
            "nav_active_text": "#1E40AF",
            "bg_nav_active": "#EFF6FF",
            "border_focus": "#2563EB",
        },
        "dark": {
            "accent_primary": "#60A5FA",
            "accent_primary_hover": "#3B82F6",
            "accent_pill_lavender": "#1E3A8A",
            "nav_active_text": "#BFDBFE",
            "bg_nav_active": "#172554",
            "border_focus": "#60A5FA",
        },
    },
    "Amber": {
        "light": {
            "accent_primary": "#D97706",
            "accent_primary_hover": "#B45309",
            "accent_pill_lavender": "#FEF3C7",
            "nav_active_text": "#92400E",
            "bg_nav_active": "#FFFBEB",
            "border_focus": "#D97706",
        },
        "dark": {
            "accent_primary": "#FBBF24",
            "accent_primary_hover": "#F59E0B",
            "accent_pill_lavender": "#78350F",
            "nav_active_text": "#FDE68A",
            "bg_nav_active": "#451A03",
            "border_focus": "#FBBF24",
        },
    },
}


def normalize_theme(theme: str) -> str:
    """Map a theme label or id to ``"light"`` or ``"dark"``."""
    text = (theme or "").strip().lower()
    if "dark" in text or "midnight" in text or "slate" in text:
        return "dark"
    return "light"


def normalize_accent(accent: str) -> str:
    """Map an accent name to a known swatch (default ``"Indigo"``)."""
    text = (accent or "").strip().lower()
    for name in ACCENTS:
        if name.lower() == text:
            return name
    return "Indigo"
TAB_DEFS: tuple[tuple[str, str, str, str], ...] = (
    ("home", "Home", "Ambient status and instant exploration.",
     "mimir.ui.visor"),
    ("observe", "Observe", "Telemetry control and session history.",
     "mimir.telemetry, mimir.episodes, mimir.detective"),
    ("memory", "Memory", "Principle bank with evidence review.",
     "mimir.store, mimir.principles"),
    ("replicate", "Replicate", "Synthesis in your authentic style.",
     "mimir.actor, mimir.store"),
    ("trust", "Trust", "Boundaries, storage, and data purging.",
     "mimir.telemetry, mimir.store"),
    ("settings", "Settings", "Persona, voice, model, appearance.",
     "desktop_app.prefs"),
)


def qt_stylesheet(theme: str = "light", accent: str = "Indigo") -> str:
    """Render the production tokens as a Qt stylesheet string.

    Object names follow desktop_app/DESIGN.md section 6. Every bordered
    control is rounded; status is carried by soft tinted badges.
    ``theme`` selects the ``"light"`` (Porcelain) or ``"dark"`` (Slate
    Midnight) palette; ``accent`` selects one of the :data:`ACCENTS`
    swatches. Defaults reproduce the signature spec exactly.
    """
    dark = normalize_theme(theme) == "dark"
    base = PORCELAIN_DARK if dark else PORCELAIN
    p = dict(base)
    p.update(ACCENTS[normalize_accent(accent)][
        "dark" if dark else "light"])
    return f"""
* {{
  font-family: "Barlow Semi Condensed", "Segoe UI", -apple-system, BlinkMacSystemFont, Roboto, "Helvetica Neue", Arial, sans-serif;
}}
QMainWindow, QWidget#root {{ background: {p["bg_app"]}; }}
QWidget#card {{
  background: {p["bg_surface"]};
  border: 1px solid {p["border_hairline"]};
  border-radius: 16px;
}}
QLabel#h1 {{
  font-family: "Barlow Semi Condensed", "Segoe UI", sans-serif;
  font-size: 26px;
  font-weight: 700;
  color: {p["text_heading"]};
}}
QLabel#h2 {{
  font-family: "Barlow Semi Condensed", "Segoe UI", sans-serif;
  font-size: 16px;
  font-weight: 700;
  color: {p["text_heading"]};
}}
QLabel#statvalue {{
  font-family: "Bebas Neue", "Barlow Semi Condensed", sans-serif;
  font-size: 28px;
  font-weight: 400;
  letter-spacing: 0.5px;
  color: {p["text_heading"]};
}}
QLabel#lede, QLabel#subtle {{ color: {p["text_body"]}; font-size: 14px; }}
QLabel#muted {{ color: {p["text_muted"]}; font-size: 12px; }}
QLabel#faint {{ color: {p["text_faint"]}; font-size: 12px; font-weight: 600; }}
QLabel#ok {{ color: {p["status_green_text"]}; font-weight: 600; }}
QLabel#err {{ color: {p["status_red_text"]}; font-weight: 600; }}
QLabel#brand {{
  font-family: "Caprasimo", "Cooper Black", "Fraunces", Georgia, serif;
  font-size: 22px;
  font-weight: 400;
  letter-spacing: -0.3px;
  color: {p["text_heading"]};
}}
QLabel#hero_overline {{
  font-family: "Bebas Neue", "Barlow Semi Condensed", sans-serif;
  font-size: 14px;
  font-weight: 400;
  letter-spacing: 1.5px;
  color: {p["text_muted"]};
}}
QLabel#hero_name {{
  font-family: "Caprasimo", "Cooper Black", "Fraunces", Georgia, serif;
  font-size: 34px;
  font-weight: 400;
  color: {p["text_serif_hero"]};
}}
QLabel#overline {{
  font-family: "Bebas Neue", "Barlow Semi Condensed", sans-serif;
  font-size: 13px;
  font-weight: 400;
  letter-spacing: 1.2px;
  color: {p["text_muted"]};
}}
QPushButton#ink {{
  background: {p["text_heading"]}; color: white; border: 0;
  border-radius: 999px; padding: 8px 20px; font-weight: 600; min-height: 22px;
}}
QPushButton#ink:hover {{ background: {p["text_body"]}; }}
QPushButton#ink:pressed {{ background: #020617; }}
QPushButton#ink:disabled {{ background: {p["bg_disabled"]}; color: {p["text_faint"]}; }}
QPushButton#soft {{
  background: {p["bg_surface"]}; color: {p["text_heading"]};
  border: 1px solid {p["border_subtle"]};
  border-radius: 999px; padding: 7px 18px; font-weight: 500; font-size: 12px; min-height: 22px;
}}
QPushButton#soft:hover {{ border-color: {p["border_focus"]}; background: {p["bg_hover"]}; }}
QPushButton#soft:pressed {{ background: {p["bg_pressed"]}; }}
QPushButton#soft:disabled {{
  background: {p["bg_subtle"]}; color: {p["text_faint"]};
  border-color: {p["border_hairline"]};
}}
QPushButton#generate_btn {{
  background: {p["accent_primary"]}; color: white; border: 0;
  border-radius: 8px; padding: 10px 24px; font-weight: 600; font-size: 13px;
  min-height: 24px;
}}
QPushButton#generate_btn:hover {{ background: {p["accent_primary_hover"]}; }}
QPushButton#generate_btn:pressed {{ background: {p["nav_active_text"]}; }}
QPushButton#generate_btn:disabled {{
  background: {p["bg_disabled"]}; color: {p["text_faint"]};
}}
QPushButton#chip {{
  background: {p["bg_surface"]}; color: {p["text_body"]};
  border: 1px solid {p["border_hairline"]};
  border-radius: 999px; padding: 6px 14px; font-size: 12px; font-weight: 500;
}}
QPushButton#chip:hover {{ border-color: {p["border_subtle"]}; color: {p["text_heading"]}; background: {p["bg_hover"]}; }}
QPushButton#nav_btn {{
  background: transparent; color: {p["text_muted"]}; border: 0;
  border-radius: 10px; padding: 9px 14px; font-weight: 500; font-size: 13px;
  text-align: left; min-height: 24px;
}}
QPushButton#nav_btn:hover {{ background: {p["bg_hover"]}; color: {p["text_heading"]}; }}
QPushButton#nav_btn:checked {{
  background: {p["bg_nav_active"]}; color: {p["nav_active_text"]};
  font-weight: 600;
}}
QPushButton#segment {{
  background: transparent; color: {p["text_muted"]}; border: 0;
  border-radius: 999px; padding: 7px 22px; font-weight: 600; font-size: 13px;
}}
QPushButton#segment:checked {{
  background: {p["bg_surface"]}; color: {p["text_heading"]};
  border: 1px solid {p["border_hairline"]};
}}
QWidget#segment_bar {{
  background: {p["bg_hover"]}; border-radius: 999px;
}}
QWidget#sidebar {{
  background: {p["bg_app"]};
  border-right: 1px solid {p["border_hairline"]};
}}
QFrame#separator {{ background: {p["border_hairline"]}; border: 0; }}
QScrollArea {{ background: transparent; border: 0; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QStackedWidget {{ background: transparent; border: 0; }}
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
  background: {p["bg_surface"]}; color: {p["text_body"]};
  border: 1px solid {p["border_subtle"]}; border-radius: 10px;
  padding: 6px 10px; selection-background-color: {p["status_blue_bg"]};
  min-height: 22px;
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
  border: 1px solid {p["border_focus"]};
}}
QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled,
QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{
  background: {p["bg_subtle"]}; color: {p["text_faint"]};
}}
QLineEdit#explore_bar {{
  border-radius: 999px; padding: 12px 20px; font-size: 14px;
  border: 1px solid {p["border_hairline"]};
}}
QFrame#explore_pill {{
  background: {p["bg_surface"]};
  border: 1px solid {p["border_hairline"]};
  border-radius: 999px;
  padding: 4px 8px 4px 18px;
}}
QLineEdit#explore_input {{
  background: transparent;
  border: 0;
  font-size: 14px;
  color: {p["text_body"]};
  padding: 6px 0px;
}}
QFrame#row_divider {{
  background: {p["border_hairline"]};
  border: 0;
  max-height: 1px;
  min-height: 1px;
}}
QTextEdit#task_input {{
  background: transparent;
  border: 0;
  padding: 8px 0px;
  font-size: 14px;
  color: {p["text_body"]};
}}
QComboBox::drop-down {{ border: 0; width: 26px; }}
QComboBox QAbstractItemView {{
  background: {p["bg_surface"]}; color: {p["text_body"]};
  border: 1px solid {p["border_subtle"]}; border-radius: 10px;
  selection-background-color: {p["status_blue_bg"]};
  selection-color: {p["text_heading"]}; outline: 0;
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
  border: 0; background: transparent; width: 22px;
}}
QCheckBox#toggle_switch {{ spacing: 0; }}
QCheckBox#toggle_switch::indicator {{
  width: 44px; height: 26px; border-radius: 13px;
  background: {p["border_subtle"]};
}}
QCheckBox#toggle_switch::indicator:checked {{
  background: {p["accent_primary"]};
}}
QCheckBox#toggle_switch::indicator:disabled {{
  background: {p["bg_disabled"]};
}}
QListWidget {{
  background: {p["bg_surface"]}; color: {p["text_body"]};
  border: 1px solid {p["border_hairline"]}; border-radius: 12px;
  padding: 4px; outline: 0;
}}
QListWidget::item {{ border-radius: 8px; padding: 6px 8px; }}
QListWidget::item:selected {{
  background: {p["status_blue_bg"]}; color: {p["text_heading"]};
}}
QListWidget::item:hover {{ background: {p["bg_hover"]}; }}
QListWidget#settings_nav {{
  background: {p["bg_surface"]};
  border: 1px solid {p["border_hairline"]};
  border-radius: 16px;
  padding: 8px;
}}
QListWidget#settings_nav::item {{
  border-radius: 10px; padding: 12px 14px; color: {p["text_muted"]};
  font-weight: 500; font-size: 13px; margin-bottom: 4px;
}}
QListWidget#settings_nav::item:hover {{
  background: {p["bg_hover"]}; color: {p["text_heading"]};
}}
QListWidget#settings_nav::item:selected {{
  background: {p["bg_nav_active"]}; color: {p["nav_active_text"]};
  font-weight: 600;
}}
QFrame#guide_banner {{
  background: {p["bg_surface"]};
  border: 1px solid {p["border_hairline"]};
  border-radius: 16px;
}}
QLabel#guide_title {{
  font-size: 15px; font-weight: 700; color: {p["text_heading"]};
}}
QLabel#guide_desc {{
  font-size: 13px; color: {p["text_muted"]};
}}
QLabel#setting_label {{
  font-size: 13px; font-weight: 600; color: {p["text_heading"]};
}}
QLabel#setting_help {{
  font-size: 12px; color: {p["text_muted"]};
}}
QFrame#setting_row_box {{
  background: transparent; border: 0;
}}
QFrame#status_card {{
  background: {p["bg_surface"]};
  border: 1px solid {p["border_hairline"]};
  border-radius: 16px;
}}
QLabel#status_dot {{
  background: {p["text_faint"]}; border-radius: 5px;
  min-width: 10px; max-width: 10px; min-height: 10px; max-height: 10px;
}}
QLabel#status_dot_on {{
  background: {p["status_green_text"]}; border-radius: 5px;
  min-width: 10px; max-width: 10px; min-height: 10px; max-height: 10px;
}}
QFrame#kpi_card {{
  background: {p["bg_surface"]};
  border: 1px solid {p["border_hairline"]};
  border-radius: 16px;
}}
QLabel#kpi_number {{
  font-size: 22px; font-weight: 700; color: {p["text_heading"]};
}}
QFrame#session_row, QFrame#principle_card, QFrame#task_row, QFrame#scope_row, QFrame#action_card {{
  background: {p["bg_surface"]};
  border: 1px solid {p["border_hairline"]};
  border-radius: 16px;
}}
QFrame#session_row:hover, QFrame#principle_card:hover, QFrame#task_row:hover,
QFrame#scope_row:hover, QFrame#action_card:hover, QFrame#kpi_card:hover {{
  border-color: {p["border_subtle"]};
}}
QLabel#icon_box_purple {{
  background: {p["status_purple_bg"]}; color: {p["status_purple_text"]};
  border-radius: 10px; font-size: 15px; font-weight: 600;
  min-width: 38px; max-width: 38px; min-height: 38px; max-height: 38px;
  qproperty-alignment: AlignCenter;
}}
QLabel#icon_box_blue {{
  background: {p["status_blue_bg"]}; color: {p["status_blue_text"]};
  border-radius: 10px; font-size: 15px; font-weight: 600;
  min-width: 38px; max-width: 38px; min-height: 38px; max-height: 38px;
  qproperty-alignment: AlignCenter;
}}
QLabel#icon_box_green {{
  background: {p["status_green_bg"]}; color: {p["status_green_text"]};
  border-radius: 10px; font-size: 15px; font-weight: 600;
  min-width: 38px; max-width: 38px; min-height: 38px; max-height: 38px;
  qproperty-alignment: AlignCenter;
}}
QLabel#icon_box_amber {{
  background: {p["status_amber_bg"]}; color: {p["status_amber_text"]};
  border-radius: 10px; font-size: 15px; font-weight: 600;
  min-width: 38px; max-width: 38px; min-height: 38px; max-height: 38px;
  qproperty-alignment: AlignCenter;
}}
QLabel#icon_box_neutral {{
  background: {p["status_neutral_bg"]}; color: {p["status_neutral_text"]};
  border-radius: 10px; font-size: 15px; font-weight: 600;
  min-width: 38px; max-width: 38px; min-height: 38px; max-height: 38px;
  qproperty-alignment: AlignCenter;
}}
QPushButton#mic_btn {{
  background: {p["bg_nav_active"]}; color: {p["nav_active_text"]};
  border: 0; border-radius: 18px; font-size: 14px;
  min-width: 36px; max-width: 36px; min-height: 36px; max-height: 36px;
}}
QPushButton#mic_btn:hover {{ background: {p["accent_pill_lavender"]}; }}
QPushButton#start_obs_btn {{
  background: {p["bg_nav_active"]}; color: {p["nav_active_text"]};
  border: 0; border-radius: 999px; padding: 9px 20px; font-weight: 600; font-size: 13px;
  min-height: 22px;
}}
QPushButton#start_obs_btn:hover {{ background: {p["accent_pill_lavender"]}; }}
QPushButton#clear_btn {{
  background: {p["status_red_bg"]}; color: {p["status_red_text"]};
  border: 1px solid rgba(220, 38, 38, 0.2);
  border-radius: 999px; padding: 7px 18px; font-weight: 600; font-size: 13px;
}}
QPushButton#clear_btn:hover {{ background: #FEE2E2; }}
QLabel#badge {{
  border-radius: 999px; padding: 4px 12px; font-weight: 600; font-size: 12px;
}}
QLabel#badge_processed, QLabel#badge_completed, QLabel#badge_confirmed {{
  background: {p["status_green_bg"]}; color: {p["status_green_text"]};
  border-radius: 999px; padding: 4px 12px; font-weight: 600; font-size: 12px;
}}
QLabel#badge_analyzed, QLabel#badge_learning, QLabel#badge_reviewed {{
  background: {p["status_blue_bg"]}; color: {p["status_blue_text"]};
  border-radius: 999px; padding: 4px 12px; font-weight: 600; font-size: 12px;
}}
QLabel#badge_ignored, QLabel#badge_unconfirmed {{
  background: {p["status_neutral_bg"]}; color: {p["status_neutral_text"]};
  border-radius: 999px; padding: 4px 12px; font-weight: 600; font-size: 12px;
}}
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:horizontal {{ width: 8px; }}
QSplitter::handle:vertical {{ height: 8px; }}
QScrollBar:vertical {{
  background: transparent; width: 10px; margin: 2px; border: 0;
}}
QScrollBar::handle:vertical {{
  background: rgba(15, 23, 42, 0.16); min-height: 30px; border-radius: 4px;
}}
QScrollBar::handle:vertical:hover {{ background: rgba(15, 23, 42, 0.28); }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
  height: 0; background: none; border: 0;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
  background: none;
}}
QScrollBar:horizontal {{
  background: transparent; height: 10px; margin: 2px; border: 0;
}}
QScrollBar::handle:horizontal {{
  background: rgba(15, 23, 42, 0.16); min-width: 30px; border-radius: 4px;
}}
QScrollBar::handle:horizontal:hover {{ background: rgba(15, 23, 42, 0.28); }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
  width: 0; background: none; border: 0;
}}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
  background: none;
}}
QStatusBar {{ color: {p["text_muted"]}; font-size: 11px; }}
QToolTip {{
  background: {p["bg_surface"]}; color: {p["text_body"]};
  border: 1px solid {p["border_subtle"]}; border-radius: 8px; padding: 4px 8px;
}}
"""
