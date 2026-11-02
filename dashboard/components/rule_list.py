"""Steerable rule cards matching the Habits & Safeguards design mockup."""

from __future__ import annotations

import html
from typing import Any, Callable

import streamlit as st

__all__ = ["render_rule_card"]

CATEGORY_ICONS = {
    "reliability": "wifi_protected_setup",
    "safety": "gavel",
    "formatting": "format_paint",
    "privacy": "visibility_lock",
    "typing": "code_blocks",
    "testing": "verified",
}


def render_rule_card(
    principle: Any,
    enabled: bool,
    *,
    key: str,
    on_toggle: Callable[[str, bool], None],
) -> None:
    """Render a high-fidelity habit card matching habits_safeguards.html."""
    status = getattr(principle, "epistemic_status", None)
    confidence = getattr(status, "confidence_score", 0.0) or 0.0
    category = (getattr(principle, "category", "") or "reliability").lower()
    name = getattr(principle, "name", "") or principle.id
    invariant = getattr(principle, "latent_invariant", "") or ""
    anti_list = getattr(principle, "anti_patterns", ()) or ()
    anti_pattern = anti_list[0] if anti_list else ""
    trigger = getattr(principle, "context_trigger", None)
    scope = getattr(trigger, "scope", "") if trigger else "External HTTP / RPC"

    icon_name = CATEGORY_ICONS.get(category, "verified_user")
    strip_color = "#006a63" if enabled else "#c7c4d7"
    badge_bg = "rgba(153, 239, 229, 0.45)" if enabled else "#eaedff"
    badge_color = "#006f67" if enabled else "#464554"
    badge_text = "Active Guard" if enabled else "Disabled"

    st.markdown(
        f"""
        <div style="background:#ffffff; border-radius:12px; box-shadow:0 1px 4px rgba(0,0,0,0.03); border:1px solid #dae2fd; display:flex; overflow:hidden; margin-bottom:8px;">
            <div style="width:6px; background:{strip_color};"></div>
            <div style="flex:1; padding:16px 20px;">
                <div style="display:flex; justify-content:space-between; align-items:flex-start;">
                    <div style="display:flex; align-items:flex-start; gap:14px;">
                        <div style="width:44px; height:44px; border-radius:10px; background:#f2f3ff; display:flex; align-items:center; justify-content:center; color:#2a14b4; flex-shrink:0;">
                            <span class="material-symbols-outlined" style="font-size:24px;">{icon_name}</span>
                        </div>
                        <div>
                            <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:4px;">
                                <span style="font-size:11px; font-weight:600; padding:2px 8px; border-radius:9999px; background:{badge_bg}; color:{badge_color};">
                                    {badge_text}
                                </span>
                                <span style="font-size:11px; font-weight:500; padding:2px 8px; border-radius:9999px; background:#f2f3ff; color:#464554;">
                                    {html.escape(category.title())} Engine
                                </span>
                                <span style="font-size:11px; color:#777586;">
                                    Trigger: {html.escape(scope)}
                                </span>
                                <span style="font-size:11px; color:#006a63; font-weight:600;">
                                    Confidence: {confidence:.0%}
                                </span>
                            </div>
                            <h3 style="margin:2px 0 6px 0; font-size:16px; font-weight:600; color:#131b2e;">
                                {html.escape(name)}
                            </h3>
                            <p style="margin:0; font-size:13.5px; color:#464554; line-height:1.45; max-width:760px;">
                                {html.escape(invariant)}
                            </p>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    t_col1, t_col2 = st.columns([1, 6])
    with t_col1:
        checked = st.toggle(
            "Safeguard State",
            value=enabled,
            key=f"rule-toggle-{key}",
        )
        if checked != enabled:
            on_toggle(principle.id, checked)

    if anti_pattern:
        with st.expander(f"Defended Invariant vs Anti-Pattern ({name})"):
            st.markdown(
                f"""
                <div style="display:grid; grid-template-columns:1fr 1fr; gap:12px;">
                    <div style="background:rgba(255, 218, 214, 0.4); border-radius:8px; padding:10px 14px; border:1px solid rgba(186, 26, 26, 0.15);">
                        <div style="font-size:11px; font-weight:700; color:#ba1a1a; margin-bottom:4px; text-transform:uppercase;">Anti-Pattern Blocked</div>
                        <div style="font-size:12.5px; color:#131b2e; font-family:monospace;">{html.escape(anti_pattern)}</div>
                    </div>
                    <div style="background:rgba(153, 239, 229, 0.35); border-radius:8px; padding:10px 14px; border:1px solid rgba(0, 106, 99, 0.15);">
                        <div style="font-size:11px; font-weight:700; color:#006a63; margin-bottom:4px; text-transform:uppercase;">Invariant Enforced</div>
                        <div style="font-size:12.5px; color:#131b2e;">{html.escape(invariant)}</div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
