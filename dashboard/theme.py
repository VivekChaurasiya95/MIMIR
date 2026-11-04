"""Institutional Assurance theme for the Hebitus Control Tower.

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
        /* Keep the collapse control above the pulled-up brand row. */
        div[data-testid="stSidebarCollapseButton"] {
            position: relative;
            z-index: 2;
        }

        /* Sidebar Styling: clean white rail, neutral hairline */
        section[data-testid="stSidebar"] {
            background-color: #ffffff !important;
            border-right: 1px solid #e9e9ee !important;
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

        /* ============ Sidebar brand ============ */
        .mimir-side-brand {
            display: flex;
            align-items: center;
            gap: 10px;
            /* Share the top header line with the collapse button; the
               right margin keeps the brand clear of the button. */
            margin: -44px 36px 12px 2px;
            padding: 0;
            position: relative;
            z-index: 1;
            max-width: 100%;
        }
        .mimir-side-brand img,
        .mimir-brand-cluster img {
            display: block;
            flex-shrink: 0;
        }
        .mimir-side-brandname {
            font-size: 16px;
            font-weight: 800;
            color: #101014;
            letter-spacing: 0.04em;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            min-width: 0;
        }
        .mimir-side-div {
            border-top: 1px solid #eeeef1;
            margin: 16px 2px 0 2px;
        }
        .mimir-side-statusrow {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 4px 2px 6px 2px;
        }
        .mimir-side-ver {
            font-size: 11.5px;
            font-weight: 600;
            color: #9a9aa3;
        }

        /* ============ Sidebar tabs: buttons styled as a tab rail ============
           Real <button> elements (never a radio), so no option circles can
           exist. Active tab = quiet gray pill + dark semibold text. */
        section[data-testid="stSidebar"] div[data-testid="stVerticalBlock"] {
            gap: 0.4rem !important;
        }
        section[data-testid="stSidebar"] div[data-testid="stButton"] > button {
            width: 100% !important;
            min-height: 40px !important;
            display: flex !important;
            align-items: center !important;
            justify-content: flex-start !important;
            text-align: left !important;
            padding: 8px 12px !important;
            margin: 0 !important;
            border-radius: 8px !important;
            background: transparent !important;
            border: 1px solid transparent !important;
            box-shadow: none !important;
            cursor: pointer !important;
            color: #23262b !important;
            font-size: 14px !important;
            font-weight: 500 !important;
            transition: background 0.15s ease, color 0.15s ease !important;
            transform: none !important;
        }
        section[data-testid="stSidebar"] div[data-testid="stButton"] > button:hover {
            background: #f4f4f6 !important;
            border-color: transparent !important;
            color: #101014 !important;
            box-shadow: none !important;
            transform: none !important;
        }
        section[data-testid="stSidebar"] div[data-testid="stButton"] > button:active {
            transform: none !important;
        }
        section[data-testid="stSidebar"] div[data-testid="stButton"] > button[kind="primary"] {
            background: #ececef !important;
            border-color: #ececef !important;
            color: #101014 !important;
            font-weight: 600 !important;
            box-shadow: none !important;
        }
        section[data-testid="stSidebar"] div[data-testid="stButton"] > button[kind="primary"]:hover {
            background: #e2e2e7 !important;
            border-color: #e2e2e7 !important;
            color: #101014 !important;
        }
        /* Tab content: icon + label on one truncated line */
        section[data-testid="stSidebar"] div[data-testid="stButton"] button [data-testid="stMarkdownContainer"],
        section[data-testid="stSidebar"] div[data-testid="stButton"] button p {
            display: flex !important;
            align-items: center !important;
            gap: 10px !important;
            margin: 0 !important;
            width: 100% !important;
            min-width: 0 !important;
            overflow: hidden !important;
            text-overflow: ellipsis !important;
            white-space: nowrap !important;
            font-size: 14px !important;
            color: inherit !important;
        }
        section[data-testid="stSidebar"] div[data-testid="stButton"] .material-symbols-outlined {
            font-size: 20px !important;
            font-weight: 300 !important;
            flex-shrink: 0 !important;
            color: inherit !important;
        }
        section[data-testid="stSidebar"] div[data-testid="stButton"] > button:focus-visible {
            outline: 2px solid #2a14b4 !important;
            outline-offset: 1px !important;
        }

        /* ============ Sidebar form controls ============ */
        section[data-testid="stSidebar"] div[data-testid="stTextInput"] input {
            background-color: #ffffff !important;
            border: 1px solid #d9d9de !important;
            border-radius: 8px !important;
            font-size: 12.5px !important;
            font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace !important;
        }
        section[data-testid="stSidebar"] div[data-testid="stTextInput"] input:focus {
            background-color: #ffffff !important;
            border-color: #2a14b4 !important;
            box-shadow: 0 0 0 2px rgba(42, 20, 180, 0.12) !important;
        }
        section[data-testid="stSidebar"] div[data-testid="stTextInput"] label p {
            font-size: 13px !important;
            font-weight: 600 !important;
            color: #23262b !important;
        }
        /* Toggle rows: label left, switch right, description tucked below. */
        section[data-testid="stSidebar"] div[data-testid="stToggle"] {
            padding: 7px 2px 0 2px !important;
            margin: 0 !important;
        }
        section[data-testid="stSidebar"] div[data-testid="stToggle"] > label {
            display: flex !important;
            align-items: center !important;
            justify-content: space-between !important;
            width: 100% !important;
            gap: 12px !important;
        }
        section[data-testid="stSidebar"] div[data-testid="stToggle"] > label > div:has(p) {
            order: 1 !important;
            flex: 1 1 auto !important;
            min-width: 0 !important;
        }
        section[data-testid="stSidebar"] div[data-testid="stToggle"] > label > div:not(:has(p)) {
            order: 2 !important;
            flex-shrink: 0 !important;
        }
        section[data-testid="stSidebar"] div[data-testid="stToggle"] label p {
            font-size: 13.5px !important;
            font-weight: 500 !important;
            color: #23262b !important;
        }
        section[data-testid="stSidebar"] div[data-testid="stCaptionContainer"] {
            margin-top: -0.3rem !important;
            padding: 0 2px 0.3rem 2px !important;
        }
        section[data-testid="stSidebar"] div[data-testid="stCaptionContainer"] p {
            font-size: 11.5px !important;
            color: #84848d !important;
        }
        /* Compact health-details expander */
        section[data-testid="stSidebar"] div[data-testid="stExpander"] details {
            background: #ffffff !important;
            border: 1px solid #e4e4e9 !important;
            border-radius: 8px !important;
            box-shadow: none !important;
        }
        section[data-testid="stSidebar"] div[data-testid="stExpander"] summary p {
            font-size: 12.5px !important;
            font-weight: 600 !important;
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

        /* ============ Consistent page + panel headers ============ */
        .mimir-page-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            gap: 16px;
            flex-wrap: wrap;
            margin-bottom: 18px;
        }
        .mimir-page-heading {
            display: flex;
            align-items: flex-start;
            gap: 12px;
            min-width: 0;
        }
        .mimir-page-icon {
            width: 40px;
            height: 40px;
            border-radius: 10px;
            background: #2a14b4;
            color: #ffffff;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
            margin-top: 2px;
        }
        .mimir-page-title {
            font-size: 24px !important;
            font-weight: 700 !important;
            color: #131b2e !important;
            margin: 0 !important;
            letter-spacing: -0.02em;
            line-height: 1.2 !important;
            padding: 0 !important;
        }
        .mimir-page-subtitle {
            font-size: 13.5px;
            color: #464554;
            margin-top: 3px;
            line-height: 1.45;
        }
        .mimir-page-status {
            display: flex;
            align-items: center;
            gap: 8px;
            flex-wrap: wrap;
            padding-top: 4px;
        }
        .mimir-panel-head {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 8px;
            margin-bottom: 10px;
        }
        .mimir-panel-name {
            display: flex;
            align-items: center;
            gap: 7px;
            font-size: 16px;
            font-weight: 600;
            color: #131b2e;
        }
        .mimir-panel-icon {
            color: #2a14b4 !important;
            font-size: 20px !important;
        }
        .mimir-panel-hint {
            font-size: 11px;
            font-weight: 500;
            background: #f2f3ff;
            color: #464554;
            padding: 2px 8px;
            border-radius: 4px;
            white-space: nowrap;
        }

        /* ============ Workflow stepper ============ */
        .mimir-stepper {
            display: flex;
            align-items: center;
            gap: 8px;
            background: #ffffff;
            border: 1px solid rgba(218, 226, 253, 0.7);
            border-radius: 10px;
            padding: 10px 16px;
            margin-bottom: 16px;
            flex-wrap: wrap;
        }
        .mimir-step {
            display: inline-flex;
            align-items: center;
            gap: 7px;
            font-size: 13px;
            font-weight: 600;
            color: #777586;
            white-space: nowrap;
        }
        .mimir-step-num {
            width: 22px;
            height: 22px;
            border-radius: 50%;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            font-size: 12px;
            font-weight: 700;
            background: #eaedff;
            color: #464554;
        }
        .mimir-step-now { color: #2a14b4; }
        .mimir-step-now .mimir-step-num { background: #2a14b4; color: #ffffff; }
        .mimir-step-done { color: #006a63; }
        .mimir-step-done .mimir-step-num { background: rgba(153, 239, 229, 0.6); color: #006a63; }
        .mimir-step-line {
            flex: 1 1 24px;
            min-width: 16px;
            max-width: 64px;
            height: 2px;
            border-radius: 2px;
            background: #eaedff;
        }

        /* ============ Sidebar sections ============ */
        .mimir-side-title {
            font-size: 11px !important;
            font-weight: 600 !important;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            color: #84848d !important;
            margin: 12px 0 4px 2px !important;
            padding: 0 !important;
        }
        .mimir-side-card {
            background: #ffffff;
            border: 1px solid #e4e4e9;
            border-radius: 10px;
            padding: 10px 12px;
            margin-bottom: 4px;
        }

        /* ============ Status pills (shared tones) ============ */
        .mimir-status-ok { background: rgba(153, 239, 229, 0.4) !important; color: #006f67 !important; }
        .mimir-status-warn { background: #ffdcc3 !important; color: #7d4200 !important; }
        .mimir-status-info { background: #eaedff !important; color: #4338ca !important; }
        .mimir-status-danger { background: #ffdad6 !important; color: #ba1a1a !important; }
        .mimir-status-muted { background: #f2f3ff !important; color: #464554 !important; }
        .mimir-status-ok .mimir-status-dot { background: #006a63; }
        .mimir-status-warn .mimir-status-dot { background: #d97706; }
        .mimir-status-info .mimir-status-dot { background: #4338ca; }
        .mimir-status-danger .mimir-status-dot { background: #ba1a1a; }
        .mimir-status-muted .mimir-status-dot { background: #777586; }
        .mimir-status-dot {
            width: 7px;
            height: 7px;
            border-radius: 50%;
            display: inline-block;
            flex-shrink: 0;
        }

        /* ============ Metric tile footer + icon ============ */
        .mimir-metric-foot {
            font-size: 12px;
            color: #777586;
            margin-top: 4px;
            display: flex;
            align-items: center;
            gap: 4px;
        }
        .mimir-metric-ico {
            width: 40px;
            height: 40px;
            border-radius: 10px;
            background: #f2f3ff;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
        }

        /* ============ Empty states ============ */
        .mimir-empty {
            text-align: center;
            padding: 32px 24px !important;
            color: #464554;
        }
        .mimir-empty-icon {
            font-size: 36px !important;
            color: #4338ca !important;
            display: block;
            margin-bottom: 8px;
        }
        .mimir-empty-title {
            font-size: 15px;
            font-weight: 600;
            color: #131b2e;
        }
        .mimir-empty-body {
            font-size: 13px;
            color: #777586;
            margin-top: 4px;
            line-height: 1.5;
        }

        /* ============ Filter/tool rows + segmented consistency ============ */
        div[data-testid="stSegmentedControl"] {
            margin-bottom: 8px;
        }
        div[data-testid="stExpander"] details {
            border: 1px solid rgba(218, 226, 253, 0.6) !important;
            border-radius: 10px !important;
            background: #ffffff !important;
        }
        div[data-testid="stExpander"] summary p {
            font-size: 13.5px !important;
            font-weight: 600 !important;
            color: #131b2e !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
