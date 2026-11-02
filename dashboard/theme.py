"""Institutional Assurance theme for the MIMIR Control Tower.

Exact styling tokens and layout overrides matching the design mockups in
`desktop_app/assets/ui/*`:
- Surfaces: #faf8ff, #f2f3ff, #eaedff, #ffffff
- Primaries: #2a14b4, #4338ca, #c1beff
- Secondaries: #006a63, #9cf2e8, #99efe5
- Tertiaries: #5c2f00, #ffb477, #ffdcc3
- Dark surfaces: #283044, #131b2e
- Responsive collapsible & expandable sidebar controls
"""

from __future__ import annotations

import streamlit as st

__all__ = ["inject_theme"]


def inject_theme() -> None:
    """Apply the complete Institutional Assurance stylesheet to Streamlit."""
    st.markdown(
        """
        <link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200" rel="stylesheet" />
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet" />

        <style>
        /* Base Global Reset */
        html, body, [class*="css"], .stApp {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif !important;
            background-color: #faf8ff !important;
            color: #131b2e !important;
        }

        /* Material Symbols Outlined Icon Helper */
        .material-symbols-outlined {
            font-family: 'Material Symbols Outlined' !important;
            font-weight: normal;
            font-style: normal;
            font-size: 20px;
            line-height: 1;
            letter-spacing: normal;
            text-transform: none;
            display: inline-block;
            white-space: nowrap;
            word-wrap: normal;
            direction: ltr;
            vertical-align: middle;
            -webkit-font-feature-settings: 'liga';
            -webkit-font-smoothing: antialiased;
        }

        /* ==============================================================
           SIDEBAR CONTROLS & EXPAND / COLLAPSE TOGGLES
           Streamlit 1.66 uses:
           - Expand trigger: button[data-testid="stExpandSidebarButton"] inside div[data-testid="stToolbar"]
           - Collapse trigger: div[data-testid="stSidebarCollapseButton"] inside div[data-testid="stSidebarHeader"]
           ============================================================== */

        /* Header: Transparent, pass clicks through except for toolbar */
        header[data-testid="stHeader"] {
            background: transparent !important;
            height: auto !important;
            pointer-events: none !important;
            z-index: 9999 !important;
        }

        /* Keep Toolbar alive & positioned at top-left for the Expand Button */
        header[data-testid="stHeader"] div[data-testid="stToolbar"] {
            display: flex !important;
            visibility: visible !important;
            opacity: 1 !important;
            pointer-events: auto !important;
            position: fixed !important;
            top: 0.75rem !important;
            left: 0.75rem !important;
            z-index: 10000 !important;
        }

        /* Hide Streamlit clutter (Deploy button, decoration, status widget), KEEP stExpandSidebarButton */
        button[data-testid="stDeployButton"],
        div[data-testid="stStatusWidget"],
        div[data-testid="stDecoration"],
        #MainMenu, footer {
            display: none !important;
            visibility: hidden !important;
        }

        /* 1. EXPAND BUTTON (When sidebar is collapsed/minimized) */
        button[data-testid="stExpandSidebarButton"] {
            display: flex !important;
            visibility: visible !important;
            opacity: 1 !important;
            pointer-events: auto !important;
            background: #ffffff !important;
            border: 1px solid #dae2fd !important;
            border-radius: 8px !important;
            color: #2a14b4 !important;
            box-shadow: 0 2px 8px rgba(42, 20, 180, 0.18) !important;
            width: 36px !important;
            height: 36px !important;
            align-items: center !important;
            justify-content: center !important;
            cursor: pointer !important;
            transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
        }
        button[data-testid="stExpandSidebarButton"]:hover {
            background: #eaedff !important;
            border-color: #4338ca !important;
            transform: scale(1.08) !important;
            box-shadow: 0 4px 12px rgba(42, 20, 180, 0.25) !important;
        }

        /* 2. COLLAPSE BUTTON (When sidebar is open/expanded) */
        div[data-testid="stSidebarHeader"] {
            display: flex !important;
            justify-content: flex-end !important;
            padding: 0.65rem 0.65rem 0 0.65rem !important;
            background: transparent !important;
        }
        div[data-testid="stSidebarCollapseButton"] button {
            display: flex !important;
            visibility: visible !important;
            opacity: 1 !important;
            background: #ffffff !important;
            border: 1px solid #dae2fd !important;
            border-radius: 8px !important;
            color: #464554 !important;
            box-shadow: 0 1px 3px rgba(0,0,0,0.04) !important;
            width: 32px !important;
            height: 32px !important;
            align-items: center !important;
            justify-content: center !important;
            cursor: pointer !important;
            transition: all 0.2s ease !important;
        }
        div[data-testid="stSidebarCollapseButton"] button:hover {
            background: #eaedff !important;
            color: #2a14b4 !important;
            border-color: #4338ca !important;
            transform: scale(1.05) !important;
        }

        /* Sidebar Styling */
        section[data-testid="stSidebar"] {
            background-color: #f2f3ff !important;
            border-right: 1px solid #dae2fd !important;
            transition: transform 0.25s cubic-bezier(0.4, 0, 0.2, 1), width 0.25s cubic-bezier(0.4, 0, 0.2, 1) !important;
        }
        section[data-testid="stSidebar"][aria-expanded="true"] {
            min-width: 17.5rem !important;
            max-width: 20rem !important;
        }
        section[data-testid="stSidebar"] .block-container {
            padding: 0.75rem 1rem 1.5rem 1rem !important;
        }

        /* Main app container padding */
        .main .block-container {
            padding-top: 1.25rem !important;
            padding-bottom: 3.5rem !important;
            max-width: 1440px !important;
            transition: margin-left 0.25s cubic-bezier(0.4, 0, 0.2, 1) !important;
        }

        /* Top Navigation Bar */
        .mimir-top-nav {
            background: #ffffff;
            border-bottom: 1px solid #eaedff;
            box-shadow: 0 1px 4px rgba(0,0,0,0.03);
            border-radius: 12px;
            padding: 0.75rem 1.25rem;
            margin-bottom: 1.5rem;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        .mimir-brand-cluster {
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }
        .mimir-logo-icon {
            width: 32px;
            height: 32px;
            border-radius: 8px;
            background: #4338ca;
            color: #ffffff;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .mimir-brand-title {
            font-size: 18px;
            font-weight: 700;
            color: #2a14b4;
            letter-spacing: -0.01em;
        }
        .mimir-workspace-badge {
            display: flex;
            align-items: center;
            gap: 0.4rem;
            background: #f2f3ff;
            color: #131b2e;
            padding: 0.3rem 0.75rem;
            border-radius: 8px;
            font-size: 13px;
            font-weight: 500;
        }
        .mimir-status-pill {
            display: inline-flex;
            align-items: center;
            gap: 0.4rem;
            background: rgba(153, 239, 229, 0.35);
            color: #006f67;
            padding: 0.25rem 0.85rem;
            border-radius: 9999px;
            font-size: 12px;
            font-weight: 600;
        }
        .mimir-pulse-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: #006a63;
            animation: pulse-ring 2s cubic-bezier(0.4, 0, 0.6, 1) infinite;
        }
        @keyframes pulse-ring {
            0%, 100% { opacity: 1; transform: scale(1); }
            50% { opacity: 0.4; transform: scale(1.15); }
        }

        /* Sidebar Navigation: Transform Radio into Menu Pills */
        div[data-testid="stRadio"] div[role="radiogroup"] {
            display: flex !important;
            flex-direction: column !important;
            gap: 4px !important;
        }
        div[data-testid="stRadio"] div[role="radiogroup"] > label {
            display: flex !important;
            align-items: center !important;
            padding: 8px 12px !important;
            border-radius: 8px !important;
            background: transparent !important;
            margin: 0 !important;
            transition: all 0.15s ease !important;
            cursor: pointer !important;
            color: #464554 !important;
            font-size: 13.5px !important;
            font-weight: 500 !important;
        }
        div[data-testid="stRadio"] div[role="radiogroup"] > label:hover {
            background: #eaedff !important;
            color: #131b2e !important;
        }
        /* Hide native circular radio button dot */
        div[data-testid="stRadio"] div[role="radiogroup"] > label > div:first-child {
            display: none !important;
        }
        div[data-testid="stRadio"] div[role="radiogroup"] > label > div:last-child {
            width: 100% !important;
        }
        /* Active Radio Menu Item */
        div[data-testid="stRadio"] div[role="radiogroup"] label:has(input:checked) {
            background: #4338ca !important;
            color: #ffffff !important;
            font-weight: 600 !important;
            box-shadow: 0 1px 4px rgba(67, 56, 202, 0.25) !important;
        }
        div[data-testid="stRadio"] div[role="radiogroup"] label:has(input:checked) * {
            color: #ffffff !important;
        }

        /* Custom Cards & Containers */
        .mimir-card {
            background: #ffffff;
            border-radius: 12px;
            padding: 1.25rem 1.4rem;
            box-shadow: 0 1px 4px rgba(0,0,0,0.03);
            margin-bottom: 1rem;
            border: 1px solid rgba(218, 226, 253, 0.6);
        }
        .mimir-sidebar-card {
            background: #ffffff;
            border-radius: 12px;
            padding: 0.85rem 1rem;
            box-shadow: 0 1px 3px rgba(0,0,0,0.03);
            margin-bottom: 1rem;
            border: 1px solid rgba(218, 226, 253, 0.5);
        }
        .mimir-card-tinted {
            background: rgba(227, 223, 255, 0.35);
            border-radius: 12px;
            padding: 1rem 1.25rem;
            margin-bottom: 1rem;
            border: 1px solid rgba(67, 56, 202, 0.15);
        }
        .mimir-alert-warn {
            background: rgba(255, 220, 195, 0.45);
            border-radius: 12px;
            padding: 0.85rem 1.15rem;
            margin-bottom: 1rem;
            border: 1px solid rgba(125, 66, 0, 0.15);
            display: flex;
            align-items: flex-start;
            gap: 0.75rem;
        }

        /* Metric Bento Tiles */
        .mimir-metric-tile {
            background: #ffffff;
            border-radius: 12px;
            padding: 1.15rem 1.25rem;
            box-shadow: 0 1px 4px rgba(0,0,0,0.03);
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            border: 1px solid rgba(218, 226, 253, 0.6);
            height: 100%;
        }
        .mimir-metric-val {
            font-size: 32px;
            font-weight: 700;
            color: #131b2e;
            letter-spacing: -0.02em;
            line-height: 1.1;
        }
        .mimir-metric-label {
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: #464554;
            margin-bottom: 0.35rem;
        }

        /* Unified Diff Container */
        .mimir-diff-box {
            background: #283044;
            color: #eef0ff;
            border-radius: 8px;
            padding: 0.85rem 1rem;
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
            font-size: 12px;
            line-height: 1.55;
            overflow-x: auto;
            box-shadow: inset 0 1px 3px rgba(0,0,0,0.25);
        }
        .mimir-diff-box .diff-hunk { color: #c1beff; font-weight: 600; }
        .mimir-diff-box .diff-add {
            background: rgba(0, 106, 99, 0.35);
            color: #9cf2e8;
            display: block;
            margin: 1px 0;
            padding: 0 4px;
            border-radius: 2px;
        }
        .mimir-diff-box .diff-del {
            background: rgba(186, 26, 26, 0.3);
            color: #ffdad6;
            display: block;
            margin: 1px 0;
            padding: 0 4px;
            border-radius: 2px;
        }

        /* Pill badges */
        .mimir-tag {
            display: inline-flex;
            align-items: center;
            gap: 4px;
            font-size: 11px;
            font-weight: 600;
            padding: 2px 8px;
            border-radius: 9999px;
            margin-right: 4px;
        }
        .mimir-tag-primary { background: #eaedff; color: #4338ca; }
        .mimir-tag-success { background: rgba(153, 239, 229, 0.5); color: #006f67; }
        .mimir-tag-warning { background: #ffdcc3; color: #7d4200; }
        .mimir-tag-danger { background: #ffdad6; color: #ba1a1a; }
        .mimir-tag-muted { background: #dae2fd; color: #464554; }

        /* Filter Chips Ribbon */
        .mimir-chip {
            padding: 5px 14px;
            border-radius: 9999px;
            font-size: 12.5px;
            font-weight: 500;
            background: #eaedff;
            color: #464554;
            display: inline-flex;
            align-items: center;
            gap: 4px;
            margin-right: 6px;
            text-decoration: none;
        }
        .mimir-chip-active {
            background: #2a14b4;
            color: #ffffff;
            font-weight: 600;
        }

        /* Buttons */
        div[data-testid="stButton"] > button[kind="primary"] {
            background: #4338ca !important;
            border: none !important;
            border-radius: 8px !important;
            padding: 0.65rem 1.25rem !important;
            font-weight: 600 !important;
            font-size: 14px !important;
            color: #ffffff !important;
            box-shadow: 0 2px 4px rgba(67, 56, 202, 0.2) !important;
            transition: all 0.15s ease !important;
        }
        div[data-testid="stButton"] > button[kind="primary"]:hover {
            background: #2a14b4 !important;
            box-shadow: 0 4px 8px rgba(67, 56, 202, 0.3) !important;
            transform: translateY(-1px);
        }
        div[data-testid="stButton"] > button[kind="secondary"] {
            background: #ffffff !important;
            border: 1px solid #c7c4d7 !important;
            border-radius: 8px !important;
            color: #131b2e !important;
            font-weight: 500 !important;
            font-size: 13.5px !important;
        }
        div[data-testid="stButton"] > button[kind="secondary"]:hover {
            background: #eaedff !important;
            border-color: #4338ca !important;
        }

        /* Inputs & Textareas */
        div[data-testid="stTextInput"] input,
        div[data-testid="stTextArea"] textarea,
        div[data-testid="stSelectbox"] select,
        div[data-baseweb="select"] > div {
            background-color: #f2f3ff !important;
            border: 1px solid transparent !important;
            border-radius: 8px !important;
            color: #131b2e !important;
            font-size: 13.5px !important;
        }
        div[data-testid="stTextInput"] input:focus,
        div[data-testid="stTextArea"] textarea:focus,
        div[data-baseweb="select"] > div:focus-within {
            background-color: #ffffff !important;
            border: 1px solid #4338ca !important;
            box-shadow: 0 0 0 2px rgba(67, 56, 202, 0.15) !important;
        }

        /* Bottom execution dock */
        .mimir-dock-container {
            background: #ffffff;
            border-radius: 12px;
            padding: 1.25rem 1.5rem;
            box-shadow: 0 1px 6px rgba(0,0,0,0.04);
            border: 1px solid #dae2fd;
            margin-top: 1.5rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
