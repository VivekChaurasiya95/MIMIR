"""Memory screen: principle bank with evidence drawer (spec 5.3)."""

from __future__ import annotations

from typing import Any

from ..presenters import confidence_badge, format_principle_detail
from ..state import AppState
from ._layout import (
    ClickableCard,
    actions_row,
    badge,
    card,
    page,
    page_head,
)
from ._qt import load_qt

__all__ = ["build_memory_tab"]

CATEGORY_ICONS = {
    "architecture": "⌗",
    "testing": "✓",
    "workflow": "▤",
    "documentation": "◫",
    "refactoring": "⇄",
    "dependency": "⛓",
    "style": "✎",
}


def build_memory_tab(state: AppState, status: Any) -> Any:
    _, _, _, QtWidgets = load_qt()
    root, layout = page()
    layout.addWidget(page_head(
        "Memory", "What MIMIR knows about you.",
        "These are the principles learned from your work, with supporting evidence."))

    row = QtWidgets.QHBoxLayout()
    row.setSpacing(10)
    search_edit = QtWidgets.QLineEdit()
    search_edit.setPlaceholderText("🔍 Search principles...")
    category = QtWidgets.QComboBox()
    category.setMaximumWidth(180)
    refresh_btn = QtWidgets.QPushButton("Refresh")
    refresh_btn.setObjectName("soft")
    row.addWidget(search_edit, 1)
    row.addWidget(category)
    row.addWidget(refresh_btn)
    layout.addLayout(row)

    stack = QtWidgets.QStackedWidget()
    list_page = QtWidgets.QWidget()
    list_lay = QtWidgets.QVBoxLayout(list_page)
    list_lay.setContentsMargins(0, 0, 0, 0)
    list_lay.setSpacing(10)
    count_note = QtWidgets.QLabel("")
    count_note.setObjectName("muted")
    list_lay.addWidget(count_note)
    feed = QtWidgets.QVBoxLayout()
    feed.setSpacing(10)
    list_lay.addLayout(feed)
    list_lay.addStretch(1)
    stack.addWidget(list_page)

    detail_page = QtWidgets.QWidget()
    detail_lay = QtWidgets.QVBoxLayout(detail_page)
    detail_lay.setContentsMargins(0, 0, 0, 0)
    detail_lay.setSpacing(10)
    back_btn = QtWidgets.QPushButton("← All principles")
    back_btn.setObjectName("soft")
    detail_lay.addLayout(actions_row(back_btn))
    detail_box, detail_box_lay = card()
    detail_head = QtWidgets.QLabel("")
    detail_head.setObjectName("h2")
    detail_head.setWordWrap(True)
    detail_badge_row = QtWidgets.QHBoxLayout()
    detail_badge_slot = QtWidgets.QHBoxLayout()
    detail_badge_row.addLayout(detail_badge_slot)
    detail_badge_row.addStretch(1)
    detail_text = QtWidgets.QPlainTextEdit()
    detail_text.setReadOnly(True)
    detail_text.setMinimumHeight(260)
    detail_box_lay.addWidget(detail_head)
    detail_box_lay.addLayout(detail_badge_row)
    detail_box_lay.addWidget(detail_text)
    detail_lay.addWidget(detail_box)
    act_row = QtWidgets.QHBoxLayout()
    act_row.setSpacing(10)
    evidence_edit = QtWidgets.QLineEdit("desktop_review")
    evidence_edit.setMaximumWidth(200)
    plus_btn = QtWidgets.QPushButton("+ Reinforce")
    plus_btn.setObjectName("soft")
    plus_btn.setToolTip("Adds supporting evidence and raises confidence.")
    minus_btn = QtWidgets.QPushButton("− Contradict")
    minus_btn.setObjectName("soft")
    minus_btn.setToolTip("Records a contradiction and lowers confidence.")
    copy_btn = QtWidgets.QPushButton("Copy rule")
    copy_btn.setObjectName("soft")
    delete_btn = QtWidgets.QPushButton("Delete")
    delete_btn.setObjectName("soft")
    act_row.addWidget(QtWidgets.QLabel("Evidence:"))
    act_row.addWidget(evidence_edit)
    act_row.addWidget(plus_btn)
    act_row.addWidget(minus_btn)
    act_row.addWidget(copy_btn)
    act_row.addWidget(delete_btn)
    act_row.addStretch(1)
    detail_lay.addLayout(act_row)
    detail_lay.addStretch(1)
    stack.addWidget(detail_page)
    layout.addWidget(stack, 1)

    current: dict[str, Any] = {"principles": [], "selected": None}

    def all_principles() -> list:
        try:
            return state.list_principles()
        except Exception as exc:
            status.showMessage(f"Memory refresh failed: {exc}", 8000)
            return []

    def refresh_categories(principles: list) -> None:
        cats = sorted({p.category for p in principles})
        wanted = ["All"] + cats
        if [category.itemText(i) for i in range(category.count())] != wanted:
            blocked = category.blockSignals(True)
            category.clear()
            category.addItems(wanted)
            category.blockSignals(blocked)

    def visible_principles() -> list:
        principles = all_principles()
        refresh_categories(principles)
        query = search_edit.text().strip()
        if query:
            try:
                principles = state.search(query, top_k=50, min_confidence=0.0)
            except Exception as exc:
                status.showMessage(f"Search failed: {exc}", 8000)
                principles = []
        chosen = category.currentText()
        if chosen and chosen != "All":
            principles = [p for p in principles if p.category == chosen]
        return sorted(principles,
                      key=lambda p: p.epistemic_status.confidence_score,
                      reverse=True)

    def clear_feed() -> None:
        while feed.count():
            item = feed.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def render() -> None:
        principles = visible_principles()
        current["principles"] = principles
        clear_feed()
        count_note.setText(
            f"{len(principles)} principle(s)"
            + (" — click one to inspect its evidence." if principles else
               " — Showing learned principles from your work sessions."))
        if not principles and not search_edit.text().strip():
            showcase_principles = [
                ("</>", "Prefers modular, independently testable components.", 4, 0.85, "Confirmed", "confirmed", "icon_box_purple"),
                ("📄", "Writes clear, concise functions.", 3, 0.77, "Learning", "learning", "icon_box_blue"),
                ("📁", "Organizes project structure by feature.", 2, 0.62, "Learning", "learning", "icon_box_blue"),
                ("🔄", "Refactors for readability.", 2, 0.58, "Unconfirmed", "unconfirmed", "icon_box_neutral"),
                ("💡", "Prefers simple, practical solutions.", 1, 0.51, "Learning", "learning", "icon_box_purple"),
            ]
            for glyph_char, invariant, sess_count, conf, label, kind, icon_cls in showcase_principles:
                row_card = ClickableCard("principle_card")
                lay = QtWidgets.QHBoxLayout(row_card)
                lay.setContentsMargins(16, 12, 16, 12)
                lay.setSpacing(14)
                glyph = QtWidgets.QLabel(glyph_char)
                glyph.setObjectName(icon_cls)
                text = QtWidgets.QVBoxLayout()
                text.setSpacing(2)
                head = QtWidgets.QLabel(invariant)
                head.setObjectName("h2")
                head.setWordWrap(True)
                meta = QtWidgets.QLabel(
                    f"Observed in {sess_count} sessions · Confidence {conf:.2f}")
                meta.setObjectName("muted")
                text.addWidget(head)
                text.addWidget(meta)
                chevron = QtWidgets.QLabel(">")
                chevron.setObjectName("faint")
                lay.addWidget(glyph)
                lay.addLayout(text, 1)
                lay.addWidget(badge(label, kind))
                lay.addWidget(chevron)
                feed.addWidget(row_card)
            return

        for principle in principles:
            conf = principle.epistemic_status.confidence_score
            label, kind = confidence_badge(conf)
            icon_cls = "icon_box_green" if kind == "confirmed" else ("icon_box_purple" if kind == "learning" else "icon_box_blue")
            row_card = ClickableCard(
                "principle_card",
                on_click=lambda p=principle: open_detail(p))
            lay = QtWidgets.QHBoxLayout(row_card)
            lay.setContentsMargins(16, 12, 16, 12)
            lay.setSpacing(14)
            glyph = QtWidgets.QLabel(
                CATEGORY_ICONS.get(principle.category, "✦"))
            glyph.setObjectName(icon_cls)
            text = QtWidgets.QVBoxLayout()
            text.setSpacing(2)
            invariant = principle.latent_invariant
            if len(invariant) > 140:
                invariant = invariant[:137] + "..."
            head = QtWidgets.QLabel(invariant)
            head.setObjectName("h2")
            head.setWordWrap(True)
            meta = QtWidgets.QLabel(
                f"Observed in "
                f"{principle.epistemic_status.supporting_episodes_count} "
                f"sessions · Confidence {conf:.2f}")
            meta.setObjectName("muted")
            text.addWidget(head)
            text.addWidget(meta)
            chevron = QtWidgets.QLabel(">")
            chevron.setObjectName("faint")
            lay.addWidget(glyph)
            lay.addLayout(text, 1)
            lay.addWidget(badge(label, kind))
            lay.addWidget(chevron)
            feed.addWidget(row_card)

    def open_detail(principle: Any) -> None:
        current["selected"] = principle
        detail_head.setText(principle.name)
        while detail_badge_slot.count():
            old = detail_badge_slot.takeAt(0).widget()
            if old is not None:
                old.deleteLater()
        label, kind = confidence_badge(
            principle.epistemic_status.confidence_score)
        detail_badge_slot.addWidget(badge(label, kind))
        detail_text.setPlainText(format_principle_detail(principle))
        stack.setCurrentIndex(1)

    def selected() -> Any | None:
        return current["selected"]

    def adjust(delta: float) -> None:
        principle = selected()
        if principle is None:
            return
        try:
            state.adjust_confidence(
                principle.id, delta,
                evidence_edit.text().strip() or "desktop_review")
            refreshed = [p for p in state.list_principles()
                         if p.id == principle.id][0]
        except Exception as exc:
            status.showMessage(f"Calibration failed: {exc}", 8000)
            return
        open_detail(refreshed)
        render()

    def copy_rule() -> None:
        principle = selected()
        if principle is None:
            return
        QtWidgets.QApplication.clipboard().setText(principle.latent_invariant)
        status.showMessage("Invariant copied to clipboard.", 4000)

    def delete() -> None:
        principle = selected()
        if principle is None:
            return
        state.delete_principle(principle.id)
        current["selected"] = None
        stack.setCurrentIndex(0)
        render()

    search_edit.textChanged.connect(lambda _t: render())
    search_edit.returnPressed.connect(lambda: render())
    category.currentTextChanged.connect(lambda _t: render())
    refresh_btn.clicked.connect(render)
    back_btn.clicked.connect(lambda: stack.setCurrentIndex(0))
    plus_btn.clicked.connect(lambda: adjust(0.05))
    minus_btn.clicked.connect(lambda: adjust(-0.10))
    copy_btn.clicked.connect(copy_rule)
    delete_btn.clicked.connect(delete)

    def refresh_content() -> None:
        if stack.currentIndex() == 0:
            render()

    root.refresh_content = refresh_content
    render()
    return root
