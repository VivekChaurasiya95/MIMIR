"""Settings screen: calibration across six categories (spec 5.6)."""

from __future__ import annotations

from typing import Any

from ..state import AppState
from ..theme import PORCELAIN
from ._layout import card, page, page_head, toggle
from ._qt import load_qt

__all__ = ["build_settings_tab"]

LANGUAGES = ("English (US)", "English (UK)", "Hinglish", "Hindi")

EXTRAS = (
    ("PyQt6 / PySide6", ("PyQt6", "PySide6"), "Desktop shell"),
    ("pyautogui", ("pyautogui",), "Cursor control"),
    ("pynput", ("pynput",), "Failsafe listeners"),
    ("openai-whisper", ("whisper",), "Local speech-to-text"),
    ("edge-tts", ("edge_tts",), "Neural speech output"),
    ("SpeechRecognition", ("speech_recognition",), "Microphone capture"),
)


def build_settings_tab(state: AppState, status: Any) -> Any:
    _, _, _, QtWidgets = load_qt()
    root, layout = page()
    layout.addWidget(page_head(
        "Settings", "Customize your experience.",
        "Control how MIMIR looks, sounds, and works."))

    body = QtWidgets.QHBoxLayout()
    body.setSpacing(16)
    nav = QtWidgets.QListWidget()
    nav.setObjectName("settings_nav")
    nav.setFixedWidth(180)
    nav.addItems(["General", "Voice", "Model", "Appearance", "Shortcuts",
                  "Integrations"])
    stack = QtWidgets.QStackedWidget()
    body.addWidget(nav)
    body.addWidget(stack, 1)
    layout.addLayout(body, 1)

    def save_prefs(note: str = "Preferences saved.") -> None:
        try:
            state.prefs.save()
        except OSError as exc:
            status.showMessage(f"Couldn't save preferences: {exc}", 8000)
            return
        status.showMessage(note, 3000)

    # -- General ---------------------------------------------------------
    general = QtWidgets.QWidget()
    general_lay = QtWidgets.QVBoxLayout(general)
    general_lay.setContentsMargins(0, 0, 0, 0)
    general_lay.setSpacing(12)
    gen_box, gen_lay = card()
    gen_head = QtWidgets.QLabel("General")
    gen_head.setObjectName("h2")
    gen_sub = QtWidgets.QLabel("Basic preferences for MIMIR.")
    gen_sub.setObjectName("muted")
    gen_lay.addWidget(gen_head)
    gen_lay.addWidget(gen_sub)
    gen_form = QtWidgets.QFormLayout()
    gen_form.setSpacing(10)
    your_name = QtWidgets.QLineEdit(state.prefs.user_name)
    assistant_name = QtWidgets.QLineEdit(state.prefs.assistant_name)
    language = QtWidgets.QComboBox()
    language.addItems(LANGUAGES)
    if state.prefs.language in LANGUAGES:
        language.setCurrentText(state.prefs.language)
    launch_toggle = toggle(state.prefs.start_on_launch)
    tray_toggle = toggle(state.prefs.show_in_tray)
    gen_form.addRow("Your name:", your_name)
    gen_form.addRow("Assistant name:", assistant_name)
    gen_form.addRow("Language:", language)
    gen_form.addRow("Start on system launch:", launch_toggle)
    gen_form.addRow("Show in system tray:", tray_toggle)
    gen_lay.addLayout(gen_form)
    general_lay.addWidget(gen_box)
    updates_box, updates_lay = card()
    updates_head = QtWidgets.QLabel("Updates")
    updates_head.setObjectName("h2")
    updates_lay.addWidget(updates_head)
    updates_row = QtWidgets.QHBoxLayout()
    version_label = QtWidgets.QLabel("")
    version_label.setObjectName("muted")
    check_btn = QtWidgets.QPushButton("Check for updates")
    check_btn.setObjectName("soft")
    updates_row.addWidget(version_label, 1)
    updates_row.addWidget(check_btn)
    updates_lay.addLayout(updates_row)
    general_lay.addWidget(updates_box)
    general_lay.addStretch(1)
    stack.addWidget(general)

    your_name.editingFinished.connect(
        lambda: (setattr(state.prefs, "user_name",
                         your_name.text().strip() or "Arin"), save_prefs()))
    assistant_name.editingFinished.connect(
        lambda: (setattr(state.prefs, "assistant_name",
                         assistant_name.text().strip() or "MIMIR"),
                 save_prefs()))
    language.currentTextChanged.connect(
        lambda text: (setattr(state.prefs, "language", text), save_prefs()))
    launch_toggle.toggled.connect(
        lambda on: (setattr(state.prefs, "start_on_launch", on), save_prefs()))
    tray_toggle.toggled.connect(
        lambda on: (setattr(state.prefs, "show_in_tray", on), save_prefs()))

    def on_check_updates() -> None:
        try:
            import mimir
            version = getattr(mimir, "__version__", "unknown")
        except ImportError:
            version = "unknown"
        version_label.setText(
            f"Version {version} — managed via pip "
            f"(pip install -U mimir); no bundled updater.")
        status.showMessage(f"MIMIR {version} installed.", 4000)

    try:
        import mimir as _mimir
        version_label.setText(f"Version {getattr(_mimir, '__version__', '?')}")
    except ImportError:
        version_label.setText("Version unknown")
    check_btn.clicked.connect(on_check_updates)

    # -- Voice -----------------------------------------------------------
    voice = QtWidgets.QWidget()
    voice_lay = QtWidgets.QVBoxLayout(voice)
    voice_lay.setContentsMargins(0, 0, 0, 0)
    voice_lay.setSpacing(12)
    voice_box, voice_box_lay = card()
    voice_head = QtWidgets.QLabel("Voice")
    voice_head.setObjectName("h2")
    voice_sub = QtWidgets.QLabel(
        "Speech input (local Whisper) and output (Edge TTS). Both degrade "
        "gracefully when hardware or packages are missing.")
    voice_sub.setObjectName("muted")
    voice_sub.setWordWrap(True)
    voice_box_lay.addWidget(voice_head)
    voice_box_lay.addWidget(voice_sub)
    mic_btn = QtWidgets.QPushButton("Test microphone")
    mic_btn.setObjectName("soft")
    speak_btn = QtWidgets.QPushButton("Speak test line")
    speak_btn.setObjectName("soft")
    voice_btns = QtWidgets.QHBoxLayout()
    voice_btns.addWidget(mic_btn)
    voice_btns.addWidget(speak_btn)
    voice_btns.addStretch(1)
    voice_box_lay.addLayout(voice_btns)
    voice_result = QtWidgets.QLabel("")
    voice_result.setObjectName("muted")
    voice_result.setWordWrap(True)
    voice_box_lay.addWidget(voice_result)
    voice_lay.addWidget(voice_box)
    voice_lay.addStretch(1)
    stack.addWidget(voice)

    def on_mic_test() -> None:
        status.showMessage("Listening… speak now.", 4000)
        ok, heard = state.try_listen(timeout_sec=4.0)
        voice_result.setText(
            f"Heard: “{heard}”" if ok else f"Microphone unavailable: {heard}")
        voice_result.setObjectName("muted")

    def on_speak_test() -> None:
        from mimir.voice import VoiceCoordinator
        line = f"Hello {state.prefs.user_name}, I am ready."
        try:
            VoiceCoordinator().speak(line)
        except Exception as exc:
            voice_result.setText(f"Speech output unavailable: {exc}")
            voice_result.setObjectName("err")
        else:
            voice_result.setText("Spoken — did you hear me?")
            voice_result.setObjectName("ok")
        voice_result.style().unpolish(voice_result)
        voice_result.style().polish(voice_result)

    mic_btn.clicked.connect(on_mic_test)
    speak_btn.clicked.connect(on_speak_test)

    # -- Model -----------------------------------------------------------
    model = QtWidgets.QWidget()
    model_lay = QtWidgets.QVBoxLayout(model)
    model_lay.setContentsMargins(0, 0, 0, 0)
    model_lay.setSpacing(12)
    model_box, model_box_lay = card()
    model_head = QtWidgets.QLabel("Model")
    model_head.setObjectName("h2")
    model_sub = QtWidgets.QLabel(
        "How MIMIR reasons and synthesizes. The offline scaffold is always "
        "available; API providers plug into ReplicationActor.")
    model_sub.setObjectName("muted")
    model_sub.setWordWrap(True)
    model_box_lay.addWidget(model_head)
    model_box_lay.addWidget(model_sub)
    model_form = QtWidgets.QFormLayout()
    model_form.setSpacing(10)
    provider = QtWidgets.QComboBox()
    provider.addItems(["Offline heuristic scaffold"])
    topk_spin = QtWidgets.QSpinBox()
    topk_spin.setRange(1, 10)
    topk_spin.setValue(state.prefs.default_top_k)
    topk_spin.setMaximumWidth(120)
    minconf_spin = QtWidgets.QDoubleSpinBox()
    minconf_spin.setRange(0.0, 1.0)
    minconf_spin.setSingleStep(0.05)
    minconf_spin.setValue(state.prefs.default_min_confidence)
    minconf_spin.setMaximumWidth(120)
    model_form.addRow("Reasoning provider:", provider)
    model_form.addRow("Default principles per task:", topk_spin)
    model_form.addRow("Default min confidence:", minconf_spin)
    model_box_lay.addLayout(model_form)
    model_lay.addWidget(model_box)
    model_lay.addStretch(1)
    stack.addWidget(model)

    topk_spin.valueChanged.connect(
        lambda value: (setattr(state.prefs, "default_top_k", int(value)),
                       save_prefs()))
    minconf_spin.valueChanged.connect(
        lambda value: (setattr(state.prefs, "default_min_confidence",
                               float(value)), save_prefs()))

    # -- Appearance ------------------------------------------------------
    appearance = QtWidgets.QWidget()
    appear_lay = QtWidgets.QVBoxLayout(appearance)
    appear_lay.setContentsMargins(0, 0, 0, 0)
    appear_lay.setSpacing(12)
    appear_box, appear_box_lay = card()
    appear_head = QtWidgets.QLabel("Appearance")
    appear_head.setObjectName("h2")
    appear_box_lay.addWidget(appear_head)
    appear_form = QtWidgets.QFormLayout()
    appear_form.setSpacing(10)
    theme_combo = QtWidgets.QComboBox()
    theme_combo.addItems(["Porcelain Light"])
    appear_form.addRow("Theme:", theme_combo)
    appear_box_lay.addLayout(appear_form)
    swatches = QtWidgets.QHBoxLayout()
    swatches.setSpacing(8)
    for name, key in (("Indigo", "accent_primary"),
                      ("Lavender", "accent_pill_lavender"),
                      ("Sage", "status_green_bg"),
                      ("Sky", "status_blue_bg"),
                      ("Amber", "status_amber_bg")):
        chip = QtWidgets.QLabel(f"  {name}  ")
        chip.setObjectName("chip")
        chip.setStyleSheet(
            f"background: {PORCELAIN[key]}; border-radius: 999px; "
            f"padding: 4px 6px; color: {PORCELAIN['text_body']};")
        swatches.addWidget(chip)
    swatches.addStretch(1)
    appear_box_lay.addLayout(swatches)
    theme_note = QtWidgets.QLabel("More themes coming soon.")
    theme_note.setObjectName("muted")
    appear_box_lay.addWidget(theme_note)
    reset_btn = QtWidgets.QPushButton("Reset window size (1280×800)")
    reset_btn.setObjectName("soft")
    reset_row = QtWidgets.QHBoxLayout()
    reset_row.addWidget(reset_btn)
    reset_row.addStretch(1)
    appear_box_lay.addLayout(reset_row)
    appear_lay.addWidget(appear_box)
    appear_lay.addStretch(1)
    stack.addWidget(appearance)
    reset_btn.clicked.connect(lambda: root.window().resize(1280, 800))

    # -- Shortcuts -------------------------------------------------------
    shortcuts = QtWidgets.QWidget()
    shortcuts_lay = QtWidgets.QVBoxLayout(shortcuts)
    shortcuts_lay.setContentsMargins(0, 0, 0, 0)
    shortcuts_lay.setSpacing(12)
    keys_box, keys_lay = card()
    keys_head = QtWidgets.QLabel("Shortcuts")
    keys_head.setObjectName("h2")
    keys_lay.addWidget(keys_head)
    keys_form = QtWidgets.QFormLayout()
    keys_form.setSpacing(8)
    for keys, action in (("Ctrl+1 … Ctrl+6", "Switch screens"),
                         ("Enter", "Ask / confirm in the focused field"),
                         ("Esc", "Trip the failsafe during live actuation")):
        label = QtWidgets.QLabel(keys)
        label.setObjectName("h2")
        keys_form.addRow(label, QtWidgets.QLabel(action))
    keys_lay.addLayout(keys_form)
    shortcuts_lay.addWidget(keys_box)
    shortcuts_lay.addStretch(1)
    stack.addWidget(shortcuts)

    # -- Integrations ----------------------------------------------------
    integrations = QtWidgets.QWidget()
    integ_lay = QtWidgets.QVBoxLayout(integrations)
    integ_lay.setContentsMargins(0, 0, 0, 0)
    integ_lay.setSpacing(12)
    integ_box, integ_box_lay = card()
    integ_head = QtWidgets.QLabel("Integrations")
    integ_head.setObjectName("h2")
    integ_sub = QtWidgets.QLabel(
        "Cobot hardware backends. Missing packages only disable live "
        "actuation — everything else keeps working.")
    integ_sub.setObjectName("muted")
    integ_sub.setWordWrap(True)
    integ_box_lay.addWidget(integ_head)
    integ_box_lay.addWidget(integ_sub)
    extras_form = QtWidgets.QFormLayout()
    extras_form.setSpacing(6)
    import importlib.util
    for label, modules, purpose in EXTRAS:
        available = any(importlib.util.find_spec(m) is not None
                        for m in modules)
        mark = QtWidgets.QLabel("Installed" if available else "Not installed")
        mark.setObjectName("ok" if available else "muted")
        extras_form.addRow(f"{label} — {purpose}:", mark)
    integ_box_lay.addLayout(extras_form)
    integ_lay.addWidget(integ_box)
    act_box, act_box_lay = card()
    act_head = QtWidgets.QLabel("Actuation self-test")
    act_head.setObjectName("h2")
    act_sub = QtWidgets.QLabel(
        "Plans a cursor glide and typing cadence without touching hardware.")
    act_sub.setObjectName("muted")
    act_box_lay.addWidget(act_head)
    act_box_lay.addWidget(act_sub)
    selftest_btn = QtWidgets.QPushButton("Run self-test")
    selftest_btn.setObjectName("soft")
    selftest_row = QtWidgets.QHBoxLayout()
    selftest_row.addWidget(selftest_btn)
    selftest_row.addStretch(1)
    act_box_lay.addLayout(selftest_row)
    selftest_out = QtWidgets.QPlainTextEdit()
    selftest_out.setReadOnly(True)
    selftest_out.setMaximumHeight(120)
    act_box_lay.addWidget(selftest_out)
    integ_lay.addWidget(act_box)
    integ_lay.addStretch(1)
    stack.addWidget(integrations)

    def on_selftest() -> None:
        from mimir.actuator import (
            CursorPoint,
            plan_keystroke_delays,
            plan_trajectory,
        )
        points = plan_trajectory(CursorPoint(x=100, y=100),
                                 CursorPoint(x=400, y=300), 30)
        delays = plan_keystroke_delays("Hi, MIMIR.", 70)
        selftest_out.setPlainText(
            f"Glide: {len(points)} eased waypoints from (100, 100) to "
            f"(400, 300).\nTyping: 10 chars in "
            f"{sum(delays) * 1000:.0f} ms with syntactic pauses.\n"
            "Failsafe: human touch aborts within milliseconds.")

    selftest_btn.clicked.connect(on_selftest)
    nav.currentRowChanged.connect(stack.setCurrentIndex)
    nav.setCurrentRow(0)
    return root
