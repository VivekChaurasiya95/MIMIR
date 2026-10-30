"""Observe screen: telemetry control + session feed (spec 5.2)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..presenters import (
    TIMEFRAMES,
    filter_episodes_by_timeframe,
    format_episode_detail,
)
from ..state import AppState
from ._layout import (
    ClickableCard,
    CobotVisorWidget,
    actions_row,
    badge,
    card,
    functionality_banner,
    page,
    page_head,
    setting_row,
    settings_group_card,
    toggle,
)
from ._qt import load_qt

__all__ = ["build_observe_tab", "clean_observe_error"]

FEED_CAP = 30


def clean_observe_error(msg: str) -> str:
    """Rewrite a raw observation failure as plain user guidance.

    Qt-free so headless tests can pin the wording. Dubious-ownership
    failures must read as an ownership/trust problem — never as "not a
    git repository" — because the folder IS a valid repo.
    """
    low = msg.lower()
    if "dubious ownership" in low:
        return ("Git blocked this folder (dubious ownership). "
                "Trust it below to allow observation.")
    if "not a git repository" in low or "fatal: cannot change to" in low or "no such file" in low:
        return "Selected folder is not a valid Git repository. Please pick a Git project folder."
    if "outside whitelisted observation roots" in low:
        return "This folder is outside your authorized workspace roots. Click Trust or select a project folder."
    if "(" in msg and ")" in msg:
        lead = msg.split("(")[0].strip()
        if len(lead) > 10:
            return lead
    return msg


def _session_badge(episode: Any, learned: set[str]) -> tuple[str, str]:
    if episode.transitions and all(
            t.skipped or t.binary for t in episode.transitions):
        return ("Ignored", "ignored")
    if episode.id in learned:
        return ("Analyzed", "analyzed")
    return ("Processed", "processed")


def build_observe_tab(state: AppState, status: Any,
                      navigate: Callable[[str], None], watcher: Any) -> Any:
    _, _, _, QtWidgets = load_qt()
    root, layout = page()
    layout.addWidget(page_head(
        "Observe", "Control what Habitous can see.",
        "Choose your workspace, manage observation, and review captured sessions."))

    layout.addWidget(functionality_banner(
        "👁",
        "Autonomous Session Observation",
        "Habitous watches file transitions, git commits, and terminal activity in your authorized "
        "workspace folders, converting raw developer actions into structured behavioral episodes.",
        pills=[("Git Telemetry", "confirmed"), ("Zero-Cloud Ingestion", "confirmed"), ("AST Analysis", "learning")],
        icon_cls="icon_box_purple",
    ))

    # Unboxed live status area with CobotVisorWidget avatar
    status_bar = QtWidgets.QWidget()
    status_bar.setStyleSheet("background: transparent; border: 0;")
    bl = QtWidgets.QHBoxLayout(status_bar)
    bl.setContentsMargins(2, 6, 2, 6)
    bl.setSpacing(16)

    obs_visor_face = CobotVisorWidget(width=58, height=48, on_click=lambda: on_watch())
    obs_visor_face.setToolTip("Habi Companion Visor — Click to toggle observation")
    bl.addWidget(obs_visor_face)

    banner_text_col = QtWidgets.QVBoxLayout()
    banner_text_col.setSpacing(2)
    state_line = QtWidgets.QLabel("Resting · Standby")
    state_line.setObjectName("h2")
    context_line = QtWidgets.QLabel("Your workspace isn't being observed right now.")
    context_line.setObjectName("muted")
    banner_text_col.addWidget(state_line)
    banner_text_col.addWidget(context_line)
    bl.addLayout(banner_text_col, 1)

    watch_btn = QtWidgets.QPushButton("")
    watch_btn.setObjectName("start_obs_btn")
    watch_btn.setFixedHeight(34)
    bl.addWidget(watch_btn)
    layout.addWidget(status_bar)

    error_box, error_lay = card()
    error_title = QtWidgets.QLabel("Observation needs attention")
    error_title.setObjectName("h2")
    error_text = QtWidgets.QLabel("")
    error_text.setObjectName("muted")
    error_text.setWordWrap(True)
    trust_btn = QtWidgets.QPushButton("Authorize & retry")
    trust_btn.setObjectName("ink")
    trust_btn.setToolTip("Authorize this folder and retry observation.")
    dismiss_btn = QtWidgets.QPushButton("Dismiss")
    dismiss_btn.setObjectName("soft")
    error_lay.addWidget(error_title)
    error_lay.addWidget(error_text)
    error_lay.addLayout(actions_row(trust_btn, dismiss_btn))
    error_box.setVisible(False)
    layout.addWidget(error_box)

    seg_row = QtWidgets.QHBoxLayout()
    seg_bar = QtWidgets.QWidget()
    seg_bar.setObjectName("segment_bar")
    seg_lay = QtWidgets.QHBoxLayout(seg_bar)
    seg_lay.setContentsMargins(4, 4, 4, 4)
    seg_lay.setSpacing(0)
    sessions_btn = QtWidgets.QPushButton("Sessions")
    sessions_btn.setObjectName("segment")
    sessions_btn.setCheckable(True)
    sessions_btn.setChecked(True)
    apps_btn = QtWidgets.QPushButton("Watched Folders")
    apps_btn.setObjectName("segment")
    apps_btn.setCheckable(True)
    filters_btn = QtWidgets.QPushButton("Workspace")
    filters_btn.setObjectName("segment")
    filters_btn.setCheckable(True)
    seg_group = QtWidgets.QButtonGroup(root)
    seg_group.setExclusive(True)
    seg_group.addButton(sessions_btn)
    seg_group.addButton(apps_btn)
    seg_group.addButton(filters_btn)
    seg_lay.addWidget(sessions_btn)
    seg_lay.addWidget(apps_btn)
    seg_lay.addWidget(filters_btn)
    seg_row.addWidget(seg_bar)
    seg_row.addStretch(1)
    timeframe = QtWidgets.QComboBox()
    timeframe.addItems(list(TIMEFRAMES))
    timeframe.setCurrentText("All time")
    timeframe.setMaximumWidth(160)
    seg_row.addWidget(timeframe)
    layout.addLayout(seg_row)

    stack = QtWidgets.QStackedWidget()
    sessions_page = QtWidgets.QWidget()
    sessions_lay = QtWidgets.QVBoxLayout(sessions_page)
    sessions_lay.setContentsMargins(0, 0, 0, 0)
    sessions_lay.setSpacing(10)
    feed_note = QtWidgets.QLabel("")
    feed_note.setObjectName("muted")
    feed_note.setVisible(False)
    sessions_lay.addWidget(feed_note)
    feed_box = QtWidgets.QVBoxLayout()
    feed_box.setSpacing(10)
    sessions_lay.addLayout(feed_box)
    sessions_lay.addStretch(1)
    stack.addWidget(sessions_page)

    apps_page = QtWidgets.QWidget()
    apps_lay = QtWidgets.QVBoxLayout(apps_page)
    apps_lay.setContentsMargins(0, 0, 0, 0)
    apps_lay.setSpacing(10)
    apps_hint = QtWidgets.QLabel(
        "Folders Habitous may observe. Switch one off to exclude it from "
        "watching without deleting it.")
    apps_hint.setObjectName("muted")
    apps_hint.setWordWrap(True)
    apps_lay.addWidget(apps_hint)
    scopes_box = QtWidgets.QVBoxLayout()
    scopes_box.setSpacing(10)
    apps_lay.addLayout(scopes_box)
    add_btn = QtWidgets.QPushButton("+ Add folder")
    add_btn.setObjectName("soft")
    apps_lay.addLayout(actions_row(add_btn))
    apps_lay.addStretch(1)
    stack.addWidget(apps_page)

    filters_page = QtWidgets.QWidget()
    filters_lay = QtWidgets.QVBoxLayout(filters_page)
    filters_lay.setContentsMargins(0, 0, 0, 0)
    filters_lay.setSpacing(12)

    ws_card, ws_box = settings_group_card(
        "Workspace & Ingestion Configuration",
        "Specify the local Git repository for Habitous to analyze and observe.",
    )
    ws_card.setMaximumWidth(700)
    repo_row = QtWidgets.QHBoxLayout()
    repo_row.setSpacing(8)
    repo_edit = QtWidgets.QLineEdit(state.prefs.last_repo)
    repo_edit.setPlaceholderText("Repository path (must be a git checkout)")
    repo_browse = QtWidgets.QPushButton("Browse…")
    repo_browse.setObjectName("soft")
    repo_browse.setFixedHeight(34)
    repo_row.addWidget(repo_edit, 1)
    repo_row.addWidget(repo_browse)

    repo_status_lbl = QtWidgets.QLabel("")
    repo_status_lbl.setObjectName("muted")
    repo_status_lbl.setWordWrap(True)

    repo_col = QtWidgets.QVBoxLayout()
    repo_col.setSpacing(4)
    repo_col.addLayout(repo_row)
    repo_col.addWidget(repo_status_lbl)

    commits_spin = QtWidgets.QSpinBox()
    commits_spin.setRange(1, 100)
    commits_spin.setValue(10)
    commits_spin.setSuffix(" commits")
    commits_spin.setMaximumWidth(140)
    commits_spin.setFixedHeight(34)

    ws_box.addWidget(setting_row(
        "Target Git Repository",
        "Select the project directory containing your Git commit history.",
        repo_col,
    ))
    ws_box.addWidget(setting_row(
        "Commit Analysis Depth",
        "Maximum number of recent Git commits to analyze for developer style patterns.",
        commits_spin,
    ))

    once_btn = QtWidgets.QPushButton("Observe now")
    once_btn.setObjectName("soft")
    once_btn.setFixedHeight(34)
    learn_btn = QtWidgets.QPushButton("🧠 Learn Principles")
    learn_btn.setObjectName("ink")
    learn_btn.setFixedHeight(34)
    view_btn = QtWidgets.QPushButton("View in Memory →")
    view_btn.setObjectName("soft")
    view_btn.setFixedHeight(34)
    view_btn.setEnabled(False)
    ws_box.addLayout(actions_row(once_btn, learn_btn, view_btn))

    feedback_banner = QtWidgets.QLabel("")
    feedback_banner.setWordWrap(True)
    feedback_banner.setVisible(False)
    ws_box.addWidget(feedback_banner)
    filters_lay.addWidget(ws_card)

    guard_note = QtWidgets.QLabel("🔒 Habitous automatically filters secret tokens, API keys, and binaries locally on this device.")
    guard_note.setObjectName("subtle")
    filters_lay.addWidget(guard_note)

    filters_lay.addStretch(1)
    stack.addWidget(filters_page)

    sessions_btn.clicked.connect(lambda: stack.setCurrentIndex(0))
    apps_btn.clicked.connect(lambda: stack.setCurrentIndex(1))
    filters_btn.clicked.connect(lambda: stack.setCurrentIndex(2))

    layout.addWidget(stack)
    layout.addStretch(1)

    def save_prefs() -> None:
        state.prefs.last_repo = repo_edit.text().strip()
        try:
            state.prefs.save()
        except OSError:
            pass

    def current_roots() -> list[str]:
        from pathlib import Path
        roots = list(state.prefs.active_roots(state.default_roots()))
        repo_text = repo_edit.text().strip() or state.prefs.last_repo
        if repo_text:
            try:
                repo_resolved = str(Path(repo_text).resolve())
                if repo_resolved not in roots:
                    roots.append(repo_resolved)
                import subprocess
                res = subprocess.run(
                    ["git", "-C", repo_resolved, "rev-parse", "--show-toplevel"],
                    capture_output=True, text=True, timeout=5,
                )
                if res.returncode == 0 and res.stdout.strip():
                    git_toplevel = str(Path(res.stdout.strip()).resolve())
                    if git_toplevel not in roots:
                        roots.append(git_toplevel)
                last_roots = list(state.prefs.last_roots)
                if repo_resolved not in last_roots:
                    last_roots.append(repo_resolved)
                state.prefs.last_roots = tuple(last_roots)
                state.prefs.save()
            except Exception:
                pass
        return roots

    def clear_box(box: Any) -> None:
        while box.count():
            item = box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def episode_meta(episode: Any) -> str:
        added = sum(t.additions for t in episode.transitions)
        deleted = sum(t.deletions for t in episode.transitions)
        day = episode.observed_at[:10]
        return (f"{episode.repo} · {day} · "
                f"{len(episode.transitions)} files · +{added}/−{deleted}")

    def show_detail(episode: Any) -> None:
        box = QtWidgets.QMessageBox(root)
        box.setWindowTitle("Session detail")
        box.setText(episode.summary or "(no summary)")
        box.setDetailedText(format_episode_detail(episode))
        box.exec()

    def render_feed() -> None:
        clear_box(feed_box)
        episodes = filter_episodes_by_timeframe(
            state.episodes, timeframe.currentText())
        feed_note.setText(
            f"{len(episodes)} session(s) · {timeframe.currentText().lower()}"
            + ("" if len(episodes) <= FEED_CAP
               else f" · showing first {FEED_CAP}"))
        feed_note.setVisible(True)
        if not episodes:
            empty_box, empty_lay = card()
            empty_title = QtWidgets.QLabel("No sessions yet")
            empty_title.setObjectName("h2")
            empty_hint = QtWidgets.QLabel(
                "Choose your workspace folder below, then press Observe "
                "to capture your first sessions from real git history.")
            empty_hint.setObjectName("muted")
            empty_hint.setWordWrap(True)
            goto_btn = QtWidgets.QPushButton("Choose workspace →")
            goto_btn.setObjectName("ink")
            goto_btn.clicked.connect(
                lambda: (stack.setCurrentIndex(2),
                         filters_btn.setChecked(True)))
            empty_lay.addWidget(empty_title)
            empty_lay.addWidget(empty_hint)
            empty_lay.addLayout(actions_row(goto_btn))
            feed_box.addWidget(empty_box)
            return
        for episode in episodes[:FEED_CAP]:
            label, kind = _session_badge(episode, state.learned_ids)
            row = ClickableCard(
                "session_row", on_click=lambda e=episode: show_detail(e))
            lay = QtWidgets.QHBoxLayout(row)
            lay.setContentsMargins(16, 12, 16, 12)
            lay.setSpacing(12)
            glyph = QtWidgets.QLabel("◉")
            glyph.setObjectName("h2")
            text = QtWidgets.QVBoxLayout()
            text.setSpacing(0)
            title = QtWidgets.QLabel(episode.summary or "(no summary)")
            title.setObjectName("h2")
            meta = QtWidgets.QLabel(episode_meta(episode))
            meta.setObjectName("muted")
            text.addWidget(title)
            text.addWidget(meta)
            chevron = QtWidgets.QLabel(">")
            chevron.setObjectName("faint")
            lay.addWidget(glyph)
            lay.addLayout(text, 1)
            lay.addWidget(badge(label, kind))
            lay.addWidget(chevron)
            feed_box.addWidget(row)

    def render_scopes() -> None:
        clear_box(scopes_box)
        from pathlib import Path
        roots = list(state.prefs.last_roots) or state.default_roots()
        valid_roots = [r for r in roots if Path(r).exists() or r in state.default_roots()]
        inactive = set(state.prefs.inactive_roots)
        for root_path in valid_roots:
            row = QtWidgets.QFrame()
            row.setObjectName("scope_row")
            row.setMaximumWidth(700)
            lay = QtWidgets.QHBoxLayout(row)
            lay.setContentsMargins(16, 12, 16, 12)
            lay.setSpacing(12)
            glyph = QtWidgets.QLabel("◫")
            glyph.setObjectName("h2")
            text = QtWidgets.QVBoxLayout()
            text.setSpacing(0)
            folder_name = Path(root_path).name or root_path
            title = QtWidgets.QLabel(folder_name)
            title.setObjectName("h2")
            meta = QtWidgets.QLabel(root_path)
            meta.setObjectName("muted")
            text.addWidget(title)
            text.addWidget(meta)
            switch = toggle(root_path not in inactive)
            switch.toggled.connect(
                lambda on, p=root_path: on_scope_toggle(p, on))
            lay.addWidget(glyph)
            lay.addLayout(text, 1)
            lay.addWidget(switch)
            scopes_box.addWidget(row)

    def on_scope_toggle(path: str, on: bool) -> None:
        inactive = set(state.prefs.inactive_roots)
        if on:
            inactive.discard(path)
        else:
            inactive.add(path)
        state.prefs.inactive_roots = tuple(sorted(inactive))
        save_prefs()
        status.showMessage(
            f"{'Watching' if on else 'Ignoring'} {path}.", 4000)

    def on_add_folder() -> None:
        chosen = QtWidgets.QFileDialog.getExistingDirectory(
            root, "Add watched folder", ".")
        if chosen:
            roots = list(state.prefs.last_roots)
            if chosen not in roots:
                roots.append(chosen)
            state.prefs.last_roots = tuple(roots)
            save_prefs()
            render_scopes()

    def refresh_banner() -> None:
        if watcher.watching:
            repo_name = Path(state.prefs.last_repo).name if state.prefs.last_repo else "workspace"
            state_line.setText("Observing")
            context_line.setText(
                f"Recording telemetry from {repo_name}.")
            watch_btn.setText("⏹ Stop Observing")
            watch_btn.setStyleSheet(
                "background: #DC2626; color: white; border: 0; border-radius: 8px; "
                "padding: 6px 16px; font-weight: 600; font-size: 13px; min-height: 22px;")
            obs_visor_face.set_state("observing")
        else:
            state_line.setText("Resting · Standby")
            context_line.setText(
                "Your workspace isn't being observed right now.")
            watch_btn.setText("▷ Start Observing")
            watch_btn.setStyleSheet(
                "background: #4F46E5; color: white; border: 0; border-radius: 8px; "
                "padding: 6px 16px; font-weight: 600; font-size: 13px; min-height: 22px;")
            obs_visor_face.set_state("resting")

    def show_error(message: str) -> None:
        cleaned = clean_observe_error(message)
        error_text.setText(cleaned)
        low = message.lower()
        if "outside" in low or "authorized" in low or "boundary" in low:
            trust_btn.setText("Authorize folder & observe")
            trust_btn.setVisible(True)
        elif "dubious ownership" in low:
            trust_btn.setText("Trust in Git & retry")
            trust_btn.setVisible(True)
        else:
            trust_btn.setVisible(False)
        error_box.setVisible(True)

    def hide_error() -> None:
        error_box.setVisible(False)
        try:
            watcher.last_error = ""
        except AttributeError:
            pass

    def sync_error() -> None:
        pending = getattr(watcher, "last_error", "")
        if pending:
            show_error(pending)

    def chime() -> None:
        try:
            if getattr(state.prefs, "chimes", True):
                QtWidgets.QApplication.beep()
        except Exception:
            pass

    def validate_repo_path() -> tuple[bool, str]:
        text = repo_edit.text().strip()
        if not text:
            repo_status_lbl.setText("ℹ️ Enter or browse to a local Git repository")
            repo_status_lbl.setStyleSheet("color: #64748B; font-size: 12px; margin-top: 4px;")
            return False, "empty"
        from pathlib import Path
        p = Path(text)
        if not p.exists():
            repo_status_lbl.setText(f"⚠️ Folder not found: {text}")
            repo_status_lbl.setStyleSheet("color: #EF4444; font-size: 12px; font-weight: 500; margin-top: 4px;")
            return False, "not_found"
        import subprocess
        try:
            res = subprocess.run(
                ["git", "-C", str(p), "rev-parse", "--show-toplevel"],
                capture_output=True, text=True, timeout=5,
            )
            if res.returncode != 0:
                repo_status_lbl.setText("⚠️ Not a Git repository (no .git directory found)")
                repo_status_lbl.setStyleSheet("color: #F59E0B; font-size: 12px; font-weight: 500; margin-top: 4px;")
                return False, "not_git"
            toplevel = res.stdout.strip()
            c_res = subprocess.run(
                ["git", "-C", str(p), "rev-list", "--count", "HEAD"],
                capture_output=True, text=True, timeout=5,
            )
            count = c_res.stdout.strip() if c_res.returncode == 0 else "?"
            repo_status_lbl.setText(f"✓ Valid Git repository: {Path(toplevel).name} ({count} commits found)")
            repo_status_lbl.setStyleSheet("color: #10B981; font-size: 12px; font-weight: 500; margin-top: 4px;")
            return True, toplevel
        except Exception as exc:
            repo_status_lbl.setText(f"⚠️ Git error: {exc}")
            repo_status_lbl.setStyleSheet("color: #EF4444; font-size: 12px; margin-top: 4px;")
            return False, str(exc)

    _trust_workers: list[Any] = []

    def on_trust_repo() -> None:
        repo = repo_edit.text().strip() or state.prefs.last_repo or "."
        from pathlib import Path
        try:
            repo_resolved = str(Path(repo).resolve())
        except Exception:
            repo_resolved = repo

        pending_err = getattr(watcher, "last_error", "") or error_text.text()
        low = pending_err.lower()

        # Workspace root authorization error: authorize directly without scary git config
        if "outside" in low or "authorized" in low or "boundary" in low:
            roots = list(state.prefs.last_roots)
            if repo_resolved not in roots:
                roots.append(repo_resolved)
            import subprocess
            try:
                res = subprocess.run(
                    ["git", "-C", repo_resolved, "rev-parse", "--show-toplevel"],
                    capture_output=True, text=True, timeout=5,
                )
                if res.returncode == 0 and res.stdout.strip():
                    top = str(Path(res.stdout.strip()).resolve())
                    if top not in roots:
                        roots.append(top)
            except Exception:
                pass
            state.prefs.last_roots = tuple(roots)
            inact = [r for r in state.prefs.inactive_roots if r != repo_resolved]
            state.prefs.inactive_roots = tuple(inact)
            state.prefs.last_repo = repo_resolved
            try:
                state.prefs.save()
            except Exception:
                pass
            hide_error()
            status.showMessage(f"Authorized {Path(repo_resolved).name} — observing now…", 4000)
            on_observe_now()
            return

        if _trust_workers:
            status.showMessage("Already trusting a folder…", 4000)
            return
        confirm = QtWidgets.QMessageBox(root)
        confirm.setWindowTitle("Trust this folder?")
        confirm.setText(
            f"Git refuses to read this repository until it is marked safe.\n\n"
            f"This runs:\n"
            f"git config --global --add safe.directory {repo}\n\n"
            f"Only trust folders you own.")
        confirm.setStandardButtons(
            QtWidgets.QMessageBox.StandardButton.Cancel
            | QtWidgets.QMessageBox.StandardButton.Ok)
        confirm.button(QtWidgets.QMessageBox.StandardButton.Ok).setText("Trust")
        if confirm.exec() != QtWidgets.QMessageBox.StandardButton.Ok:
            return
        from ..observe_worker import create_callable_worker, run_trust_repo
        trust_btn.setEnabled(False)
        status.showMessage("Marking folder safe…", 4000)
        worker = create_callable_worker(run_trust_repo, (repo,), parent=root)
        _trust_workers.append(worker)

        def _release() -> None:
            trust_btn.setEnabled(True)
            try:
                _trust_workers.remove(worker)
            except ValueError:
                pass
            try:
                worker.deleteLater()
            except Exception:
                pass

        def _done(_repo: object) -> None:
            _release()
            hide_error()
            status.showMessage(
                "Folder trusted — retrying observation.", 4000)
            on_observe_now()

        def _failed(message: str) -> None:
            _release()
            status.showMessage(f"Could not mark folder safe: {message}",
                               8000)

        def _cancelled() -> None:
            _release()
            status.showMessage("Trust cancelled.", 4000)

        worker.callable_finished.connect(_done)
        worker.callable_error.connect(_failed)
        worker.callable_cancelled.connect(_cancelled)
        worker.start()

    def on_watch() -> None:
        if watcher.watching:
            watcher.stop()
            refresh_banner()
            return
        repo = repo_edit.text().strip() or state.prefs.last_repo
        if not repo:
            status.showMessage("Choose a workspace repo first.", 5000)
            repo_status_lbl.setText("⚠️ Please enter or browse to a workspace repo first.")
            repo_status_lbl.setStyleSheet("color: #EF4444; font-size: 12px; font-weight: 500; margin-top: 4px;")
            return
        from pathlib import Path
        try:
            repo_resolved = str(Path(repo).resolve())
        except Exception:
            repo_resolved = repo
        save_prefs()
        roots = current_roots()
        if repo_resolved not in roots:
            roots.append(repo_resolved)

        def _started(ok: bool, message: str) -> None:
            if ok:
                hide_error()
                render_feed()
                render_scopes()
            else:
                try:
                    watcher.last_error = message
                except AttributeError:
                    pass
                show_error(f"Couldn't start observing: {message}")
                status.showMessage(
                    f"Couldn't start observing: {message}", 8000)
            watch_btn.setEnabled(True)
            refresh_banner()

        start_async = getattr(watcher, "start_async", None)
        if start_async is None:
            ok, message = watcher.start(repo_resolved, roots,
                                        commits_spin.value())
            _started(ok, message)
            return
        watch_btn.setEnabled(False)
        status.showMessage("Starting observation…", 4000)
        start_async(repo_resolved, roots, commits_spin.value(), _started)

    def on_observe_now(on_success: Optional[Callable[[], None]] = None) -> None:
        repo = repo_edit.text().strip() or state.prefs.last_repo or "."
        from pathlib import Path
        try:
            repo_resolved = str(Path(repo).resolve())
        except Exception:
            repo_resolved = repo
        save_prefs()
        roots = current_roots()
        if repo_resolved not in roots:
            roots.append(repo_resolved)

        feedback_banner.setVisible(False)
        once_btn.setEnabled(False)
        once_btn.setText("⏳ Observing…")
        obs_visor_face.set_state("observing")
        QtWidgets.QApplication.processEvents()
        status.showMessage(f"Analyzing {Path(repo_resolved).name}…", 4000)

        def _cleanup() -> None:
            once_btn.setEnabled(True)
            once_btn.setText("Observe now")

        def _observe_done(episodes: Any, repo_path: str, cb: Optional[Callable[[], None]] = None) -> None:
            _cleanup()
            obs_visor_face.set_state("resting")
            hide_error()
            count = len(episodes) if episodes else 0
            feedback_banner.setText(
                f"✓ Successfully observed {count} commit episode(s) from {Path(repo_path).name}! "
                f"Ready to Learn Principles."
            )
            feedback_banner.setStyleSheet(
                "color: #065F46; font-size: 13px; font-weight: 500; background: #ECFDF5; "
                "border: 1px solid #A7F3D0; border-radius: 8px; padding: 10px 14px; margin-top: 6px;"
            )
            feedback_banner.setVisible(True)
            view_btn.setEnabled(True)
            status.showMessage(f"Observed {count} episode(s) from {Path(repo_path).name}.", 5000)
            render_feed()
            render_scopes()
            if cb is not None:
                cb()

        def _observe_fail(message: str) -> None:
            _cleanup()
            obs_visor_face.set_state("alert")
            try:
                watcher.last_error = message
            except AttributeError:
                pass
            feedback_banner.setText(f"Observation failed: {clean_observe_error(message)}")
            feedback_banner.setStyleSheet(
                "color: #991B1B; font-size: 13px; font-weight: 500; background: #FEF2F2; "
                "border: 1px solid #FECACA; border-radius: 8px; padding: 10px 14px; margin-top: 6px;"
            )
            feedback_banner.setVisible(True)
            show_error(f"Observe failed: {message}")
            status.showMessage(f"Observe failed: {message}", 8000)

        try:
            episodes = state.observe(repo_resolved, roots, commits_spin.value())
            _observe_done(episodes, repo_resolved, on_success)
        except Exception as exc:
            _observe_fail(str(exc))

    def _do_learn() -> None:
        if not state.episodes:
            status.showMessage("No commit history found to learn from. Try increasing commit depth.", 6000)
            feedback_banner.setText("⚠️ No commit history found to analyze. Please choose a repository with commits.")
            feedback_banner.setStyleSheet(
                "color: #9A3412; font-size: 13px; font-weight: 500; background: #FFF7ED; "
                "border: 1px solid #FED7AA; border-radius: 8px; padding: 10px 14px; margin-top: 6px;"
            )
            feedback_banner.setVisible(True)
            return
        learn_btn.setEnabled(False)
        learn_btn.setText("🧠 Mining principles…")
        obs_visor_face.set_state("thinking")
        status.showMessage("Inductive Detective mining behavioral principles…", 4000)
        try:
            principles = state.learn(min_cluster_size=1)
        except Exception as exc:
            learn_btn.setEnabled(True)
            learn_btn.setText("🧠 Learn Principles")
            obs_visor_face.set_state("alert")
            status.showMessage(f"Learn failed: {exc}", 8000)
            feedback_banner.setText(f"Learn failed: {exc}")
            feedback_banner.setStyleSheet(
                "color: #991B1B; font-size: 13px; font-weight: 500; background: #FEF2F2; "
                "border: 1px solid #FECACA; border-radius: 8px; padding: 10px 14px; margin-top: 6px;"
            )
            feedback_banner.setVisible(True)
            return

        learn_btn.setEnabled(True)
        learn_btn.setText("🧠 Learn Principles")
        obs_visor_face.set_state("resting")
        count = len(principles)
        feedback_banner.setText(
            f"🎉 Successfully learned {count} behavioral principle(s)! Click 'View in Memory →' to inspect."
        )
        feedback_banner.setStyleSheet(
            "color: #065F46; font-size: 13px; font-weight: 600; background: #ECFDF5; "
            "border: 1px solid #A7F3D0; border-radius: 8px; padding: 10px 14px; margin-top: 6px;"
        )
        feedback_banner.setVisible(True)
        view_btn.setEnabled(True)
        view_btn.setStyleSheet(
            "background: #4F46E5; color: white; border: 0; border-radius: 8px; "
            "padding: 6px 16px; font-weight: 600; font-size: 13px; min-height: 22px;"
        )
        status.showMessage(f"Learned {count} principle(s) stored in Behavioral Memory.", 6000)
        chime()
        render_feed()

    def on_learn() -> None:
        if not state.episodes:
            feedback_banner.setText(
                "ℹ️ No captured episodes yet. Ingesting git commits first, then learning principles…"
            )
            feedback_banner.setStyleSheet(
                "color: #3730A3; font-size: 13px; font-weight: 500; background: #EEF2FF; "
                "border: 1px solid #C7D2FE; border-radius: 8px; padding: 10px 14px; margin-top: 6px;"
            )
            feedback_banner.setVisible(True)
            on_observe_now(on_success=_do_learn)
            return
        _do_learn()

    def on_browse_repo() -> None:
        chosen = QtWidgets.QFileDialog.getExistingDirectory(
            root, "Choose repository", repo_edit.text().strip() or ".")
        if chosen:
            repo_edit.setText(chosen)
            save_prefs()
            current_roots()
            render_scopes()
            validate_repo_path()
            hide_error()

    repo_edit.textChanged.connect(lambda _: validate_repo_path())
    timeframe.currentTextChanged.connect(lambda _t: render_feed())
    repo_browse.clicked.connect(on_browse_repo)
    watch_btn.clicked.connect(on_watch)
    once_btn.clicked.connect(on_observe_now)
    learn_btn.clicked.connect(on_learn)
    view_btn.clicked.connect(lambda: navigate("memory"))
    add_btn.clicked.connect(on_add_folder)
    trust_btn.clicked.connect(on_trust_repo)
    dismiss_btn.clicked.connect(hide_error)

    def refresh_content() -> None:
        refresh_banner()
        render_feed()
        render_scopes()
        sync_error()
        validate_repo_path()
        try:
            if state.store.list_all():
                view_btn.setEnabled(True)
                view_btn.setStyleSheet(
                    "background: #4F46E5; color: white; border: 0; border-radius: 8px; "
                    "padding: 6px 16px; font-weight: 600; font-size: 13px; min-height: 22px;"
                )
        except Exception:
            pass

    root.refresh_content = refresh_content
    refresh_content()
    return root
