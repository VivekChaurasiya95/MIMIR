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
    functionality_banner,
    page,
    page_head,
)
from ._qt import load_qt

__all__ = ["build_memory_tab"]

CATEGORY_ICONS = {
    "architecture": "</>",
    "testing": "✓",
    "workflow": "📁",
    "documentation": "◫",
    "refactoring": "🔄",
    "dependency": "⛓",
    "style": "📄",
    "practical": "💡",
}


def build_memory_tab(state: AppState, status: Any) -> Any:
    _, _, _, QtWidgets = load_qt()
    root, layout = page()
    layout.addWidget(page_head(
        "Memory", "What Habitous knows about you.",
        "These are the principles learned from your work, with supporting evidence."))

    layout.addWidget(functionality_banner(
        "📖",
        "Behavioral Principle Bank",
        "Inspect and calibrate every coding standard, testing invariant, and architectural pattern "
        "Habitous has abstracted from your work. Each rule is scored with epistemic confidence and verifiable evidence.",
        pills=[("Epistemic Scoring", "confirmed"), ("Inspectable Traces", "learning"), ("Human Calibrated", "confirmed")],
        icon_cls="icon_box_purple",
    ))

    row = QtWidgets.QHBoxLayout()
    row.setSpacing(12)
    search_edit = QtWidgets.QLineEdit()
    search_edit.setPlaceholderText("🔍 Search principles by invariant or scope...")
    search_edit.setMinimumWidth(320)
    category = QtWidgets.QComboBox()
    category.setMinimumWidth(140)
    row.addWidget(search_edit, 1)
    row.addWidget(category)
    layout.addLayout(row)

    seed_row = QtWidgets.QHBoxLayout()
    seed_row.setSpacing(12)
    seed_row.addWidget(QtWidgets.QLabel("Demo persona:"))
    persona_combo = QtWidgets.QComboBox()
    persona_combo.setMinimumWidth(260)
    try:
        from ..personas import list_personas
        for persona in list_personas():
            persona_combo.addItem(
                f"{persona['title']} — {persona['description']}",
                persona["id"])
    except ImportError:
        persona_combo.addItem("Personas unavailable", "")
    seed_btn = QtWidgets.QPushButton("Seed Demo Persona")
    seed_btn.setObjectName("soft")
    seed_btn.setToolTip(
        "Populate Behavioral Memory with a rich developer profile "
        "for demos on fresh machines.")
    seed_row.addWidget(persona_combo, 1)
    seed_row.addWidget(seed_btn)
    seed_row.addStretch(1)
    layout.addLayout(seed_row)

    stack = QtWidgets.QStackedWidget()
    list_page = QtWidgets.QWidget()
    list_lay = QtWidgets.QVBoxLayout(list_page)
    list_lay.setContentsMargins(0, 0, 0, 0)
    list_lay.setSpacing(10)
    count_note = QtWidgets.QLabel("")
    count_note.setObjectName("muted")
    count_note.setVisible(False)
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
    detail_meter_row = QtWidgets.QHBoxLayout()
    detail_meter_row.setSpacing(10)
    detail_meter = QtWidgets.QProgressBar()
    detail_meter.setRange(0, 100)
    detail_meter.setValue(0)
    detail_meter.setTextVisible(False)
    detail_meter.setFixedHeight(8)
    detail_meter_label = QtWidgets.QLabel("")
    detail_meter_label.setObjectName("muted")
    detail_meter_row.addWidget(detail_meter, 1)
    detail_meter_row.addWidget(detail_meter_label)
    detail_box_lay.addWidget(detail_head)
    detail_box_lay.addLayout(detail_badge_row)
    detail_box_lay.addLayout(detail_meter_row)
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
            previous = category.currentText()
            blocked = category.blockSignals(True)
            category.clear()
            category.addItems(wanted)
            if previous in wanted:
                category.setCurrentText(previous)
            category.blockSignals(blocked)

    def _matches_query(principle: Any, tokens: list[str]) -> bool:
        haystack = (
            f"{principle.name} {principle.latent_invariant} "
            f"{principle.category} {principle.context_trigger.scope}"
        ).lower()
        return any(token in haystack for token in tokens)

    def visible_principles() -> list:
        principles = all_principles()
        refresh_categories(principles)
        query = search_edit.text().strip()
        if query:
            try:
                ranked = state.search(query, top_k=50, min_confidence=0.0)
            except Exception as exc:
                status.showMessage(f"Search failed: {exc}", 8000)
                ranked = []
            # The store ranks by similarity but never excludes; narrow to
            # keyword hits so searching visibly filters the bank.
            import re
            tokens = [t for t in re.split(r"\W+", query.lower())
                      if len(t) >= 3]
            principles = ([p for p in ranked
                           if _matches_query(p, tokens)]
                          if tokens else ranked)
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
                widget.hide()
                widget.deleteLater()

    def render() -> None:
        principles = visible_principles()
        current["principles"] = principles
        clear_feed()
        filtered = bool(search_edit.text().strip()) or (
            category.currentText() not in ("", "All"))
        if principles:
            tail = " — click one to inspect its evidence."
        elif filtered:
            tail = " — no matches for these filters."
        else:
            tail = " — learn your first ones below."
        count_note.setText(f"{len(principles)} principle(s)" + tail)
        count_note.setVisible(True)
        if not principles:
            if search_edit.text().strip() or category.currentText() not in (
                    "", "All"):
                no_box, no_lay = card()
                no_title = QtWidgets.QLabel("No matching principles")
                no_title.setObjectName("h2")
                no_hint = QtWidgets.QLabel(
                    "Nothing matches this search and category. "
                    "Try different keywords or clear the filters.")
                no_hint.setObjectName("muted")
                no_hint.setWordWrap(True)
                clear_btn = QtWidgets.QPushButton("Clear filters")
                clear_btn.setObjectName("ink")

                def _clear_filters() -> None:
                    search_edit.clear()
                    if category.findText("All") >= 0:
                        category.setCurrentText("All")
                    render()

                clear_btn.clicked.connect(_clear_filters)
                no_lay.addWidget(no_title)
                no_lay.addWidget(no_hint)
                no_lay.addLayout(actions_row(clear_btn))
                feed.addWidget(no_box)
                return
            empty_box, empty_lay = card()
            empty_title = QtWidgets.QLabel("Behavioral Memory is empty")
            empty_title.setObjectName("h2")
            empty_hint = QtWidgets.QLabel(
                "Observe a repository and press Learn to distill your "
                "first principles — or seed a demo persona to explore "
                "with realistic data right now.")
            empty_hint.setObjectName("muted")
            empty_hint.setWordWrap(True)
            seed_now_btn = QtWidgets.QPushButton("Seed Demo Persona")
            seed_now_btn.setObjectName("ink")
            seed_now_btn.clicked.connect(on_seed)
            empty_lay.addWidget(empty_title)
            empty_lay.addWidget(empty_hint)
            empty_lay.addLayout(actions_row(seed_now_btn))
            feed.addWidget(empty_box)
            return

        for principle in principles:
            conf = principle.epistemic_status.confidence_score
            label, kind = confidence_badge(conf)
            glyph_char = CATEGORY_ICONS.get(principle.category, "✦")
            if glyph_char == "</>":
                icon_cls = "icon_box_purple"
            elif glyph_char in ("📄", "📁"):
                icon_cls = "icon_box_blue"
            elif glyph_char == "🔄":
                icon_cls = "icon_box_neutral"
            elif glyph_char == "💡":
                icon_cls = "icon_box_purple"
            else:
                icon_cls = "icon_box_green" if kind == "confirmed" else "icon_box_blue"

            row_card = ClickableCard(
                "principle_card",
                on_click=lambda p=principle: open_detail(p))
            lay = QtWidgets.QHBoxLayout(row_card)
            lay.setContentsMargins(16, 12, 16, 12)
            lay.setSpacing(14)
            glyph = QtWidgets.QLabel(glyph_char)
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
            meter = QtWidgets.QProgressBar()
            meter.setRange(0, 100)
            meter.setValue(int(round(conf * 100)))
            meter.setTextVisible(False)
            meter.setFixedHeight(6)
            meter.setToolTip(f"Confidence {conf:.2f}")
            text.addWidget(head)
            text.addWidget(meta)
            text.addWidget(meter)
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
                old.hide()
                old.deleteLater()
        conf = principle.epistemic_status.confidence_score
        label, kind = confidence_badge(conf)
        detail_badge_slot.addWidget(badge(label, kind))
        detail_meter.setValue(int(round(conf * 100)))
        detail_meter.setToolTip(f"Confidence {conf:.2f}")
        detail_meter_label.setText(
            f"{conf:.2f} · "
            f"+{principle.epistemic_status.supporting_episodes_count}/"
            f"−{principle.epistemic_status.contradictions_count}")
        detail_text.setPlainText(format_principle_detail(principle))
        stack.setCurrentIndex(1)

    def selected() -> Any | None:
        return current["selected"]

    def adjust(delta: float) -> None:
        principle = selected()
        if principle is None:
            return
        before = principle.epistemic_status.confidence_score
        try:
            state.adjust_confidence(
                principle.id, delta,
                evidence_edit.text().strip() or "desktop_review")
            refreshed = [p for p in state.list_principles()
                         if p.id == principle.id][0]
        except Exception as exc:
            status.showMessage(f"Calibration failed: {exc}", 8000)
            return
        after = refreshed.epistemic_status.confidence_score
        open_detail(refreshed)
        render()
        status.showMessage(
            f"Confidence {before:.2f} → {after:.2f} ({delta:+.2f}).", 4000)

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
        confirm = QtWidgets.QMessageBox(root)
        confirm.setWindowTitle("Delete this principle?")
        confirm.setText(f"“{principle.name}” will be permanently removed.")
        confirm.setInformativeText("This cannot be undone.")
        confirm.setStandardButtons(
            QtWidgets.QMessageBox.StandardButton.Cancel
            | QtWidgets.QMessageBox.StandardButton.Ok)
        confirm.button(QtWidgets.QMessageBox.StandardButton.Ok).setText(
            "Delete")
        if confirm.exec() != QtWidgets.QMessageBox.StandardButton.Ok:
            return
        state.delete_principle(principle.id)
        current["selected"] = None
        stack.setCurrentIndex(0)
        render()
        status.showMessage("Principle deleted.", 4000)

    def on_seed() -> None:
        persona_id = persona_combo.currentData()
        if not persona_id:
            status.showMessage("No demo persona available.", 4000)
            return
        try:
            from ..personas import get_persona, seed_persona
            seeded = seed_persona(state.store, persona_id)
            title = get_persona(persona_id)["title"]
        except Exception as exc:
            status.showMessage(f"Persona seeding failed: {exc}", 8000)
            return
        search_edit.clear()
        render()
        status.showMessage(
            f"Seeded {len(seeded)} {title} principle(s) into Memory.", 5000)

    seed_btn.clicked.connect(on_seed)
    search_edit.textChanged.connect(lambda _t: render())
    search_edit.returnPressed.connect(lambda: render())
    category.currentTextChanged.connect(lambda _t: render())
    back_btn.clicked.connect(
        lambda: (stack.setCurrentIndex(0), render()))
    plus_btn.clicked.connect(lambda: adjust(0.05))
    minus_btn.clicked.connect(lambda: adjust(-0.10))
    copy_btn.clicked.connect(copy_rule)
    delete_btn.clicked.connect(delete)

    def refresh_content() -> None:
        if stack.currentIndex() == 0:
            render()
            return
        sel = selected()
        if sel is None:
            stack.setCurrentIndex(0)
            render()
            return
        fresh = [p for p in all_principles() if p.id == sel.id]
        if fresh:
            open_detail(fresh[0])
        else:
            current["selected"] = None
            stack.setCurrentIndex(0)
            render()

    root.refresh_content = refresh_content
    render()
    return root
