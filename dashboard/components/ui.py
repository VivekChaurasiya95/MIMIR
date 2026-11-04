"""Shared Streamlit UI primitives for the Hebitus Control Tower.

Single source of truth for page headers, section titles, metric tiles,
status pills, and the Intention Studio workflow stepper so every screen
renders the same structure, type scale, and spacing.
"""

from __future__ import annotations

import html

import streamlit as st

__all__ = [
    "NAV_ITEMS",
    "NAV_KEYS",
    "nav_label",
    "page_header",
    "panel_title",
    "metric_tile",
    "status_pill",
    "stepper",
    "empty_state",
]

# (internal key, visible label, material icon). Order defines the sidebar nav.
NAV_ITEMS: tuple[tuple[str, str, str], ...] = (
    ("intention", "Intention Matrix", "neurology"),
    ("vault", "Knowledge Vault", "folder_open"),
    ("habits", "Assisting Habits", "tune"),
    ("audit", "Audit Trace Ledger", "history_edu"),
)
NAV_KEYS: tuple[str, ...] = tuple(key for key, _, _ in NAV_ITEMS)


def nav_label(key: str) -> str:
    """Tab label for a nav key, with a Material icon Streamlit renders."""
    for item_key, label, icon in NAV_ITEMS:
        if item_key == key:
            return f":material/{icon}:  {label}"
    raise KeyError(key)


def page_header(
    *,
    icon: str,
    title: str,
    subtitle: str,
    status_html: str = "",
) -> None:
    """Consistent page header: icon tile + title/subtitle + optional status."""
    st.markdown(
        f"""
        <div class="mimir-page-header">
            <div class="mimir-page-heading">
                <div class="mimir-page-icon">
                    <span class="material-symbols-outlined" style="font-size:20px;">{icon}</span>
                </div>
                <div>
                    <h1 class="mimir-page-title">{html.escape(title)}</h1>
                    <div class="mimir-page-subtitle">{html.escape(subtitle)}</div>
                </div>
            </div>
            <div class="mimir-page-status">{status_html}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def panel_title(*, icon: str, title: str, hint: str = "") -> None:
    """Consistent in-page panel/column heading."""
    hint_html = (
        f'<span class="mimir-panel-hint">{html.escape(hint)}</span>'
        if hint
        else ""
    )
    st.markdown(
        f"""
        <div class="mimir-panel-head">
            <div class="mimir-panel-name">
                <span class="material-symbols-outlined mimir-panel-icon">{icon}</span>
                <span>{html.escape(title)}</span>
            </div>
            {hint_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def metric_tile(
    *,
    label: str,
    value: str,
    foot: str,
    foot_icon: str,
    icon: str,
    icon_color: str = "#2a14b4",
) -> None:
    """One metric bento tile with identical layout on every screen."""
    st.markdown(
        f"""
        <div class="mimir-metric-tile">
            <div>
                <div class="mimir-metric-label">{html.escape(label)}</div>
                <div class="mimir-metric-val">{value}</div>
                <div class="mimir-metric-foot">
                    <span class="material-symbols-outlined" style="font-size:14px;">{foot_icon}</span>
                    {foot}
                </div>
            </div>
            <div class="mimir-metric-ico" style="color:{icon_color};">
                <span class="material-symbols-outlined" style="font-size:22px;">{icon}</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def status_pill(*, text: str, tone: str = "ok") -> str:
    """Small status pill HTML. Tones: ok, warn, info, danger, muted."""
    return (
        f'<span class="mimir-status-pill mimir-status-{tone}">'
        f'<span class="mimir-status-dot"></span><span>{html.escape(text)}</span>'
        "</span>"
    )


def stepper(*, steps: tuple[str, ...], current: int) -> None:
    """Horizontal workflow stepper; `current` is the 0-based active step."""
    items = []
    for index, label in enumerate(steps):
        if index < current:
            cls = "mimir-step-done"
            marker = '<span class="material-symbols-outlined" style="font-size:14px;">check</span>'
        elif index == current:
            cls = "mimir-step-now"
            marker = f"<span>{index + 1}</span>"
        else:
            cls = "mimir-step-todo"
            marker = f"<span>{index + 1}</span>"
        items.append(
            f'<div class="mimir-step {cls}">'
            f'<span class="mimir-step-num">{marker}</span>'
            f"<span>{html.escape(label)}</span></div>"
        )
        if index < len(steps) - 1:
            items.append('<div class="mimir-step-line"></div>')
    st.markdown(
        f'<div class="mimir-stepper">{"".join(items)}</div>',
        unsafe_allow_html=True,
    )


def empty_state(*, icon: str, title: str, body: str) -> None:
    """Centered empty-state card used identically on every screen."""
    st.markdown(
        f"""
        <div class="mimir-card mimir-empty">
            <span class="material-symbols-outlined mimir-empty-icon">{icon}</span>
            <div class="mimir-empty-title">{html.escape(title)}</div>
            <div class="mimir-empty-body">{html.escape(body)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
