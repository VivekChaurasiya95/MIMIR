"""Home cockpit: serif greeting, status card, explore bar, KPIs (spec 5.1)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from ..presenters import greeting_for_hour
from ..state import AppState
from ._layout import ClickableCard, elevate, overline, page
from ._qt import load_qt

__all__ = ["build_home_tab"]

CHIP_PROMPTS = (
    ("✦ Explain this code", "Explain this code: "),
    ("◫ Plan a feature", "Help me plan a feature: "),
    ("☼ Brainstorm ideas", "Brainstorm ideas for: "),
)


def build_home_tab(state: AppState, navigate: Callable[[str], None],
                   status: Any, watcher: Any) -> Any:
    _, _, _, QtWidgets = load_qt()
    root, layout = page()

    top = QtWidgets.QHBoxLayout()
    top.setSpacing(16)
    hero = QtWidgets.QVBoxLayout()
    hero.setSpacing(4)
    greet = QtWidgets.QLabel("")
    greet.setObjectName("hero_overline")
    name = QtWidgets.QLabel("")
    name.setObjectName("hero_name")
    tagline = QtWidgets.QLabel("")
    tagline.setObjectName("lede")
    tagline.setWordWrap(True)
    hero.addWidget(greet)
    hero.addWidget(name)
    hero.addWidget(tagline)
    top.addLayout(hero, 1)

    status_card = QtWidgets.QFrame()
    status_card.setObjectName("status_card")
    status_card.setFixedWidth(280)
    elevate(status_card)
    sc = QtWidgets.QVBoxLayout(status_card)
    sc.setContentsMargins(18, 16, 18, 16)
    sc.setSpacing(8)
    dot_row = QtWidgets.QHBoxLayout()
    dot = QtWidgets.QLabel("")
    dot.setObjectName("status_dot")
    state_line = QtWidgets.QLabel("")
    state_line.setObjectName("h2")
    dot_row.addWidget(dot)
    dot_row.addWidget(state_line, 1)
    context_line = QtWidgets.QLabel("")
    context_line.setObjectName("muted")
    context_line.setWordWrap(True)
    watch_btn = QtWidgets.QPushButton("")
    watch_btn.setObjectName("start_obs_btn")
    sc.addLayout(dot_row)
    sc.addWidget(context_line)
    sc.addWidget(watch_btn)
    top.addWidget(status_card)
    layout.addLayout(top)

    bar_row = QtWidgets.QHBoxLayout()
    bar_row.setSpacing(10)
    sparkle = QtWidgets.QLabel("✦")
    sparkle.setObjectName("faint")
    explore = QtWidgets.QLineEdit()
    explore.setObjectName("explore_bar")
    explore.setPlaceholderText("What would you like to explore?")
    explore.setClearButtonEnabled(True)
    mic_btn = QtWidgets.QPushButton("🎙")
    mic_btn.setObjectName("mic_btn")
    mic_btn.setFixedSize(40, 40)
    mic_btn.setToolTip("Speak instead of typing (needs a microphone)")
    bar_row.addWidget(sparkle)
    bar_row.addWidget(explore, 1)
    bar_row.addWidget(mic_btn)
    layout.addLayout(bar_row)

    chips = QtWidgets.QHBoxLayout()
    chips.setSpacing(8)
    for label, seed in CHIP_PROMPTS:
        chip = QtWidgets.QPushButton(label)
        chip.setObjectName("chip")
        chip.clicked.connect(
            lambda _=False, s=seed: (explore.setText(s),
                                     explore.setFocus()))
        chips.addWidget(chip)
    more = QtWidgets.QPushButton("··· More")
    more.setObjectName("chip")
    more.clicked.connect(lambda: navigate("replicate"))
    chips.addWidget(more)
    chips.addStretch(1)
    layout.addLayout(chips)

    reply = QtWidgets.QLabel("")
    reply.setObjectName("subtle")
    reply.setWordWrap(True)
    reply.setVisible(False)
    layout.addWidget(reply)

    kpi_row = QtWidgets.QHBoxLayout()
    kpi_row.setSpacing(12)
    kpi_cards: list[Any] = []

    def kpi(icon: str, caption: str, target: str, icon_cls: str = "icon_box_purple") -> Any:
        card_widget = ClickableCard("kpi_card",
                                    on_click=lambda: navigate(target))
        lay = QtWidgets.QHBoxLayout(card_widget)
        lay.setContentsMargins(18, 14, 16, 14)
        lay.setSpacing(14)
        glyph = QtWidgets.QLabel(icon)
        glyph.setObjectName(icon_cls)
        text = QtWidgets.QVBoxLayout()
        text.setSpacing(2)
        number = QtWidgets.QLabel("0")
        number.setObjectName("kpi_number")
        cap = QtWidgets.QLabel(caption)
        cap.setObjectName("muted")
        text.addWidget(number)
        text.addWidget(cap)
        chevron = QtWidgets.QLabel(">")
        chevron.setObjectName("faint")
        lay.addWidget(glyph)
        lay.addLayout(text, 1)
        lay.addWidget(chevron)
        kpi_cards.append(number)
        return card_widget

    kpi_row.addWidget(kpi("▤", "Principles learned", "memory", "icon_box_purple"))
    kpi_row.addWidget(kpi("ılı", "Episodes observed", "observe", "icon_box_blue"))
    kpi_row.addWidget(kpi("📊", "Average confidence", "memory", "icon_box_green"))
    layout.addLayout(kpi_row)

    layout.addWidget(overline("What would you like to do?"))
    actions_row_layout = QtWidgets.QHBoxLayout()
    actions_row_layout.setSpacing(12)
    actions = [
        ("👁", "Observe", "Watch repos, learn your habits.", "observe", "icon_box_purple"),
        ("📖", "Memory", "Your standards, inspectable.", "memory", "icon_box_blue"),
        ("⎘", "Replicate", "Apply your style to new tasks.", "replicate", "icon_box_green"),
        ("🛡", "Trust", "Stays local, you're in control.", "trust", "icon_box_amber"),
    ]
    for icon, heading, body, tab_id, icon_cls in actions:
        tile = ClickableCard("action_card", on_click=lambda t=tab_id: navigate(t))
        lay = QtWidgets.QHBoxLayout(tile)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(12)
        glyph = QtWidgets.QLabel(icon)
        glyph.setObjectName(icon_cls)
        text_col = QtWidgets.QVBoxLayout()
        text_col.setSpacing(2)
        head = QtWidgets.QLabel(heading)
        head.setObjectName("h2")
        sub = QtWidgets.QLabel(body)
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        text_col.addWidget(head)
        text_col.addWidget(sub)
        chevron = QtWidgets.QLabel(">")
        chevron.setObjectName("faint")
        lay.addWidget(glyph)
        lay.addLayout(text_col, 1)
        lay.addWidget(chevron)
        actions_row_layout.addWidget(tile)
    layout.addLayout(actions_row_layout)
    layout.addStretch(1)

    def refresh_content() -> None:
        greet.setText(greeting_for_hour(datetime.now().hour))
        name.setText(state.prefs.user_name or "there")
        tagline.setText(
            f"I'm {state.prefs.assistant_name}. I observe how you work, "
            "learn your style, and apply it when you need me.")
        if watcher.watching:
            dot.setObjectName("status_dot_on")
            state_line.setText("Observing · Recording telemetry")
            context_line.setText(
                f"Watching {state.prefs.last_repo or 'your workspace'} "
                "for new commits.")
            watch_btn.setText("⏹ Stop Observing")
        else:
            dot.setObjectName("status_dot")
            state_line.setText("Resting · Not observing")
            context_line.setText(
                "Your workspace isn't being observed right now.")
            watch_btn.setText("▷ Start Observing")
        dot.style().unpolish(dot)
        dot.style().polish(dot)
        try:
            principles = state.list_principles()
        except Exception:
            principles = []
        stats = state.memory_stats() if principles else {
            "count": 0, "avg_confidence": 0.0}
        kpi_cards[0].setText(str(stats["count"]))
        kpi_cards[1].setText(str(len(state.episodes)))
        kpi_cards[2].setText(f"{stats['avg_confidence']:.2f}")

    def on_watch() -> None:
        if watcher.watching:
            watcher.stop()
            refresh_content()
            return
        repo = state.prefs.last_repo
        roots = state.prefs.active_roots(state.default_roots())
        if not repo:
            status.showMessage(
                "Choose a workspace repo in Observe first.", 5000)
            navigate("observe")
            return
        ok, message = watcher.start(repo, roots)
        if not ok:
            status.showMessage(f"Couldn't start observing: {message}", 8000)
        refresh_content()

    def on_ask() -> None:
        text = explore.text().strip()
        if not text:
            return
        parsed = state.parse_utterance(text)
        answer = state.respond(parsed["intent"], parsed["text"])
        reply.setText(f"{state.prefs.assistant_name}: {answer}")
        reply.setVisible(True)
        status.showMessage(f"Intent: {parsed['intent']}", 4000)

    def on_mic() -> None:
        status.showMessage("Listening… speak now.", 4000)
        ok, heard = state.try_listen(timeout_sec=6.0)
        if ok:
            explore.setText(heard)
            explore.setFocus()
        else:
            status.showMessage(f"Microphone: {heard}", 8000)

    watch_btn.clicked.connect(on_watch)
    explore.returnPressed.connect(on_ask)
    mic_btn.clicked.connect(on_mic)
    root.refresh_content = refresh_content  # called by the shell on navigate
    refresh_content()
    return root
