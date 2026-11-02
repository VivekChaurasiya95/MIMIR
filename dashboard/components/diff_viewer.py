"""Color-coded unified diff and plain-English preview viewer."""

from __future__ import annotations

import html

import streamlit as st

__all__ = ["render_diff", "render_english_comparison"]


def render_diff(unified_diff: str, *, filename: str = "") -> None:
    """Render a unified diff as a dark code panel with colored hunks."""
    if not unified_diff.strip():
        st.markdown(
            """
            <div style="background:#ffffff; border:1px dashed #c7c4d7; border-radius:8px; padding:1.5rem; text-align:center; color:#464554; font-size:13px;">
                <span class="material-symbols-outlined" style="font-size:24px; color:#4338ca; display:block; margin-bottom:4px;">preview</span>
                No changes proposed yet. Describe a task on the left and click <strong>Generate Proposed Change</strong>.
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    rows: list[str] = []
    line_no = 1
    for line in unified_diff.splitlines():
        safe = html.escape(line)
        if line.startswith(("+++", "---")):
            rows.append(f'<div class="diff-hunk">{safe}</div>')
        elif line.startswith("@@"):
            rows.append(f'<div class="diff-hunk" style="margin-top:4px;">{safe}</div>')
        elif line.startswith("+"):
            rows.append(f'<div class="diff-add"><span style="opacity:0.5; margin-right:8px;">{line_no:3d} +</span>{safe[1:]}</div>')
            line_no += 1
        elif line.startswith("-"):
            rows.append(f'<div class="diff-del"><span style="opacity:0.5; margin-right:8px;">{line_no:3d} -</span>{safe[1:]}</div>')
        else:
            rows.append(f'<div style="opacity:0.85;"><span style="opacity:0.4; margin-right:8px;">{line_no:3d}  </span>{safe}</div>')
            line_no += 1

    header = ""
    if filename:
        header = f"""
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px; font-family:monospace; font-size:12px; color:#464554;">
            <span><strong>{html.escape(filename)}</strong> (Unified Diff)</span>
            <span>Lines {line_no} modified</span>
        </div>
        """

    footer = """
    <div style="display:flex; justify-content:space-between; align-items:center; margin-top:8px; font-size:11px; color:#777586;">
        <span style="display:inline-flex; align-items:center; gap:4px;">
            <span class="material-symbols-outlined" style="font-size:14px;">tune</span>
            AST Mutation Engine v4.2
        </span>
        <span>Zero external dependencies added</span>
    </div>
    """

    st.markdown(
        f"{header}<div class='mimir-diff-box'>{chr(10).join(rows)}</div>{footer}",
        unsafe_allow_html=True,
    )


def render_english_comparison(before: str, after: str) -> None:
    """Render the dual Before/After plain-English explanation cards."""
    st.markdown(
        f"""
        <div style="display:flex; flex-direction:column; gap:10px; margin-top:8px;">
            <div style="background:rgba(255, 218, 214, 0.4); border-radius:10px; padding:12px 16px; border:1px solid rgba(186, 26, 26, 0.15);">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
                    <span style="display:inline-flex; align-items:center; gap:4px; font-size:12px; font-weight:600; color:#ba1a1a;">
                        <span class="material-symbols-outlined" style="font-size:16px;">remove_circle</span>
                        Current Behavior (Before)
                    </span>
                    <span style="font-size:11px; color:#ba1a1a; font-weight:500;">Raw Direct State</span>
                </div>
                <p style="font-size:13.5px; color:#131b2e; margin:0; line-height:1.45;">
                    {html.escape(before or '—')}
                </p>
            </div>
            <div style="background:rgba(153, 239, 229, 0.35); border-radius:10px; padding:12px 16px; border:1px solid rgba(0, 106, 99, 0.15);">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
                    <span style="display:inline-flex; align-items:center; gap:4px; font-size:12px; font-weight:600; color:#006a63;">
                        <span class="material-symbols-outlined" style="font-size:16px;">add_circle</span>
                        Improved Behavior (After)
                    </span>
                    <span style="font-size:11px; color:#006a63; font-weight:500;">Defensive Wrapper</span>
                </div>
                <p style="font-size:13.5px; color:#131b2e; margin:0; line-height:1.45;">
                    {html.escape(after or '—')}
                </p>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
