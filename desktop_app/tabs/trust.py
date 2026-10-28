"""Trust screen: allow-list, storage policy, wipe, filter test (spec 5.5)."""

from __future__ import annotations

from typing import Any

from ..state import AppState
from ._layout import ClickableCard, actions_row, card, page, page_head, toggle
from ._qt import load_qt

__all__ = ["build_trust_tab"]

SAMPLE = (
    "deploy.sh:\n"
    'api_key = "sk-live-9f2c4a1b"\n'
    "db_url = postgres://mimir:hunter2@db.internal:5432/app\n"
    "token = ghp_abcDEF1234567890\n"
)


def build_trust_tab(state: AppState, status: Any) -> Any:
    _, _, _, QtWidgets = load_qt()
    root, layout = page()
    layout.addWidget(page_head(
        "Trust", "Your data, your control.",
        "Manage permissions, storage, and privacy settings."))

    access_box, access_lay = card()
    access_title = QtWidgets.QLabel("Access")
    access_title.setObjectName("h2")
    access_sub = QtWidgets.QLabel("Choose what MIMIR can observe and interact with.")
    access_sub.setObjectName("muted")
    access_lay.addWidget(access_title)
    access_lay.addWidget(access_sub)
    scopes = QtWidgets.QVBoxLayout()
    scopes.setSpacing(10)
    access_lay.addLayout(scopes)
    
    add_row = ClickableCard("scope_row", on_click=lambda: on_add())
    add_row.setStyleSheet("background: transparent; border: 0;")
    al = QtWidgets.QHBoxLayout(add_row)
    al.setContentsMargins(0, 4, 0, 4)
    al.setSpacing(12)
    plus_icon = QtWidgets.QLabel("+")
    plus_icon.setObjectName("h2")
    add_label = QtWidgets.QLabel("Add application")
    add_label.setObjectName("h2")
    add_chevron = QtWidgets.QLabel(">")
    add_chevron.setObjectName("faint")
    al.addWidget(plus_icon)
    al.addWidget(add_label)
    al.addStretch(1)
    al.addWidget(add_chevron)
    access_lay.addWidget(add_row)
    layout.addWidget(access_box)

    data_box, data_lay = card()
    data_title = QtWidgets.QLabel("Data")
    data_title.setObjectName("h2")
    data_sub = QtWidgets.QLabel("See how your data is stored and managed.")
    data_sub.setObjectName("muted")
    data_lay.addWidget(data_title)
    data_lay.addWidget(data_sub)

    data_rows = QtWidgets.QVBoxLayout()
    data_rows.setSpacing(10)

    # Row 1: Storage location
    r1 = QtWidgets.QHBoxLayout()
    r1_icon = QtWidgets.QLabel("🗄")
    r1_icon.setObjectName("h2")
    r1_label = QtWidgets.QLabel("Storage location")
    r1_label.setObjectName("h2")
    r1_val = QtWidgets.QLabel("Local (This device)")
    r1_val.setObjectName("muted")
    r1_chev = QtWidgets.QLabel(">")
    r1_chev.setObjectName("faint")
    r1.addWidget(r1_icon)
    r1.addWidget(r1_label)
    r1.addStretch(1)
    r1.addWidget(r1_val)
    r1.addWidget(r1_chev)
    data_rows.addLayout(r1)

    div1 = QtWidgets.QFrame()
    div1.setObjectName("row_divider")
    data_rows.addWidget(div1)

    # Row 2: Retention period
    r2 = QtWidgets.QHBoxLayout()
    r2_icon = QtWidgets.QLabel("🕒")
    r2_icon.setObjectName("h2")
    r2_label = QtWidgets.QLabel("Retention period")
    r2_label.setObjectName("h2")
    r2_val = QtWidgets.QLabel("Until you delete")
    r2_val.setObjectName("muted")
    r2_chev = QtWidgets.QLabel(">")
    r2_chev.setObjectName("faint")
    r2.addWidget(r2_icon)
    r2.addWidget(r2_label)
    r2.addStretch(1)
    r2.addWidget(r2_val)
    r2.addWidget(r2_chev)
    data_rows.addLayout(r2)

    div2 = QtWidgets.QFrame()
    div2.setObjectName("row_divider")
    data_rows.addWidget(div2)

    # Row 3: Clear all learned data
    r3 = QtWidgets.QHBoxLayout()
    r3_icon = QtWidgets.QLabel("🗑")
    r3_icon.setObjectName("h2")
    r3_label = QtWidgets.QLabel("Clear all learned data")
    r3_label.setObjectName("h2")
    wipe_btn = QtWidgets.QPushButton("Clear")
    wipe_btn.setObjectName("clear_btn")
    r3.addWidget(r3_icon)
    r3.addWidget(r3_label)
    r3.addStretch(1)
    r3.addWidget(wipe_btn)
    data_rows.addLayout(r3)

    data_lay.addLayout(data_rows)
    layout.addWidget(data_box)

    filter_box, filter_lay = card()
    filter_title = QtWidgets.QLabel("Privacy filter test")
    filter_title.setObjectName("h2")
    filter_sub = QtWidgets.QLabel(
        "Secrets become [REDACTED:kind] at ingestion — originals are never stored.")
    filter_sub.setObjectName("muted")
    filter_sub.setWordWrap(True)
    filter_lay.addWidget(filter_title)
    filter_lay.addWidget(filter_sub)
    raw = QtWidgets.QTextEdit()
    raw.setPlaceholderText("Paste text with a fake secret, or insert the sample…")
    raw.setFixedHeight(76)
    filter_lay.addWidget(raw)
    check_btn = QtWidgets.QPushButton("Redact preview")
    check_btn.setObjectName("ink")
    sample_btn = QtWidgets.QPushButton("Insert sample")
    sample_btn.setObjectName("soft")
    filter_lay.addLayout(actions_row(check_btn, sample_btn))
    filter_verdict = QtWidgets.QLabel("")
    filter_verdict.setObjectName("muted")
    filter_verdict.setWordWrap(True)
    filter_lay.addWidget(filter_verdict)
    filter_box.setVisible(False)
    layout.addWidget(filter_box)
    layout.addStretch(1)

    def save_prefs() -> None:
        try:
            state.prefs.save()
        except OSError:
            pass

    def clear_scopes() -> None:
        while scopes.count():
            old = scopes.takeAt(0)
            widget = old.widget() or (old.layout() and None)
            if widget is not None:
                widget.deleteLater()

    def render_scopes() -> None:
        clear_scopes()
        roots = list(state.prefs.last_roots)
        inactive = set(state.prefs.inactive_roots)
        if not roots:
            apps = [
                ("🟦", "VS Code", "D:\\Projects", True),
                ("🌐", "Google Chrome", "Browsing activity (titles & structure)", True),
                ("⌨", "Terminal / Command Line", "Commands and context (no sensitive content)", False),
            ]
            for i, (glyph_char, title_str, meta_str, on) in enumerate(apps):
                if i > 0:
                    div = QtWidgets.QFrame()
                    div.setObjectName("row_divider")
                    scopes.addWidget(div)
                row = QtWidgets.QHBoxLayout()
                row.setSpacing(12)
                glyph = QtWidgets.QLabel(glyph_char)
                glyph.setObjectName("h2")
                text = QtWidgets.QVBoxLayout()
                text.setSpacing(2)
                title = QtWidgets.QLabel(title_str)
                title.setObjectName("h2")
                meta = QtWidgets.QLabel(meta_str)
                meta.setObjectName("muted")
                text.addWidget(title)
                text.addWidget(meta)
                switch = toggle(on)
                row.addWidget(glyph)
                row.addLayout(text, 1)
                row.addWidget(switch)
                scopes.addLayout(row)
            div_end = QtWidgets.QFrame()
            div_end.setObjectName("row_divider")
            scopes.addWidget(div_end)
            return

        for root_path in roots:
            row = QtWidgets.QHBoxLayout()
            row.setSpacing(12)
            lower = root_path.lower()
            glyph_char = "🟦" if "code" in lower else ("🌐" if "chrome" in lower else ("⌨" if "term" in lower else "📁"))
            glyph = QtWidgets.QLabel(glyph_char)
            glyph.setObjectName("h2")
            text = QtWidgets.QVBoxLayout()
            text.setSpacing(2)
            title = QtWidgets.QLabel(
                root_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] or root_path)
            title.setObjectName("h2")
            meta = QtWidgets.QLabel(root_path)
            meta.setObjectName("muted")
            text.addWidget(title)
            text.addWidget(meta)
            switch = toggle(root_path not in inactive)
            switch.toggled.connect(
                lambda on, p=root_path: on_scope_toggle(p, on))
            row.addWidget(glyph)
            row.addLayout(text, 1)
            row.addWidget(switch)
            scopes.addLayout(row)

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

    def on_add() -> None:
        chosen = QtWidgets.QFileDialog.getExistingDirectory(
            root, "Add watched folder", ".")
        if chosen:
            roots = list(state.prefs.last_roots)
            if chosen not in roots:
                roots.append(chosen)
            state.prefs.last_roots = tuple(roots)
            save_prefs()
            render_scopes()

    def on_wipe() -> None:
        confirm = QtWidgets.QMessageBox(root)
        confirm.setWindowTitle("Clear all learned data?")
        confirm.setText(
            "This permanently deletes every learned principle and all "
            "observed episodes. This cannot be undone.")
        confirm.setStandardButtons(
            QtWidgets.QMessageBox.StandardButton.Cancel
            | QtWidgets.QMessageBox.StandardButton.Ok)
        confirm.button(QtWidgets.QMessageBox.StandardButton.Ok).setText("Clear")
        if confirm.exec() != QtWidgets.QMessageBox.StandardButton.Ok:
            return
        removed = state.clear_memory()
        status.showMessage(f"Cleared {removed} principle(s).", 6000)

    def set_verdict(text: str, kind: str) -> None:
        filter_verdict.setText(text)
        filter_verdict.setObjectName(kind)
        filter_verdict.style().unpolish(filter_verdict)
        filter_verdict.style().polish(filter_verdict)

    def on_check() -> None:
        preview = state.redact_preview(raw.toPlainText())
        kinds = ", ".join(preview["kinds"]) or "(none)"
        if preview["count"]:
            set_verdict(
                f"Caught {preview['count']} secret(s): {kinds} — originals "
                f"discarded.\n{preview['text']}", "ok")
        else:
            set_verdict("No secrets detected — text passed through unchanged.",
                        "muted")
        status.showMessage(f"Redaction: {preview['count']} substitution(s).", 4000)

    wipe_btn.clicked.connect(on_wipe)
    sample_btn.clicked.connect(lambda: raw.setPlainText(SAMPLE))
    check_btn.clicked.connect(on_check)

    def refresh_content() -> None:
        render_scopes()

    root.refresh_content = refresh_content
    render_scopes()
    return root
