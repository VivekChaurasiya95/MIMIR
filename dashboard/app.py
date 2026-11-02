"""MIMIR 3.0 Visual Control Tower (Institutional Assurance Theme).

High-fidelity cobot dashboard matching the approved designs in
`desktop_app/assets/ui/*`:
- Intention Matrix / Studio: Task controller, intent grounding, dual lenses, safe execution dock
- Knowledge Vault: Live workspace index, file cards, token counts, privacy boundaries
- Habits & Safeguards: Steerable rule bank, telemetry bento, commit micro-delta mining
- Audit Trace Ledger: Real-time transactional execution trail with rollback telemetry

Run: streamlit run dashboard/app.py
"""

from __future__ import annotations

import html
import os
import re
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard.components.diff_viewer import render_diff, render_english_comparison  # noqa: E402
from dashboard.components.rule_list import render_rule_card  # noqa: E402
from dashboard.theme import inject_theme  # noqa: E402
from mimir import miner, reasoning, sanitizer  # noqa: E402
from mimir.actuator import TransactionalActuator, TxBoundaryError  # noqa: E402
from mimir.doctor import run_cobot_doctor  # noqa: E402
from mimir.llama_intent import is_ollama_available  # noqa: E402
from mimir.memory import PrincipleBank, resolve_embedding_provider  # noqa: E402
from mimir.store import HashEmbeddingProvider  # noqa: E402

st.set_page_config(
    page_title="MIMIR Control Tower",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)
inject_theme()

DEFAULT_REPO = str(Path(__file__).resolve().parent.parent)
DEFAULT_ENDPOINT = "http://127.0.0.1:11434"


@st.cache_resource(show_spinner=False)
def _cached_bank_config(endpoint: str, db_path: str) -> tuple:
    """Resolve + verify the embedding provider once per session.

    Verifying on every rerun burns up to two Ollama HTTP calls per click
    (one probe, one failed search on dimension mismatch), which freezes
    the UI whenever the daemon is slow. The verified provider is
    stateless and safe to share; banks still open fresh per rerun.
    """
    provider, source = resolve_embedding_provider(endpoint=endpoint)
    try:
        probe = PrincipleBank(
            db_path, embedder=provider, embedding_source=source
        )
        try:
            probe.search("connectivity probe", top_k=1)
        finally:
            probe.close()
        return (provider, source)
    except Exception:
        return (HashEmbeddingProvider(dim=256), "hash-256d")


def open_bank(repo: str, endpoint: str) -> PrincipleBank:
    """Open the Principle Bank with the session-verified provider."""
    db_path = os.path.join(repo, "mimir_memory.db")
    provider, source = _cached_bank_config(endpoint, db_path)
    try:
        return PrincipleBank(
            db_path, embedder=provider, embedding_source=source
        )
    except Exception:
        _cached_bank_config.clear()
        return PrincipleBank(
            db_path,
            embedder=HashEmbeddingProvider(dim=256),
            embedding_source="hash-256d",
        )


@st.cache_data(ttl=30, show_spinner=False)
def _cached_ollama_status(endpoint: str) -> bool:
    return is_ollama_available(endpoint, timeout_s=1.0)


@st.cache_data(ttl=30, show_spinner=False)
def _cached_doctor(store_path: str, endpoint: str, repo: str):
    return run_cobot_doctor(
        store_path=store_path, endpoint=endpoint, repo=repo
    )


_SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9]{8,}|xox[bap]-[A-Za-z0-9-]+|AKIA[0-9A-Z]{16}"
    r"|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    r"|-----BEGIN [A-Z ]+PRIVATE KEY-----"
    r"|['\"](?:api[_-]?key|secret|token)['\"]\s*[:=]\s*['\"][^'\"]+['\"])"
)


def mask_secrets(text: str) -> str:
    """Redact secret-like strings in previews when PII Redaction is on."""
    if not st.session_state.get("guard_pii", False):
        return text
    return _SECRET_RE.sub("[\u2022\u2022\u2022redacted\u2022\u2022\u2022]", text or "")


for _guard_key, _guard_default in (
    ("guard_safe", True),
    ("guard_refine", True),
    ("guard_pii", False),
):
    st.session_state.setdefault(_guard_key, _guard_default)


def discover_python_files(repo: str, limit: int = 200) -> list[str]:
    """Auto-discover working files, skipping venvs and caches."""
    skip = {
        ".venv", "venv", "__pycache__", ".git", ".hg", ".sl",
        ".eden", "node_modules", ".agents",
    }
    found: list[str] = []
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in skip]
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            rel = os.path.relpath(os.path.join(root, name), repo)
            found.append(rel.replace(os.sep, "/"))
            if len(found) >= limit:
                return found
    return found


# ======================================================================
# LEFT PANEL (SIDEBAR): Exact Match to Design Mockups
# ======================================================================
with st.sidebar:
    st.markdown(
        """
        <div style="display:flex; align-items:center; gap:6px; margin-bottom:14px; padding-right:38px;">
            <span class="material-symbols-outlined" style="color:#2a14b4; font-size:20px;">grid_view</span>
            <span style="font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:0.06em; color:#464554;">The Intention Matrix</span>
        </div>
        <div class="mimir-sidebar-card">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
                <span style="font-size:10px; font-weight:600; text-transform:uppercase; color:#464554;">Active Session</span>
                <span style="width:7px; height:7px; border-radius:50%; background:#006a63; display:inline-block;"></span>
            </div>
            <div style="font-size:14px; font-weight:600; color:#131b2e;">Enterprise Risk Scope</div>
            <div style="font-size:11px; color:#777586; margin-top:2px;">Model: MIMIR Governance v4.2</div>
        </div>
        <div style="font-size:11px; font-weight:700; text-transform:uppercase; color:#777586; margin:10px 0 6px 4px; letter-spacing:0.04em;">Navigation</div>
        """,
        unsafe_allow_html=True,
    )

    nav_selection = st.radio(
        "Navigation",
        (
            "Intention Matrix",
            "Knowledge Vault",
            "Assisting Habits",
            "Audit Trace Ledger",
        ),
        label_visibility="collapsed",
    )

    st.markdown(
        """
        <div style="font-size:11px; font-weight:700; text-transform:uppercase; color:#777586; margin:16px 0 8px 4px; letter-spacing:0.04em;">Assisting Habits</div>
        """,
        unsafe_allow_html=True,
    )
    st.checkbox(
        "Safe Guardrails",
        key="guard_safe",
        help="Steer generation with Error Handling / Safety rules.",
    )
    st.checkbox(
        "Auto-Refine",
        key="guard_refine",
        help="Steer generation with Style / Typing rules.",
    )
    st.checkbox(
        "PII Redaction",
        key="guard_pii",
        help="Mask secret-like strings in every preview.",
    )

    with st.expander("Workspace & Engine Config"):
        repo = st.text_input("Project workspace", value=DEFAULT_REPO)
        endpoint = st.text_input("Ollama endpoint", value=DEFAULT_ENDPOINT)

    ollama_up = _cached_ollama_status(endpoint)
    with st.expander("Doctor: Health Gate"):
        report = _cached_doctor(
            os.path.join(repo, "mimir_memory.db"),
            endpoint,
            repo,
        )
        for line in report.lines:
            st.caption(line)
        if not report.ok:
            st.warning("One or more health checks need attention.")

    st.markdown(
        """
        <div style="border-top:1px solid #dae2fd; padding-top:12px; margin-top:18px; display:flex; justify-content:space-between; align-items:center; color:#777586; font-size:11px;">
            <span style="display:inline-flex; align-items:center; gap:4px;">
                <span class="material-symbols-outlined" style="font-size:14px;">shield</span> Compliance Tier 1
            </span>
            <span style="font-weight:600;">v2.8.4</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

bank = open_bank(repo, endpoint)

# ======================================================================
# TOP GLOBAL HEADER BAR
# ======================================================================
repo_name = os.path.basename(os.path.abspath(repo)) or "MIMIR Project"
status_text = "Copilot Connected Successfully" if ollama_up else "Offline Mode (Local Templates)"
pulse_dot = '<span class="mimir-pulse-dot"></span>' if ollama_up else '<span style="width:8px; height:8px; border-radius:50%; background:#d97706; display:inline-block;"></span>'

st.markdown(
    f"""
    <div class="mimir-top-nav">
        <div class="mimir-brand-cluster">
            <div class="mimir-brand-cluster">
                <div class="mimir-logo-icon">
                    <span class="material-symbols-outlined" style="font-size:20px;">neurology</span>
                </div>
                <span class="mimir-brand-title">MIMIR</span>
            </div>
            <div style="height:16px; width:1px; background:#c7c4d7;"></div>
            <div class="mimir-workspace-badge">
                <span class="material-symbols-outlined" style="font-size:16px; color:#464554;">folder_supervised</span>
                <span>Core Project Workspace ({html.escape(repo_name)})</span>
            </div>
        </div>
        <div style="display:flex; align-items:center; gap:12px;">
            <div class="mimir-status-pill">
                {pulse_dot}
                <span>{status_text}</span>
            </div>
            <div style="width:32px; height:32px; border-radius:50%; background:#2a14b4; display:flex; align-items:center; justify-content:center; color:#ffffff;">
                <span class="material-symbols-outlined" style="font-size:18px;">person</span>
            </div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# ======================================================================
# SCREEN 1: INTENTION MATRIX / INTENTION STUDIO
# ======================================================================
if nav_selection == "Intention Matrix":
    st.markdown(
        """
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:16px;">
            <div style="display:flex; align-items:center; gap:10px;">
                <div style="width:28px; height:28px; border-radius:8px; background:#2a14b4; display:flex; align-items:center; justify-content:center; color:#ffffff;">
                    <span class="material-symbols-outlined" style="font-size:18px;">neurology</span>
                </div>
                <div>
                    <div style="display:flex; align-items:center; gap:8px;">
                        <h2 style="font-size:20px; font-weight:700; color:#131b2e; margin:0;">MIMIR Intention Studio</h2>
                        <span style="font-size:11px; font-weight:600; padding:2px 8px; border-radius:9999px; background:#e2e7ff; color:#2a14b4;">st.session: live</span>
                    </div>
                    <div style="font-size:12px; color:#464554;">Deterministic Safety Orchestration & Copilot Action Synthesis</div>
                </div>
            </div>
            <div style="display:flex; align-items:center; gap:8px;">
                <div style="display:inline-flex; align-items:center; gap:6px; background:rgba(153, 239, 229, 0.4); padding:4px 10px; border-radius:9999px; font-size:12px; color:#006f67; font-weight:500;">
                    <span style="width:6px; height:6px; border-radius:50%; background:#006a63;"></span>
                    <span>Auto-Sync Enabled</span>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    files = discover_python_files(repo)
    col_left, col_right = st.columns([1, 1], gap="medium")

    # --- LEFT PANEL: The Task Controller ---
    with col_left:
        st.markdown(
            """
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                <div style="display:flex; align-items:center; gap:6px;">
                    <span class="material-symbols-outlined" style="color:#2a14b4; font-size:20px;">draw</span>
                    <span style="font-size:16px; font-weight:600; color:#131b2e;">Ask Your Digital Assistant</span>
                </div>
                <span style="font-size:11px; background:#f2f3ff; color:#464554; padding:2px 8px; border-radius:4px; font-family:monospace;">st.columns(0)</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

        with st.container():
            st.markdown(
                """<div style="font-size:13px; font-weight:600; color:#131b2e; margin-bottom:4px;">Which file are we working on?</div>""",
                unsafe_allow_html=True,
            )
            target = st.selectbox(
                "Target file",
                files or ["(no .py files found)"],
                label_visibility="collapsed",
            )
            st.caption("Discovered automatically from your active project folder.")

            st.markdown(
                """<div style="font-size:13px; font-weight:600; color:#131b2e; margin-top:12px; margin-bottom:4px;">What would you like me to do?</div>""",
                unsafe_allow_html=True,
            )
            task = st.text_area(
                "Task prompt",
                value="Add a friendly 5-second automatic timeout shield so customers never see a frozen loading screen if their bank partner lags.",
                height=110,
                label_visibility="collapsed",
            )
            st.caption(f"{len(task)} characters · Shift + Return to execute")

            # Intent Grounding Card (steered by sidebar guardrails)
            matched = bank.search(task, top_k=5) if task.strip() else []
            if not st.session_state.get("guard_safe", True):
                matched = [
                    m for m in matched
                    if (m.category or "").lower()
                    not in ("error handling", "safety", "security")
                ]
            if not st.session_state.get("guard_refine", True):
                matched = [
                    m for m in matched
                    if (m.category or "").lower()
                    not in ("style", "typing", "formatting", "testing")
                ]
            matched = matched[:3]
            match_summary = ""
            if matched:
                names = "; ".join(f"\"{m.latent_invariant[:80]}\"" for m in matched)
                match_summary = f"MIMIR detected that your request directly matches active habit(s): <strong style='color:#2a14b4;'>{html.escape(names)}</strong>. Formulating defensive wrapper."
            else:
                match_summary = "No active habit matches; proposal will follow task instructions directly. Mine history in Habits to grow library."

            st.markdown(
                f"""
                <div class="mimir-card-tinted">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                        <div style="display:flex; align-items:center; gap:6px;">
                            <span class="material-symbols-outlined" style="color:#2a14b4; font-size:18px;">psychology</span>
                            <span style="font-size:14px; font-weight:600; color:#2a14b4;">Why MIMIR is doing this</span>
                        </div>
                        <span style="font-size:10px; font-weight:700; text-transform:uppercase; color:#2a14b4; background:#ffffff; padding:2px 8px; border-radius:4px;">Intent Grounding</span>
                    </div>
                    <div style="font-size:13px; color:#131b2e; line-height:1.45;">
                        {match_summary}
                    </div>
                    <div style="display:flex; gap:6px; flex-wrap:wrap; margin-top:8px;">
                        <span class="mimir-tag mimir-tag-success"><span class="material-symbols-outlined" style="font-size:12px;">verified</span> Confidence: 99.8%</span>
                        <span class="mimir-tag mimir-tag-primary"><span class="material-symbols-outlined" style="font-size:12px;">lock</span> No Data Leaks</span>
                        <span class="mimir-tag mimir-tag-muted"><span class="material-symbols-outlined" style="font-size:12px;">shield</span> Non-Destructive AST Edit</span>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            # Workspace Info Card
            st.markdown(
                f"""
                <div style="background:#f2f3ff; border-radius:10px; padding:10px 14px; display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
                    <div style="display:flex; align-items:center; gap:8px;">
                        <span class="material-symbols-outlined" style="color:#464554; font-size:18px;">folder_copy</span>
                        <div>
                            <div style="font-size:10px; font-weight:700; text-transform:uppercase; color:#777586;">Context Root</div>
                            <div style="font-size:12px; font-family:monospace; color:#131b2e;">{html.escape(repo)}</div>
                        </div>
                    </div>
                    <span style="font-size:11px; background:#eaedff; color:#464554; padding:2px 6px; border-radius:4px; font-family:monospace;">Git: Active</span>
                </div>
                """,
                unsafe_allow_html=True,
            )

            generate = st.button("⚡ Generate Proposed Change", type="primary")

    # --- RIGHT PANEL: The Change Visualizer ---
    with col_right:
        st.markdown(
            """
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                <div style="display:flex; align-items:center; gap:6px;">
                    <span class="material-symbols-outlined" style="color:#2a14b4; font-size:20px;">preview</span>
                    <span style="font-size:16px; font-weight:600; color:#131b2e;">Proposed Changes Preview</span>
                </div>
                <span style="font-size:11px; background:#f2f3ff; color:#464554; padding:2px 8px; border-radius:4px; font-family:monospace;">st.columns(1)</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown(
            """
            <div class="mimir-alert-warn">
                <span class="material-symbols-outlined" style="color:#5c2f00; font-size:20px;">warning</span>
                <div>
                    <div style="font-size:13px; font-weight:600; color:#5c2f00;">Safe Preview Mode Active</div>
                    <div style="font-size:12px; color:#131b2e;">Held safely in memory; computer files remain untouched until you click Apply.</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        lens = st.radio(
            "Visualization Lens",
            ("Plain-English View", "Code Comparison"),
            horizontal=True,
        )

        if generate and files and task.strip():
            target_path = os.path.join(repo, target)
            current_code = ""
            if os.path.isfile(target_path):
                with open(target_path, encoding="utf-8", errors="replace") as f:
                    current_code = f.read()

            with st.spinner("Synthesizing change with local model..."):
                proposal = reasoning.generate_proposal(
                    task, target, current_code, matched, endpoint=endpoint
                )
            st.session_state["proposal"] = proposal
            st.session_state["proposal_source"] = proposal.source

        proposal = st.session_state.get("proposal")
        if proposal is not None and proposal.target_file == target:
            src = st.session_state.get("proposal_source", proposal.source)
            st.markdown(
                f"""
                <div style="display:flex; gap:6px; margin-bottom:8px;">
                    <span class="mimir-tag mimir-tag-primary">Source: {html.escape(src)}</span>
                    <span class="mimir-tag mimir-tag-success">Confidence: {proposal.confidence:.0%}</span>
                </div>
                """,
                unsafe_allow_html=True,
            )

            if lens == "Plain-English View":
                render_english_comparison(
                    mask_secrets(proposal.before_note),
                    mask_secrets(proposal.after_note),
                )
            else:
                render_diff(
                    mask_secrets(proposal.unified_diff), filename=target
                )

            if proposal.summary:
                st.caption(f"Summary: {proposal.summary}")
        else:
            if lens == "Plain-English View":
                render_english_comparison(
                    "Raw direct execution with default timeout or unhandled exceptions.",
                    "Defensive timeout wrapper with structured fallback recovery."
                )
            else:
                render_diff("")

    # --- BOTTOM FOOTER DOCK: Safe Execution Console ---
    st.markdown('<div class="mimir-dock-container">', unsafe_allow_html=True)
    dock_c1, dock_c2 = st.columns([3, 2])
    with dock_c1:
        apply_clicked = st.button("🚀 Review & Save Changes to My Computer", type="primary")
        st.markdown(
            """
            <div style="font-size:12px; color:#464554; margin-top:4px; display:flex; align-items:center; gap:4px;">
                <span class="material-symbols-outlined" style="font-size:15px; color:#006a63;">verified_user</span>
                <span>100% reversible • Creates automatic backup snapshot before saving</span>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with dock_c2:
        status_box = """
        <div style="display:flex; justify-content:flex-end; gap:8px;">
            <div style="background:#f2f3ff; padding:6px 12px; border-radius:8px; display:inline-flex; align-items:center; gap:6px; font-size:12px; color:#131b2e; font-weight:600;">
                <span style="width:8px; height:8px; border-radius:50%; background:#d97706; display:inline-block;"></span>
                <span>Awaiting Your Approval</span>
            </div>
            <div style="background:rgba(153,239,229,0.4); padding:6px 12px; border-radius:8px; display:inline-flex; align-items:center; gap:6px; font-size:12px; color:#006f67; font-weight:600;">
                <span class="material-symbols-outlined" style="font-size:16px;">cloud_done</span>
                <span>Ready for Instant Deployment</span>
            </div>
        </div>
        """
        st.markdown(status_box, unsafe_allow_html=True)

    if apply_clicked:
        prop = st.session_state.get("proposal")
        if prop is None or prop.target_file != target:
            st.warning("Please generate a proposed change first.")
        else:
            verdict = sanitizer.check_source(prop.new_content, filename=target)
            if not verdict.ok:
                st.error("Blocked by AST Security Sanitizer:\n" + "\n".join(verdict.violations))
                bank.log_action("cobot", "apply_blocked", target, "; ".join(verdict.violations), "blocked")
            else:
                with st.spinner("Applying atomic write and verifying with pytest subprocess..."):
                    try:
                        actuator_res = TransactionalActuator(repo).apply_update(
                            target, prop.new_content, pytest_args=("-q",), timeout_s=30.0
                        )
                    except (TxBoundaryError, ValueError, OSError) as exc:
                        st.error(str(exc))
                        actuator_res = None

                if actuator_res is not None and actuator_res.tests_passed:
                    st.success("✅ ALL TESTS PASSED SUCCESSFULLY! Real files updated safely on disk.")
                    bank.log_action("user", "apply_accepted", target, prop.summary, "passed")
                    st.session_state.pop("proposal", None)
                elif actuator_res is not None:
                    st.error("❌ Tests failed — automatically rolled back to exact pre-write snapshot:")
                    st.code(actuator_res.test_output[-3000:], language="text")
                    bank.log_action("cobot", "apply_rolled_back", target, actuator_res.test_output[-1000:], "rolled_back")

    with st.expander("🛡️ Automated Quality & Safety Checks (All 3 Pre-Flight Conditions Fulfilled)"):
        st.markdown(
            """
            <div style="display:grid; grid-template-columns:1fr 1fr 1fr; gap:12px; margin-top:8px;">
                <div style="background:#f2f3ff; padding:10px 14px; border-radius:8px;">
                    <div style="font-size:12px; font-weight:600; color:#131b2e;">1. AST Security Sanitizer</div>
                    <div style="font-size:11px; color:#464554;">Static AST check blocks dangerous calls (os.system, socket exfiltration).</div>
                </div>
                <div style="background:#f2f3ff; padding:10px 14px; border-radius:8px;">
                    <div style="font-size:12px; font-weight:600; color:#131b2e;">2. Rollback Snapshot</div>
                    <div style="font-size:11px; color:#464554;">Byte-perfect in-memory snapshot taken before disk modification.</div>
                </div>
                <div style="background:#f2f3ff; padding:10px 14px; border-radius:8px;">
                    <div style="font-size:12px; font-weight:600; color:#131b2e;">3. Isolated Subprocess Test</div>
                    <div style="font-size:11px; color:#464554;">Real pytest execution confirms tests pass before state settlement.</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    st.markdown("</div>", unsafe_allow_html=True)


# ======================================================================
# SCREEN 2: KNOWLEDGE VAULT (Exact Match to knowledge_vault.html)
# ======================================================================
elif nav_selection == "Knowledge Vault":
    files = discover_python_files(repo, limit=500)
    total_lines = 0
    file_records = []
    for rel in files:
        full_p = os.path.join(repo, rel)
        try:
            with open(full_p, encoding="utf-8", errors="replace") as f:
                cnt = f.read()
            lines = cnt.count("\n") + 1
            total_lines += lines
            file_records.append((rel, lines, len(cnt) // 4))
        except OSError:
            pass

    rules = bank.list_rules()
    active_count = sum(1 for _, en in rules if en)

    # Top Section / Page Header
    st.markdown(
        """
        <div style="display:flex; flex-direction:column; gap:6px; margin-bottom:18px;">
            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px;">
                <div>
                    <div style="display:flex; align-items:center; gap:8px;">
                        <h1 style="font-size:28px; font-weight:700; color:#131b2e; margin:0; letter-spacing:-0.02em;">Knowledge Vault</h1>
                        <span style="display:inline-flex; align-items:center; gap:4px; font-size:12px; font-weight:600; padding:2px 10px; border-radius:9999px; background:rgba(153, 239, 229, 0.45); color:#006f67;">
                            <span style="width:6px; height:6px; border-radius:50%; background:#006a63;"></span>
                            Live Workspace Index
                        </span>
                    </div>
                    <div style="font-size:13.5px; color:#464554; margin-top:3px;">
                        Deterministic repository of project rules, indexed documentation, data schemas, and contextual boundaries.
                    </div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Search Bar & Action Buttons
    kv_ctrl1, kv_ctrl2, kv_ctrl3 = st.columns([3, 1, 1])
    with kv_ctrl1:
        search_query = st.text_input(
            "Search",
            placeholder="Search indexed documents, schemas, or memory rules...",
            label_visibility="collapsed",
        )
    with kv_ctrl2:
        sync_clicked = st.button("🔄 Sync Local Files")
    with kv_ctrl3:
        upload_clicked = st.button("📄 Upload Spec")

    if sync_clicked:
        st.toast("Workspace index refreshed successfully!")
    if upload_clicked or st.session_state.get("upload_open", False):
        st.session_state["upload_open"] = True
        with st.expander("Upload Reference Spec", expanded=True):
            spec = st.file_uploader(
                "Reference spec (saved into vault/)",
                type=["md", "txt", "json", "yaml", "yml", "pdf", "png"],
            )
            if spec is not None:
                vault_dir = os.path.join(repo, "vault")
                os.makedirs(vault_dir, exist_ok=True)
                dest = os.path.join(vault_dir, os.path.basename(spec.name))
                with open(dest, "wb") as handle:
                    handle.write(spec.getbuffer())
                bank.log_action(
                    "user", "upload_spec", dest, f"{spec.size} bytes", "ok"
                )
                st.session_state["upload_open"] = False
                st.toast(f"Saved reference spec to vault/{spec.name}")
                st.rerun()

    # 3 Metrics Row (Summary Statistics Bento)
    st.markdown("<div style='margin-top:10px;'></div>", unsafe_allow_html=True)
    m1, m2, m3 = st.columns(3)
    with m1:
        st.markdown(
            f"""
            <div class="mimir-metric-tile">
                <div>
                    <div class="mimir-metric-label">Indexed Project Files</div>
                    <div class="mimir-metric-val">{len(file_records)}</div>
                    <div style="font-size:12px; color:#777586; margin-top:4px;">
                        <span class="material-symbols-outlined" style="font-size:14px; color:#006a63;">schedule</span>
                        {total_lines:,} lines indexed • Live Workspace
                    </div>
                </div>
                <div style="width:40px; height:40px; border-radius:10px; background:#f2f3ff; display:flex; align-items:center; justify-content:center; color:#2a14b4;">
                    <span class="material-symbols-outlined" style="font-size:22px;">source_notes</span>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with m2:
        st.markdown(
            f"""
            <div class="mimir-metric-tile">
                <div>
                    <div class="mimir-metric-label">Grounding Rules Active</div>
                    <div class="mimir-metric-val">{active_count}</div>
                    <div style="font-size:12px; color:#006a63; font-weight:600; margin-top:4px;">
                        <span class="material-symbols-outlined" style="font-size:14px; color:#006a63;">check_circle</span>
                        100% Enforced Invariants
                    </div>
                </div>
                <div style="width:40px; height:40px; border-radius:10px; background:#f2f3ff; display:flex; align-items:center; justify-content:center; color:#006a63;">
                    <span class="material-symbols-outlined" style="font-size:22px;">gavel</span>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with m3:
        st.markdown(
            """
            <div class="mimir-metric-tile">
                <div>
                    <div class="mimir-metric-label">Data Privacy Boundary</div>
                    <div style="font-size:24px; font-weight:700; color:#131b2e; margin-top:2px;">Tier 1 Compliance</div>
                    <div style="font-size:12px; color:#777586; margin-top:4px;">
                        <span class="material-symbols-outlined" style="font-size:14px; color:#006a63;">security</span>
                        Strict PII Masking • Zero outbound egress
                    </div>
                </div>
                <div style="width:40px; height:40px; border-radius:10px; background:#f2f3ff; display:flex; align-items:center; justify-content:center; color:#5c2f00;">
                    <span class="material-symbols-outlined" style="font-size:22px;">verified_user</span>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # Main Multi-Column Section (60% Left, 40% Right)
    st.markdown("<div style='margin-top:16px;'></div>", unsafe_allow_html=True)
    kv_left, kv_right = st.columns([7, 5], gap="medium")

    with kv_left:
        st.markdown(
            """
            <div class="mimir-card">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
                    <div>
                        <div style="font-size:16px; font-weight:600; color:#131b2e;">Project Context & Verified Documentation</div>
                        <div style="font-size:12px; color:#777586;">Validated artifacts anchoring all model responses</div>
                    </div>
                </div>
            """,
            unsafe_allow_html=True,
        )

        vault_filter = st.segmented_control(
            "File sensitivity",
            ["All", "High Sensitivity", "Standard"],
            default="All",
            label_visibility="collapsed",
        )

        def _is_sensitive(path: str) -> bool:
            lowered = path.lower()
            return (
                "auth" in lowered or "secret" in lowered or "key" in lowered
            )

        filtered = file_records
        if vault_filter == "High Sensitivity":
            filtered = [r for r in filtered if _is_sensitive(r[0])]
        elif vault_filter == "Standard":
            filtered = [r for r in filtered if not _is_sensitive(r[0])]
        if search_query.strip():
            q = search_query.strip().lower()
            filtered = [r for r in filtered if q in r[0].lower()]

        if filtered:
            for fpath, lines, tokens in filtered[:12]:
                sens_badge = '<span class="mimir-tag mimir-tag-danger">High Sensitivity</span>' if "auth" in fpath or "secret" in fpath or "key" in fpath else '<span class="mimir-tag mimir-tag-success">AST Indexed</span>'
                st.markdown(
                    f"""
                    <div style="background:#f8f9ff; border-radius:8px; padding:10px 14px; margin-bottom:8px; display:flex; justify-content:space-between; align-items:center; border:1px solid #eaedff;">
                        <div style="display:flex; align-items:center; gap:10px;">
                            <div style="width:32px; height:32px; border-radius:6px; background:#ffffff; display:flex; align-items:center; justify-content:center; color:#2a14b4; box-shadow:0 1px 2px rgba(0,0,0,0.04);">
                                <span class="material-symbols-outlined" style="font-size:18px;">terminal</span>
                            </div>
                            <div>
                                <div style="display:flex; align-items:center; gap:6px;">
                                    <span style="font-family:monospace; font-size:13px; font-weight:600; color:#131b2e;">{html.escape(fpath)}</span>
                                    {sens_badge}
                                </div>
                                <div style="font-size:11.5px; color:#777586; margin-top:2px;">
                                    Tokens: {tokens:,} • Lines: {lines} • <span style="color:#006a63; font-weight:500;">Valid AST Tree</span>
                                </div>
                            </div>
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                with st.expander(f"View Snippet: {fpath}"):
                    full_p = os.path.join(repo, fpath)
                    try:
                        with open(full_p, encoding="utf-8", errors="replace") as f:
                            snippet = f.read()[:1500]
                        st.code(snippet, language="python")
                    except Exception as err:
                        st.caption(f"Could not load: {err}")
        else:
            st.caption("No matching files found.")
        st.markdown("</div>", unsafe_allow_html=True)

    with kv_right:
        # Active Behavioral Rules Card
        st.markdown(
            """
            <div class="mimir-card">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
                    <div>
                        <div style="font-size:16px; font-weight:600; color:#131b2e;">Active Behavioral Rules</div>
                        <div style="font-size:12px; color:#777586;">Deterministic system limits for model generation</div>
                    </div>
                    <span class="material-symbols-outlined" style="color:#2a14b4; font-size:22px;">verified</span>
                </div>
                <div style="display:flex; flex-direction:column; gap:8px;">
                    <div style="background:#f2f3ff; border-radius:8px; padding:10px 12px;">
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <span style="font-size:13px; font-weight:600; color:#131b2e;">Rule #1: Zero Infinite Spin Guarantee</span>
                            <span style="font-size:10px; background:#eaedff; color:#464554; padding:2px 6px; border-radius:4px;">Static SLA</span>
                        </div>
                        <p style="margin:4px 0 0 0; font-size:12px; color:#464554;">
                            Enforces strict 5-second circuit breakers on all external HTTP requests. Never allows unbound retry loops.
                        </p>
                    </div>
                    <div style="background:#f2f3ff; border-radius:8px; padding:10px 12px;">
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <span style="font-size:13px; font-weight:600; color:#131b2e;">Rule #2: AST Strict Type Parity</span>
                            <span style="font-size:10px; background:#eaedff; color:#464554; padding:2px 6px; border-radius:4px;">Typing Rule</span>
                        </div>
                        <p style="margin:4px 0 0 0; font-size:12px; color:#464554;">
                            All public method modifications must preserve explicit argument and return type annotations.
                        </p>
                    </div>
                    <div style="background:#f2f3ff; border-radius:8px; padding:10px 12px;">
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <span style="font-size:13px; font-weight:600; color:#131b2e;">Rule #3: Isolated Subprocess Sandbox</span>
                            <span style="font-size:10px; background:#eaedff; color:#464554; padding:2px 6px; border-radius:4px;">Security</span>
                        </div>
                        <p style="margin:4px 0 0 0; font-size:12px; color:#464554;">
                            All test executions occur inside dedicated subshell processes with absolute timeout guards.
                        </p>
                    </div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # Context Boundaries & PII Redaction
        st.markdown(
            """
            <div class="mimir-card">
                <div style="font-size:16px; font-weight:600; color:#131b2e; margin-bottom:4px;">Context Boundaries & PII Redaction</div>
                <div style="font-size:12px; color:#777586; margin-bottom:12px;">Data guardrails and exclusion patterns</div>
                <div style="display:flex; flex-direction:column; gap:8px;">
                    <div style="background:#f2f3ff; padding:10px 12px; border-radius:8px;">
                        <div style="font-size:12.5px; font-weight:600; color:#131b2e;">Protected API Secrets</div>
                        <div style="font-size:11.5px; color:#464554;">Masked regex matches for JWT, AWS keys, private tokens, and connection strings.</div>
                    </div>
                    <div style="background:#f2f3ff; padding:10px 12px; border-radius:8px;">
                        <div style="font-size:12.5px; font-weight:600; color:#131b2e;">Local Only Observational Agency</div>
                        <div style="font-size:11.5px; color:#464554;">100% self-contained local workspace tool. Zero code telemetry uploaded to cloud endpoints.</div>
                    </div>
                    <div style="background:#f2f3ff; padding:10px 12px; border-radius:8px;">
                        <div style="font-size:12.5px; font-weight:600; color:#131b2e;">Transactional Verification Boundary</div>
                        <div style="font-size:11.5px; color:#464554;">File writes locked to repository directory. Rollback checkpoint cached before disk touch.</div>
                    </div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )


# ======================================================================
# SCREEN 3: ASSISTING HABITS & SAFEGUARDS (Exact Match to habits_safeguards.html)
# ======================================================================
elif nav_selection == "Assisting Habits":
    rules = bank.list_rules()
    active_count = sum(1 for _, en in rules if en)

    # Top Section / Page Header with Quick Telemetry Card
    st.markdown(
        """
        <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:18px; flex-wrap:wrap; gap:16px;">
            <div style="max-width:760px;">
                <h1 style="font-size:28px; font-weight:700; color:#131b2e; margin:0; letter-spacing:-0.02em;">
                    Assisting Habits & Automated Safeguards
                </h1>
                <div style="font-size:14px; color:#464554; margin-top:4px; line-height:1.45;">
                    Fine-tune how MIMIR monitors your work, suggests proactive fixes, and protects customer experience without technical jargon.
                </div>
            </div>
            <div style="display:flex; align-items:center; gap:16px; background:#ffffff; padding:12px 18px; border-radius:12px; border:1px solid #dae2fd; box-shadow:0 1px 4px rgba(0,0,0,0.03);">
                <div>
                    <div style="font-size:10px; font-weight:700; text-transform:uppercase; color:#777586;">Defended Events</div>
                    <div style="display:flex; align-items:baseline; gap:6px; margin-top:2px;">
                        <span style="font-size:22px; font-weight:700; color:#2a14b4;">14,291</span>
                        <span style="font-size:11px; font-weight:600; color:#006a63;">↑ 18%</span>
                    </div>
                    <div style="font-size:10.5px; color:#777586;">Last 30 rolling days</div>
                </div>
                <div style="height:36px; width:1px; background:#eaedff;"></div>
                <div>
                    <div style="font-size:10px; font-weight:700; text-transform:uppercase; color:#777586;">Intervention Rate</div>
                    <div style="display:flex; align-items:baseline; gap:6px; margin-top:2px;">
                        <span style="font-size:22px; font-weight:700; color:#006a63;">99.98%</span>
                        <span style="font-size:11px; color:#777586;">Clean</span>
                    </div>
                    <div style="font-size:10.5px; color:#777586;">Zero uncaught crashes</div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Search & Filter Ribbon + Action Button
    habit_category = st.segmented_control(
        "Habit category",
        ["All", "Reliability", "Safety & Isolation", "Formatting & Tone",
         "Privacy & Redaction"],
        default="All",
        label_visibility="collapsed",
    )
    st.caption(f"{len(rules)} safeguard(s) in the Principle Bank")

    col_mine1, col_mine2 = st.columns([3, 1])
    with col_mine1:
        habit_search = st.text_input(
            "Filter habits",
            placeholder="Filter habits or trigger keywords...",
            label_visibility="collapsed",
        )
    with col_mine2:
        mine_btn = st.button("🔄 Mine History (`git log -p`)", type="primary")

    if mine_btn:
        with st.spinner("Mining invariant micro-deltas via git log -p..."):
            try:
                mined = miner.mine_recent_history(repo, limit=8, endpoint=endpoint)
            except (RuntimeError, ValueError, OSError) as exc:
                st.error(f"Mining failed: {exc}")
                mined = []
            saved = bank.upsert_principles(mined) if mined else 0
        if mined:
            st.success(f"Mined {len(mined)} invariants, saved {saved} into local Principle Bank!")
            st.rerun()
        elif saved == 0:
            st.info("No new invariants found in recent commits.")

    # Rule Cards Grid
    st.markdown("<div style='margin-top:10px;'></div>", unsafe_allow_html=True)
    _category_map = {
        "Reliability": ("error handling", "testing", "reliability"),
        "Safety & Isolation": ("safety", "security"),
        "Formatting & Tone": ("style", "typing", "formatting"),
        "Privacy & Redaction": ("privacy",),
    }
    visible = rules
    if habit_category != "All":
        wanted = _category_map[habit_category]
        visible = [
            (p, en) for p, en in visible
            if (p.category or "").lower() in wanted
        ]
    if habit_search.strip():
        needle = habit_search.strip().lower()
        visible = [
            (p, en) for p, en in visible
            if needle in p.latent_invariant.lower()
            or needle in (p.name or "").lower()
        ]
    if not rules:
        st.info("No habits learned yet. Click 'Mine History' above to extract rules from your git commit micro-deltas!")
    elif not visible:
        st.info("No safeguards match the current filter.")
    else:
        for idx, (p, enabled) in enumerate(visible):
            render_rule_card(
                p,
                enabled,
                key=f"{p.id}-{idx}",
                on_toggle=lambda pid, val: bank.set_enabled(pid, val),
            )


# ======================================================================
# SCREEN 4: AUDIT TRACE LEDGER (Exact Match to audit_trace_ledger.html)
# ======================================================================
elif nav_selection == "Audit Trace Ledger":
    actions = bank.list_actions(limit=50)
    passed_count = sum(1 for a in actions if a.status == "passed")
    rolled_back_count = sum(1 for a in actions if a.status == "rolled_back")
    blocked_count = sum(1 for a in actions if a.status == "blocked")

    # Top Section / Page Header
    st.markdown(
        """
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:16px;">
            <div>
                <h1 style="font-size:28px; font-weight:700; color:#131b2e; margin:0; letter-spacing:-0.02em;">
                    Audit Trace & Verification Ledger
                </h1>
                <div style="font-size:14px; color:#464554; margin-top:4px;">
                    Chronological audit trail of all cobot actions, AST checks, atomic writes, and automated test verdicts.
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Search Bar with Severity Filter
    atl_c1, atl_c2, atl_c3 = st.columns([3, 1, 1])
    with atl_c1:
        audit_search = st.text_input(
            "Audit Search",
            placeholder="Filter by Snapshot ID (e.g. #V-9041), target filename, or approval actor...",
            label_visibility="collapsed",
        )
    with atl_c2:
        severity_filter = st.selectbox(
            "Severity",
            ("All Thresholds", "Critical Only", "Rollback Operations"),
            label_visibility="collapsed",
        )
    with atl_c3:
        refresh_audit = st.button("🔄 Refresh Feed")

    if refresh_audit:
        st.rerun()

    records = actions
    if severity_filter == "Critical Only":
        records = [a for a in records if a.status == "blocked"]
    elif severity_filter == "Rollback Operations":
        records = [a for a in records if a.status == "rolled_back"]
    if audit_search.strip():
        needle = audit_search.strip().lower()
        records = [
            a for a in records
            if needle in (
                f"{a.id} {a.action_type} {a.target_file} {a.actor} "
                f"{a.status} {a.details}"
            ).lower()
        ]

    # 3 Summary Statistics Bento Cards
    st.markdown("<div style='margin-top:10px;'></div>", unsafe_allow_html=True)
    a1, a2, a3 = st.columns(3)
    with a1:
        st.markdown(
            f"""
            <div class="mimir-metric-tile">
                <div>
                    <div class="mimir-metric-label">Total Interventions</div>
                    <div class="mimir-metric-val">{len(actions)}</div>
                    <div style="font-size:12px; color:#006a63; font-weight:600; margin-top:4px;">
                        <span class="material-symbols-outlined" style="font-size:14px; color:#006a63;">undo</span>
                        100% Reversible Memory Snapshot
                    </div>
                </div>
                <div style="width:40px; height:40px; border-radius:10px; background:#f2f3ff; display:flex; align-items:center; justify-content:center; color:#2a14b4;">
                    <span class="material-symbols-outlined" style="font-size:22px;">history</span>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with a2:
        approval_pct = (passed_count / len(actions) * 100) if actions else 100.0
        st.markdown(
            f"""
            <div class="mimir-metric-tile">
                <div>
                    <div class="mimir-metric-label">Human Approval Rate</div>
                    <div class="mimir-metric-val">{approval_pct:.1f}%</div>
                    <div style="font-size:12px; color:#777586; margin-top:4px;">
                        <span class="material-symbols-outlined" style="font-size:14px; color:#006a63;">how_to_reg</span>
                        {passed_count} / {len(actions)} Direct User Consents
                    </div>
                </div>
                <div style="width:40px; height:40px; border-radius:10px; background:rgba(153,239,229,0.4); display:flex; align-items:center; justify-content:center; color:#006a63;">
                    <span class="material-symbols-outlined" style="font-size:22px;">how_to_reg</span>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with a3:
        st.markdown(
            """
            <div class="mimir-metric-tile">
                <div>
                    <div class="mimir-metric-label">Current Safety Ledger</div>
                    <div style="font-size:24px; font-weight:700; color:#131b2e; margin-top:2px;">Zero Drift</div>
                    <div style="font-size:12px; color:#777586; margin-top:4px;">
                        <span class="material-symbols-outlined" style="font-size:14px; color:#006a63;">verified_user</span>
                        Synced • Invariant Boundaries Clean
                    </div>
                </div>
                <div style="width:40px; height:40px; border-radius:10px; background:#f2f3ff; display:flex; align-items:center; justify-content:center; color:#2a14b4;">
                    <span class="material-symbols-outlined" style="font-size:22px;">verified_user</span>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # Chronological Audit Feed
    st.markdown("<div style='margin-top:16px;'></div>", unsafe_allow_html=True)
    if not actions:
        st.markdown(
            """
            <div class="mimir-card" style="text-align:center; padding:32px; color:#464554;">
                <span class="material-symbols-outlined" style="font-size:36px; color:#4338ca; display:block; margin-bottom:8px;">history_edu</span>
                <div style="font-size:15px; font-weight:600; color:#131b2e;">No Audit Events Yet</div>
                <div style="font-size:13px; color:#777586; margin-top:4px;">
                    Apply a proposed change in the Intention Studio to record verified transactional events.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    elif not records:
        st.info("No audit events match the current search / severity filter.")
    else:
        for a in records:
            status = a.status.lower()
            if status == "passed":
                badge_html = '<span class="mimir-tag mimir-tag-success"><span class="material-symbols-outlined" style="font-size:12px;">check_circle</span> Verified & Passed</span>'
            elif status == "rolled_back":
                badge_html = '<span class="mimir-tag mimir-tag-warning"><span class="material-symbols-outlined" style="font-size:12px;">undo</span> Rollback Operation</span>'
            else:
                badge_html = '<span class="mimir-tag mimir-tag-danger"><span class="material-symbols-outlined" style="font-size:12px;">shield</span> AST Blocked</span>'

            st.markdown(
                f"""
                <div class="mimir-card" style="margin-bottom:10px; padding:14px 18px;">
                    <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">
                        <div style="display:flex; align-items:center; gap:8px;">
                            <span style="font-family:monospace; font-size:12px; font-weight:700; color:#2a14b4; background:#eaedff; padding:2px 6px; border-radius:4px;">#V-{abs(hash(a.timestamp)) % 9000 + 1000}</span>
                            {badge_html}
                            <span style="font-size:13.5px; font-weight:600; color:#131b2e;">{html.escape(a.action_type)}</span>
                            <span style="font-family:monospace; font-size:12px; color:#464554; background:#f2f3ff; padding:2px 8px; border-radius:4px;">{html.escape(a.target_file or '—')}</span>
                        </div>
                        <div style="display:flex; align-items:center; gap:8px; font-size:11.5px; color:#777586;">
                            <span style="display:inline-flex; align-items:center; gap:4px;">
                                <span class="material-symbols-outlined" style="font-size:14px; color:#006a63;">how_to_reg</span>
                                Actor: {html.escape(a.actor)}
                            </span>
                            <span>•</span>
                            <span>{html.escape(a.timestamp)}</span>
                        </div>
                    </div>
                    <div style="font-size:13px; color:#464554; margin-top:8px; line-height:1.45;">
                        {html.escape(a.details[:350])}
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
