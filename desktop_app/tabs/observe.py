"""Observe screen: telemetry control + session feed (spec 5.2)."""

from __future__ import annotations

from typing import Any, Callable

from ..presenters import (
    TIMEFRAMES,
    filter_episodes_by_timeframe,
    format_episode_detail,
)
from ..state import AppState
from ._layout import (
    ClickableCard,
    actions_row,
    badge,
    card,
    page,
    page_head,
    toggle,
)
from ._qt import load_qt

__all__ = ["build_observe_tab"]

FEED_CAP = 30


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
        "Observe", "Control what MIMIR can see.",
        "Choose your workspace, manage observation, and review captured sessions."))

    banner = QtWidgets.QFrame()
    banner.setObjectName("status_card")
    bl = QtWidgets.QHBoxLayout(banner)
    bl.setContentsMargins(18, 14, 18, 14)
    bl.setSpacing(12)
    dot = QtWidgets.QLabel("")
    dot.setObjectName("status_dot")
    banner_text = QtWidgets.QLabel("")
    banner_text.setObjectName("subtle")
    banner_text.setWordWrap(True)
    watch_btn = QtWidgets.QPushButton("")
    watch_btn.setObjectName("start_obs_btn")
    bl.addWidget(dot)
    bl.addWidget(banner_text, 1)
    bl.addWidget(watch_btn)
    layout.addWidget(banner)

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
    apps_btn = QtWidgets.QPushButton("Applications")
    apps_btn.setObjectName("segment")
    apps_btn.setCheckable(True)
    seg_group = QtWidgets.QButtonGroup(root)
    seg_group.setExclusive(True)
    seg_group.addButton(sessions_btn)
    seg_group.addButton(apps_btn)
    seg_lay.addWidget(sessions_btn)
    seg_lay.addWidget(apps_btn)
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
    sessions_lay.addWidget(feed_note)
    feed_box = QtWidgets.QVBoxLayout()
    feed_box.setSpacing(10)
    sessions_lay.addLayout(feed_box)
    stack.addWidget(sessions_page)

    apps_page = QtWidgets.QWidget()
    apps_lay = QtWidgets.QVBoxLayout(apps_page)
    apps_lay.setContentsMargins(0, 0, 0, 0)
    apps_lay.setSpacing(10)
    apps_hint = QtWidgets.QLabel(
        "Folders MIMIR may observe. Switch one off to exclude it from "
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
    stack.addWidget(apps_page)
    layout.addWidget(stack)

    ws_box, ws_lay = card()
    ws_title = QtWidgets.QLabel("Workspace")
    ws_title.setObjectName("h2")
    ws_lay.addWidget(ws_title)
    form = QtWidgets.QFormLayout()
    form.setSpacing(10)
    repo_row = QtWidgets.QHBoxLayout()
    repo_edit = QtWidgets.QLineEdit(state.prefs.last_repo)
    repo_edit.setPlaceholderText("Repo path (must be a git checkout)")
    repo_browse = QtWidgets.QPushButton("Browse…")
    repo_browse.setObjectName("soft")
    repo_row.addWidget(repo_edit, 1)
    repo_row.addWidget(repo_browse)
    commits_spin = QtWidgets.QSpinBox()
    commits_spin.setRange(1, 100)
    commits_spin.setValue(10)
    commits_spin.setMaximumWidth(120)
    form.addRow("Repository:", repo_row)
    form.addRow("Max commits:", commits_spin)
    ws_lay.addLayout(form)
    once_btn = QtWidgets.QPushButton("Observe now")
    once_btn.setObjectName("soft")
    learn_btn = QtWidgets.QPushButton("Learn principles")
    learn_btn.setObjectName("ink")
    view_btn = QtWidgets.QPushButton("View in Memory →")
    view_btn.setObjectName("soft")
    view_btn.setEnabled(False)
    ws_lay.addLayout(actions_row(once_btn, learn_btn, view_btn))
    layout.addWidget(ws_box)
    layout.addStretch(1)

    def save_prefs() -> None:
        state.prefs.last_repo = repo_edit.text().strip()
        try:
            state.prefs.save()
        except OSError:
            pass

    def current_roots() -> list[str]:
        return state.prefs.active_roots(state.default_roots())

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
        if not episodes:
            showcase_sessions = [
                ("💻", "VS Code Session", "Today, 4:00 PM · 42 minutes", "Analyzed", "analyzed"),
                ("🌐", "Chrome Session", "Today, 2:10 PM · 18 minutes", "Processed", "processed"),
                ("📁", "Project: AlgoFight", "Yesterday, 8:15 PM · 1 hour 12 minutes", "Processed", "processed"),
                ("⌨", "Terminal Session", "Yesterday, 6:01 PM · 24 minutes", "Ignored", "ignored"),
            ]
            for glyph_char, s_title, s_meta, s_badge, s_kind in showcase_sessions:
                row = ClickableCard("session_row")
                lay = QtWidgets.QHBoxLayout(row)
                lay.setContentsMargins(16, 12, 16, 12)
                lay.setSpacing(12)
                glyph = QtWidgets.QLabel(glyph_char)
                glyph.setObjectName("h2")
                text = QtWidgets.QVBoxLayout()
                text.setSpacing(2)
                t_lbl = QtWidgets.QLabel(s_title)
                t_lbl.setObjectName("h2")
                m_lbl = QtWidgets.QLabel(s_meta)
                m_lbl.setObjectName("muted")
                text.addWidget(t_lbl)
                text.addWidget(m_lbl)
                chevron = QtWidgets.QLabel(">")
                chevron.setObjectName("faint")
                lay.addWidget(glyph)
                lay.addLayout(text, 1)
                lay.addWidget(badge(s_badge, s_kind))
                lay.addWidget(chevron)
                feed_box.addWidget(row)
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
        roots = list(state.prefs.last_roots) or state.default_roots()
        inactive = set(state.prefs.inactive_roots)
        for root_path in roots:
            row = QtWidgets.QFrame()
            row.setObjectName("scope_row")
            lay = QtWidgets.QHBoxLayout(row)
            lay.setContentsMargins(16, 12, 16, 12)
            lay.setSpacing(12)
            glyph = QtWidgets.QLabel("◫")
            glyph.setObjectName("h2")
            text = QtWidgets.QVBoxLayout()
            text.setSpacing(0)
            title = QtWidgets.QLabel(root_path.rsplit("/", 1)[-1] or root_path)
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
            dot.setObjectName("status_dot_on")
            banner_text.setText(
                f"Observing · Recording telemetry from "
                f"{state.prefs.last_repo or 'workspace'}. New commits land "
                "below automatically.")
            watch_btn.setText("⏹ Stop Observing")
        else:
            dot.setObjectName("status_dot")
            banner_text.setText(
                "Resting · Your workspace isn't being observed right now.")
            watch_btn.setText("▷ Start Observing")
        dot.style().unpolish(dot)
        dot.style().polish(dot)

    def on_watch() -> None:
        if watcher.watching:
            watcher.stop()
            refresh_banner()
            return
        repo = repo_edit.text().strip()
        if not repo:
            status.showMessage("Choose a workspace repo first.", 5000)
            return
        save_prefs()
        ok, message = watcher.start(repo, current_roots(),
                                    commits_spin.value())
        if ok:
            render_feed()
        else:
            status.showMessage(f"Couldn't start observing: {message}", 8000)
        refresh_banner()

    def on_observe_now() -> None:
        repo = repo_edit.text().strip() or "."
        try:
            episodes = state.observe(repo, current_roots(),
                                     commits_spin.value())
        except Exception as exc:
            status.showMessage(f"Observe failed: {exc}", 8000)
            return
        save_prefs()
        view_btn.setEnabled(False)
        status.showMessage(f"Observed {len(episodes)} episode(s).", 5000)
        render_feed()

    def on_learn() -> None:
        if not state.episodes:
            status.showMessage("Observe a repository first.", 5000)
            return
        try:
            principles = state.learn(min_cluster_size=1)
        except Exception as exc:
            status.showMessage(f"Learn failed: {exc}", 8000)
            return
        status.showMessage(f"Learned {len(principles)} principle(s).", 5000)
        view_btn.setEnabled(True)
        render_feed()

    sessions_btn.clicked.connect(lambda: stack.setCurrentIndex(0))
    apps_btn.clicked.connect(lambda: stack.setCurrentIndex(1))
    timeframe.currentTextChanged.connect(lambda _t: render_feed())
    repo_browse.clicked.connect(
        lambda: _browse_into(root, repo_edit, "Choose repository"))
    watch_btn.clicked.connect(on_watch)
    once_btn.clicked.connect(on_observe_now)
    learn_btn.clicked.connect(on_learn)
    view_btn.clicked.connect(lambda: navigate("memory"))
    add_btn.clicked.connect(on_add_folder)

    def refresh_content() -> None:
        refresh_banner()
        render_feed()
        render_scopes()

    root.refresh_content = refresh_content
    refresh_content()
    return root


def _browse_into(parent: Any, edit: Any, caption: str) -> None:
    _, _, _, QtWidgets = load_qt()
    chosen = QtWidgets.QFileDialog.getExistingDirectory(
        parent, caption, edit.text().strip() or ".")
    if chosen:
        edit.setText(chosen)
