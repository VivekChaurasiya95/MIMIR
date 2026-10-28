"""Replicate screen: style synthesis + recent tasks (spec 5.4)."""

from __future__ import annotations

from typing import Any

from ..presenters import format_replication_report, rel_time_ago
from ..state import AppState
from ._layout import (
    ClickableCard,
    actions_row,
    badge,
    card,
    overline,
    page,
    page_head,
)
from ._qt import load_qt

__all__ = ["build_replicate_tab"]

TOP_K_OPTIONS = (1, 3, 5, 10)
FEED_PREVIEW = 5


def build_replicate_tab(state: AppState, status: Any) -> Any:
    _, _, _, QtWidgets = load_qt()
    root, layout = page()
    layout.addWidget(page_head(
        "Replicate", "Apply your style to new tasks.",
        "Describe what you want, and MIMIR will use your learned principles."))

    prompt_box, prompt_lay = card()
    prompt_head = QtWidgets.QHBoxLayout()
    prompt_label = QtWidgets.QLabel("What would you like MIMIR to create or change?")
    prompt_label.setObjectName("h2")
    mic_btn = QtWidgets.QPushButton("🎙")
    mic_btn.setObjectName("mic_btn")
    mic_btn.setFixedSize(36, 36)
    mic_btn.setToolTip("Dictate instead of typing (needs a microphone)")
    prompt_head.addWidget(prompt_label, 1)
    prompt_head.addWidget(mic_btn)
    prompt_lay.addLayout(prompt_head)
    task_edit = QtWidgets.QTextEdit()
    task_edit.setObjectName("task_input")
    task_edit.setPlaceholderText("e.g. Implement an authentication flow…")
    task_edit.setFixedHeight(96)
    prompt_lay.addWidget(task_edit)

    target = QtWidgets.QHBoxLayout()
    target.setSpacing(12)
    project_combo = QtWidgets.QComboBox()
    project_combo.setEditable(True)
    project_combo.setMinimumWidth(180)
    principles_combo = QtWidgets.QComboBox()
    principles_combo.addItems(f"Auto-selected ({n})" for n in TOP_K_OPTIONS)
    conf_spin = QtWidgets.QDoubleSpinBox()
    conf_spin.setRange(0.0, 1.0)
    conf_spin.setSingleStep(0.05)
    conf_spin.setValue(state.prefs.default_min_confidence)
    conf_spin.setMaximumWidth(110)
    conf_spin.setToolTip("Principles below this confidence are ignored.")
    generate_btn = QtWidgets.QPushButton("Generate →")
    generate_btn.setObjectName("generate_btn")
    target.addWidget(QtWidgets.QLabel("Project"))
    target.addWidget(project_combo, 1)
    target.addWidget(QtWidgets.QLabel("Use principles"))
    target.addWidget(principles_combo)
    target.addWidget(QtWidgets.QLabel("Min conf"))
    target.addWidget(conf_spin)
    target.addWidget(generate_btn)
    prompt_lay.addLayout(target)
    layout.addWidget(prompt_box)

    verdict = QtWidgets.QLabel("")
    verdict.setObjectName("muted")
    layout.addWidget(verdict)

    out_box, out_lay = card()
    applied_row = QtWidgets.QHBoxLayout()
    applied_row.setSpacing(8)
    applied_title = QtWidgets.QLabel("Guided by:")
    applied_title.setObjectName("muted")
    applied_row.addWidget(applied_title)
    applied_chips = QtWidgets.QHBoxLayout()
    applied_chips.setSpacing(6)
    applied_row.addLayout(applied_chips)
    applied_row.addStretch(1)
    out_lay.addLayout(applied_row)
    output = QtWidgets.QPlainTextEdit()
    output.setReadOnly(True)
    output.setMinimumHeight(180)
    output.setPlaceholderText("Your styled draft appears here.")
    out_lay.addWidget(output)
    copy_btn = QtWidgets.QPushButton("Copy solution")
    copy_btn.setObjectName("soft")
    copy_btn.setEnabled(False)
    out_lay.addLayout(actions_row(copy_btn))
    layout.addWidget(out_box)

    feed_head = QtWidgets.QHBoxLayout()
    feed_head.addWidget(overline("Recent tasks"))
    feed_head.addStretch(1)
    view_all = QtWidgets.QPushButton("View all >")
    view_all.setObjectName("chip")
    feed_head.addWidget(view_all)
    layout.addLayout(feed_head)
    feed = QtWidgets.QVBoxLayout()
    feed.setSpacing(10)
    layout.addLayout(feed)
    layout.addStretch(1)

    last_solution = {"text": ""}
    show_all = {"on": False}

    def sync_project_items() -> None:
        seen: list[str] = []
        for task in state.prefs.recent_tasks:
            if task.get("project") and task["project"] not in seen:
                seen.append(task["project"])
        if state.prefs.last_repo:
            base = state.prefs.last_repo.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            if base and base not in seen:
                seen.append(base)
        blocked = project_combo.blockSignals(True)
        project_combo.clear()
        project_combo.addItems(seen or ["AlgoFight", "MIMIR", "Inbox"])
        project_combo.blockSignals(blocked)

    def sync_defaults() -> None:
        top_k = state.prefs.default_top_k
        label = f"Auto-selected ({top_k})"
        index = principles_combo.findText(label)
        principles_combo.setCurrentIndex(index if index >= 0 else 1)
        conf_spin.setValue(state.prefs.default_min_confidence)

    def clear_chips() -> None:
        while applied_chips.count():
            old = applied_chips.takeAt(0).widget()
            if old is not None:
                old.deleteLater()

    def set_verdict(text: str, kind: str) -> None:
        verdict.setText(text)
        verdict.setObjectName(kind)
        verdict.style().unpolish(verdict)
        verdict.style().polish(verdict)

    def render_feed() -> None:
        while feed.count():
            old = feed.takeAt(0).widget()
            if old is not None:
                old.deleteLater()
        tasks = list(state.prefs.recent_tasks)
        view_all.setVisible(len(tasks) > FEED_PREVIEW or not tasks)
        view_all.setText("Show less ∧" if show_all["on"] else
                         (f"View all ({len(tasks)}) >" if tasks else "View all >"))
        if not tasks:
            showcase_tasks = [
                ("</>", "Implement authentication flow", "AlgoFight · 2 days ago", "Completed", "completed", "icon_box_purple"),
                ("</>", "Improve error handling", "MIMIR · 4 days ago", "Completed", "completed", "icon_box_blue"),
                ("💡", "Design system ideas", "Personal Notes · 6 days ago", "Reviewed", "reviewed", "icon_box_amber"),
            ]
            for glyph_char, t_title, t_meta, t_badge, t_kind, icon_cls in showcase_tasks:
                row = ClickableCard("task_row")
                lay = QtWidgets.QHBoxLayout(row)
                lay.setContentsMargins(16, 12, 16, 12)
                lay.setSpacing(14)
                glyph = QtWidgets.QLabel(glyph_char)
                glyph.setObjectName(icon_cls)
                text = QtWidgets.QVBoxLayout()
                text.setSpacing(2)
                title = QtWidgets.QLabel(t_title)
                title.setObjectName("h2")
                meta = QtWidgets.QLabel(t_meta)
                meta.setObjectName("muted")
                text.addWidget(title)
                text.addWidget(meta)
                chevron = QtWidgets.QLabel(">")
                chevron.setObjectName("faint")
                lay.addWidget(glyph)
                lay.addLayout(text, 1)
                lay.addWidget(badge(t_badge, t_kind))
                lay.addWidget(chevron)
                feed.addWidget(row)
            return
        shown = tasks if show_all["on"] else tasks[:FEED_PREVIEW]
        for task in shown:
            done = task.get("verdict") == "Completed"
            row = ClickableCard("task_row")
            lay = QtWidgets.QHBoxLayout(row)
            lay.setContentsMargins(16, 12, 16, 12)
            lay.setSpacing(14)
            glyph = QtWidgets.QLabel("✓" if done else "◫")
            glyph.setObjectName("icon_box_green" if done else "icon_box_blue")
            text = QtWidgets.QVBoxLayout()
            text.setSpacing(2)
            title = QtWidgets.QLabel(task.get("title", ""))
            title.setObjectName("h2")
            meta = QtWidgets.QLabel(
                f"{task.get('project', '')} · "
                f"{rel_time_ago(task.get('at', ''))}")
            meta.setObjectName("muted")
            text.addWidget(title)
            text.addWidget(meta)
            chevron = QtWidgets.QLabel(">")
            chevron.setObjectName("faint")
            lay.addWidget(glyph)
            lay.addLayout(text, 1)
            lay.addWidget(badge(task.get("verdict", "Reviewed"),
                                "completed" if done else "reviewed"))
            lay.addWidget(chevron)
            feed.addWidget(row)

    def on_generate() -> None:
        task = task_edit.toPlainText().strip()
        if not task:
            status.showMessage("Describe a task first.", 4000)
            return
        top_k = TOP_K_OPTIONS[max(0, principles_combo.currentIndex())]
        try:
            result = state.replicate(
                task,
                target_scope=project_combo.currentText().strip() or None,
                min_confidence=conf_spin.value(),
                top_k=top_k,
            )
        except Exception as exc:
            status.showMessage(f"Replicate failed: {exc}", 8000)
            output.setPlainText(f"Replicate failed: {exc}")
            set_verdict("", "muted")
            copy_btn.setEnabled(False)
            return
        report = format_replication_report(result)
        clear_chips()
        for principle in result.applied_principles:
            applied_chips.addWidget(badge(principle.id, "analyzed"))
        if not result.applied_principles:
            report = ("No principles matched — the draft below is generic. "
                      "Tip: Observe a repo first, or lower Min conf.\n\n"
                      + report)
            set_verdict("Generic draft — no principles matched.", "muted")
        else:
            set_verdict(
                f"Drafted with {len(result.applied_principles)} of your "
                "principles.", "ok")
        output.setPlainText(report)
        last_solution["text"] = result.replicated_solution
        copy_btn.setEnabled(True)
        compliant = bool(result.compliance_report.get("compliant", True))
        state.prefs = state.prefs.record_task(
            task[:60], project_combo.currentText().strip(),
            "Completed" if compliant and result.applied_principles
            else "Reviewed")
        try:
            state.prefs.save()
        except OSError:
            pass
        render_feed()
        status.showMessage(
            f"Applied {len(result.applied_principles)} principle(s).", 5000)

    def on_copy() -> None:
        if last_solution["text"]:
            QtWidgets.QApplication.clipboard().setText(last_solution["text"])
            status.showMessage("Solution copied to clipboard.", 4000)

    def on_mic() -> None:
        status.showMessage("Listening… speak now.", 4000)
        ok, heard = state.try_listen(timeout_sec=8.0)
        if ok:
            task_edit.setPlainText(
                (task_edit.toPlainText() + " " + heard).strip())
            task_edit.setFocus()
        else:
            status.showMessage(f"Microphone: {heard}", 8000)

    generate_btn.clicked.connect(on_generate)
    copy_btn.clicked.connect(on_copy)
    mic_btn.clicked.connect(on_mic)
    view_all.clicked.connect(
        lambda: (show_all.update(on=not show_all["on"]), render_feed()))

    def refresh_content() -> None:
        sync_project_items()
        sync_defaults()
        render_feed()

    root.refresh_content = refresh_content
    refresh_content()
    return root
