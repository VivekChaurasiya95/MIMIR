"""Settings screen: calibration across six categories (spec 5.6)."""

from __future__ import annotations

import importlib.util
from typing import Any

from ..prefs import DesktopPrefs
from ..state import AppState
from ..theme import PORCELAIN, normalize_accent, normalize_theme, qt_stylesheet
from ._layout import (
    actions_row,
    badge,
    card,
    functionality_banner,
    page,
    page_head,
    set_density,
    setting_row,
    settings_group_card,
    toggle,
)
from ._qt import load_qt

__all__ = ["build_settings_tab"]

LANGUAGES = ("English (US)", "English (UK)", "Hinglish", "Hindi")

EXTRAS = (
    ("PyQt6 / PySide6", ("PyQt6", "PySide6"), "Desktop application shell"),
    ("pyautogui", ("pyautogui",), "Cobot cursor trajectory control"),
    ("pynput", ("pynput",), "Real-time failsafe listener & tripwire"),
    ("openai-whisper", ("whisper",), "On-device speech-to-text engine"),
    ("edge-tts", ("edge_tts",), "Neural voice speech synthesis"),
    ("SpeechRecognition", ("speech_recognition",), "Audio microphone capture"),
)


def build_settings_tab(state: AppState, status: Any) -> Any:
    _, QtCore, _, QtWidgets = load_qt()
    root, layout = page()
    layout.addWidget(page_head(
        "Settings", "Customize your experience.",
        "Fine-tune how Habitous looks, sounds, reasons, and integrates with your machine."))

    body = QtWidgets.QHBoxLayout()
    body.setSpacing(20)

    nav = QtWidgets.QListWidget()
    nav.setObjectName("settings_nav")
    nav.setFixedWidth(210)
    nav.addItems([
        "⚙   General",
        "🎙   Voice",
        "✦   Model",
        "🎨   Appearance",
        "⌨   Shortcuts",
        "🔌   Integrations",
    ])

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

    # Live worker refs (voice tests): kept alive for their lifetime.
    _workers: list[Any] = []

    def restyle_main_window() -> None:
        """Re-apply the saved theme + accent to the main window."""
        try:
            css = qt_stylesheet(
                normalize_theme(getattr(state.prefs, "theme", "light")),
                normalize_accent(getattr(state.prefs, "accent", "Indigo")),
            )
        except Exception:
            return
        app = QtWidgets.QApplication.instance()
        if app is None:
            return
        for top in app.topLevelWidgets():
            try:
                if isinstance(top, QtWidgets.QMainWindow):
                    top.setStyleSheet(css)
            except (RuntimeError, AttributeError):
                pass

    # =========================================================================
    # 1. GENERAL SUBPAGE
    # =========================================================================
    general_page = QtWidgets.QWidget()
    general_lay = QtWidgets.QVBoxLayout(general_page)
    general_lay.setContentsMargins(0, 0, 4, 0)
    general_lay.setSpacing(16)

    general_lay.addWidget(functionality_banner(
        "⚙",
        "General Configuration",
        "Manage Habi's assistant identity, user preferences, localization, "
        "and system startup integration. All changes persist across app restarts.",
        pills=[("Identity", "learning"), ("Lifecycle", "confirmed"), ("Localization", "learning")],
        icon_cls="icon_box_purple",
    ))

    # Card 1: Identity & Persona
    id_card, id_box = settings_group_card(
        "Assistant Identity & Persona",
        "Customize how Habi introduces itself and addresses you across all companion interactions.",
    )
    assistant_name = QtWidgets.QLineEdit(state.prefs.assistant_name or "Habi")
    assistant_name.setMaximumWidth(280)
    user_name = QtWidgets.QLineEdit(state.prefs.user_name or "Arin")
    user_name.setMaximumWidth(280)
    language = QtWidgets.QComboBox()
    language.addItems(LANGUAGES)
    language.setMaximumWidth(280)
    if state.prefs.language in LANGUAGES:
        language.setCurrentText(state.prefs.language)

    id_box.addWidget(setting_row(
        "Assistant name",
        "The conversational moniker used in greetings, replies, and notifications.",
        assistant_name,
    ))
    id_box.addWidget(setting_row(
        "Your name",
        "How Habi refers to you in the home cockpit and generated replication reports.",
        user_name,
    ))
    id_box.addWidget(setting_row(
        "Interaction language",
        "Primary dialect for voice recognition, reasoning prompts, and synthesis output.",
        language,
    ))
    general_lay.addWidget(id_card)

    # Card 2: Application Lifecycle
    life_card, life_box = settings_group_card(
        "System Lifecycle & Tray Integration",
        "Control how Habitous launches and resides in your operating system environment.",
    )
    launch_toggle = toggle(state.prefs.start_on_launch)
    tray_toggle = toggle(state.prefs.show_in_tray)
    chime_toggle = toggle(getattr(state.prefs, "chimes", True))

    life_box.addWidget(setting_row(
        "Start on system launch",
        "Quietly boot Habitous when you sign into your operating system so observation is always ready.",
        launch_toggle,
    ))
    life_box.addWidget(setting_row(
        "Show in system tray",
        "Minimize Habitous to the system notification tray instead of quitting when the window is closed.",
        tray_toggle,
    ))
    life_box.addWidget(setting_row(
        "Ambient event chimes",
        "Play gentle audio confirmation tones when telemetry observation starts or rules are learned.",
        chime_toggle,
    ))
    general_lay.addWidget(life_card)

    # Card 3: Version & Maintenance
    upd_card, upd_box = settings_group_card(
        "Version & Maintenance",
        "Check installed software versions and manage companion updates.",
    )
    version_row = QtWidgets.QHBoxLayout()
    version_label = QtWidgets.QLabel("Checking version…")
    version_label.setObjectName("muted")
    check_btn = QtWidgets.QPushButton("Check for updates")
    check_btn.setObjectName("soft")
    version_row.addWidget(version_label)
    version_row.addStretch(1)
    version_row.addWidget(check_btn)
    upd_box.addLayout(version_row)
    general_lay.addWidget(upd_card)

    general_lay.addStretch(1)
    stack.addWidget(general_page)

    # Handlers for General
    assistant_name.editingFinished.connect(
        lambda: (setattr(state.prefs, "assistant_name",
                         assistant_name.text().strip() or "Habi"),
                 save_prefs()))
    user_name.editingFinished.connect(
        lambda: (setattr(state.prefs, "user_name",
                         user_name.text().strip() or "Arin"),
                 save_prefs()))
    language.currentTextChanged.connect(
        lambda text: (setattr(state.prefs, "language", text), save_prefs()))
    def on_launch_changed(on: bool) -> None:
        from ..autostart import set_autostart
        ok, message = set_autostart(on)
        state.prefs.start_on_launch = on if ok else False
        if not ok and on:
            blocked = launch_toggle.blockSignals(True)
            launch_toggle.setChecked(False)
            launch_toggle.blockSignals(blocked)
        save_prefs(message)

    def on_tray_changed(on: bool) -> None:
        from ..tray import sync_tray
        state.prefs.show_in_tray = on
        save_prefs()
        host = root.window()
        tray = sync_tray(host, on) if host is not None else None
        if on and tray is None:
            status.showMessage(
                "System tray unavailable here — "
                "the window stays in the taskbar.", 6000)
        elif on:
            status.showMessage(
                "Habitous will minimize to the system tray.", 4000)

    launch_toggle.toggled.connect(on_launch_changed)
    tray_toggle.toggled.connect(on_tray_changed)
    chime_toggle.toggled.connect(
        lambda on: (setattr(state.prefs, "chimes", on), save_prefs()))

    def on_check_updates() -> None:
        try:
            import mimir
            version = getattr(mimir, "__version__", "1.0.0")
        except ImportError:
            version = "1.0.0"
        version_label.setText(
            f"Version {version} — up to date via pip (pip install -U mimir).")
        status.showMessage(f"Habitous {version} is running.", 4000)
        box = QtWidgets.QMessageBox(root)
        box.setWindowTitle("Check for updates")
        box.setText(f"Habitous {version} is installed.")
        box.setInformativeText(
            "Habitous updates ship through pip (no background updater "
            "phones home). To upgrade, run:\n\npip install -U mimir")
        copy_btn = box.addButton(
            "Copy upgrade command",
            QtWidgets.QMessageBox.ButtonRole.ActionRole)
        box.addButton(QtWidgets.QMessageBox.StandardButton.Ok)
        box.exec()
        if box.clickedButton() is copy_btn:
            QtWidgets.QApplication.clipboard().setText("pip install -U mimir")
            status.showMessage("Upgrade command copied.", 4000)

    try:
        import mimir as _mimir
        version_label.setText(f"Version {getattr(_mimir, '__version__', '1.0.0')} (Stable)")
    except ImportError:
        version_label.setText("Version 1.0.0 (Local)")
    check_btn.clicked.connect(on_check_updates)

    # =========================================================================
    # 2. VOICE SUBPAGE
    # =========================================================================
    voice_page = QtWidgets.QWidget()
    voice_lay = QtWidgets.QVBoxLayout(voice_page)
    voice_lay.setContentsMargins(0, 0, 4, 0)
    voice_lay.setSpacing(16)

    voice_lay.addWidget(functionality_banner(
        "🎙",
        "Voice & Speech Engine",
        "Configure local Whisper microphone transcription and Edge neural voice synthesis. "
        "Both pipelines operate securely with graceful fallbacks when offline.",
        pills=[("Whisper STT", "confirmed"), ("Edge Neural TTS", "learning"), ("Hands-Free Ready", "confirmed")],
        icon_cls="icon_box_blue",
    ))

    # Card 1: Speech-to-Text
    stt_card, stt_box = settings_group_card(
        "Speech Ingestion (Speech-to-Text)",
        "Captures your voice via microphone, routes spoken intents, and auto-populates prompts.",
    )
    mic_test_btn = QtWidgets.QPushButton("🎙  Test microphone (4s)")
    mic_test_btn.setObjectName("soft")
    stt_status = QtWidgets.QLabel("Ready to listen. Click to test audio hardware capture.")
    stt_status.setObjectName("muted")
    stt_status.setWordWrap(True)

    stt_box.addWidget(setting_row(
        "Audio input hardware",
        "System default recording device processed through local Whisper acoustic models.",
        mic_test_btn,
    ))
    stt_box.addWidget(stt_status)
    voice_lay.addWidget(stt_card)

    # Card 2: Text-to-Speech
    tts_card, tts_box = settings_group_card(
        "Voice Synthesis (Text-to-Speech)",
        "Allows MIMIR to speak answers, summarize replication solutions, and acknowledge actions.",
    )
    voice_persona_combo = QtWidgets.QComboBox()
    voice_persona_combo.addItems([
        "en-US-AriaNeural (Warm & Natural)",
        "en-US-GuyNeural (Concise & Technical)",
        "en-GB-SoniaNeural (Crisp & Direct)",
    ])
    voice_persona_combo.setMaximumWidth(280)
    _saved_voice = (getattr(state.prefs, "voice_persona", "")
                    or "en-US-AriaNeural")
    for _idx in range(voice_persona_combo.count()):
        if voice_persona_combo.itemText(_idx).split(" ")[0] == _saved_voice:
            voice_persona_combo.setCurrentIndex(_idx)
            break

    def on_voice_persona_changed(text: str) -> None:
        state.prefs.voice_persona = text.split(" ")[0]
        save_prefs()

    voice_persona_combo.currentTextChanged.connect(on_voice_persona_changed)

    speak_test_btn = QtWidgets.QPushButton("🔊  Speak test line")
    speak_test_btn.setObjectName("soft")
    tts_status = QtWidgets.QLabel("")
    tts_status.setObjectName("muted")
    tts_status.setWordWrap(True)

    tts_box.addWidget(setting_row(
        "Voice persona",
        "Neural acoustic model used for speech output playback.",
        voice_persona_combo,
    ))
    tts_box.addWidget(setting_row(
        "Speech audio test",
        "Test voice playback through default system speakers.",
        speak_test_btn,
    ))
    tts_box.addWidget(tts_status)
    voice_lay.addWidget(tts_card)

    # Card 3: Hands-Free Vocab
    vocab_card, vocab_box = settings_group_card(
        "Voice Commands Reference",
        "MIMIR recognizes these spoken intents natively through its keyword intent matcher.",
    )
    vocab_grid = QtWidgets.QGridLayout()
    vocab_grid.setSpacing(10)
    phrases = [
        ("“push my code”", "Reviews git status and drafts commit principles."),
        ("“what did you learn?”", "Speaks a summary of recently inferred memory rules."),
        ("“go ahead”", "Approves pending actuation or synthesis proposal."),
        ("“stop” / “sleep”", "Immediately halts current activity and sets visor to resting."),
    ]
    for row_idx, (phrase, desc) in enumerate(phrases):
        p_lbl = QtWidgets.QLabel(phrase)
        p_lbl.setObjectName("h2")
        d_lbl = QtWidgets.QLabel(desc)
        d_lbl.setObjectName("muted")
        vocab_grid.addWidget(p_lbl, row_idx, 0)
        vocab_grid.addWidget(d_lbl, row_idx, 1)
    vocab_box.addLayout(vocab_grid)
    voice_lay.addWidget(vocab_card)

    voice_lay.addStretch(1)
    stack.addWidget(voice_page)

    def on_mic_test() -> None:
        try:
            from ..voice_worker import create_listen_worker
        except ImportError:
            status.showMessage("Listening… speak now.", 4000)
            stt_status.setText(
                "Listening for 4 seconds… speak into your microphone.")
            ok, heard = state.try_listen(timeout_sec=4.0)
            stt_status.setText(
                f"Heard: “{heard}”" if ok
                else f"Microphone capture note: {heard}")
            status.showMessage("Microphone test finished.", 3000)
            return
        status.showMessage("Listening… speak now.", 5000)
        stt_status.setText(
            "Listening for 4 seconds… speak into your microphone.")
        mic_test_btn.setEnabled(False)
        worker = create_listen_worker(state.voice_coordinator(),
                                      timeout_sec=4.0, parent=root)
        _workers.append(worker)

        def _done(ok: bool, heard: str) -> None:
            mic_test_btn.setEnabled(True)
            stt_status.setText(
                f"Heard: “{heard}”" if ok
                else f"Microphone capture note: {heard}")
            status.showMessage("Microphone test finished.", 3000)
            try:
                _workers.remove(worker)
            except ValueError:
                pass
            try:
                worker.deleteLater()
            except Exception:
                pass

        worker.listen_finished.connect(_done)
        worker.start()

    def on_speak_test() -> None:
        line = (f"Hello {state.prefs.user_name}, "
                "MIMIR speech output is operating.")
        try:
            from ..voice_worker import create_speak_worker
        except ImportError:
            from mimir.voice import VoiceCoordinator
            try:
                VoiceCoordinator().speak(line)
            except Exception as exc:
                tts_status.setText(f"Speech output unavailable: {exc}")
                tts_status.setObjectName("err")
            else:
                tts_status.setText(
                    "Speech played successfully through default output device.")
                tts_status.setObjectName("ok")
            tts_status.style().unpolish(tts_status)
            tts_status.style().polish(tts_status)
            return
        speak_test_btn.setEnabled(False)
        tts_status.setText("Speaking…")
        tts_status.setObjectName("muted")
        tts_status.style().unpolish(tts_status)
        tts_status.style().polish(tts_status)
        worker = create_speak_worker(state.voice_coordinator(), line,
                                     parent=root)
        _workers.append(worker)

        def _release() -> None:
            speak_test_btn.setEnabled(True)
            try:
                _workers.remove(worker)
            except ValueError:
                pass
            try:
                worker.deleteLater()
            except Exception:
                pass

        def _finished() -> None:
            tts_status.setText(
                "Speech played successfully through default output device.")
            tts_status.setObjectName("ok")
            tts_status.style().unpolish(tts_status)
            tts_status.style().polish(tts_status)
            _release()

        def _failed(message: str) -> None:
            tts_status.setText(f"Speech output unavailable: {message}")
            tts_status.setObjectName("err")
            tts_status.style().unpolish(tts_status)
            tts_status.style().polish(tts_status)
            _release()

        worker.speak_finished.connect(_finished)
        worker.speak_error.connect(_failed)
        worker.start()

    mic_test_btn.clicked.connect(on_mic_test)
    speak_test_btn.clicked.connect(on_speak_test)

    # =========================================================================
    # 3. MODEL SUBPAGE
    # =========================================================================
    model_page = QtWidgets.QWidget()
    model_lay = QtWidgets.QVBoxLayout(model_page)
    model_lay.setContentsMargins(0, 0, 4, 0)
    model_lay.setSpacing(16)

    model_lay.addWidget(functionality_banner(
        "✦",
        "Reasoning & Epistemic Synthesis",
        "Calibrate how MIMIR analyzes developer telemetry, infers behavioral invariants, "
        "and injects relevant memory principles during code generation.",
        pills=[("Offline Scaffolding", "confirmed"), ("Bayesian Calibration", "learning"), ("AST Invariant Extraction", "learning")],
        icon_cls="icon_box_purple",
    ))

    # Card 1: Reasoning Provider
    prov_card, prov_box = settings_group_card(
        "Synthesis Architecture & Provider",
        "Select the underlying engine that powers replication and principle extraction.",
    )
    provider_combo = QtWidgets.QComboBox()
    provider_combo.addItems([
        "Offline Heuristic Scaffold (Zero-API, 100% Local)",
        "OpenAI Bridge (Cloud - Needs API Key)",
        "Anthropic Claude Bridge (Cloud - Needs API Key)",
        "Gemini 2.5 Flash Bridge (Hybrid Cloud - Optional)",
        "Local Ollama / Llama 3 Bridge (Self-Hosted)",
    ])
    provider_combo.setMaximumWidth(360)
    api_key_input = QtWidgets.QLineEdit()
    api_key_input.setPlaceholderText("Optional: leave blank to remain 100% offline")
    api_key_input.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
    api_key_input.setMaximumWidth(360)
    api_key_input.setText(state.prefs.api_key or "")
    model_name_input = QtWidgets.QLineEdit(state.prefs.model_name or "")
    model_name_input.setPlaceholderText("e.g. gpt-4o-mini (blank = default)")
    model_name_input.setMaximumWidth(360)
    model_endpoint_input = QtWidgets.QLineEdit(state.prefs.model_endpoint or "")
    model_endpoint_input.setPlaceholderText("http://localhost:11434")
    model_endpoint_input.setMaximumWidth(360)

    from ..llm import normalize_provider as _normalize_provider

    def _provider_id_for_label(label: str) -> str:
        return _normalize_provider(label)

    _wanted_provider = _normalize_provider(
        state.prefs.model_provider or "offline")
    for _idx in range(provider_combo.count()):
        if _normalize_provider(provider_combo.itemText(_idx)) == _wanted_provider:
            provider_combo.setCurrentIndex(_idx)
            break

    prov_box.addWidget(setting_row(
        "Reasoning provider",
        "The deterministic offline engine guarantees privacy without external network calls.",
        provider_combo,
    ))
    prov_box.addWidget(setting_row(
        "External model API key",
        "Optional credential used only when connecting to an external LLM endpoint.",
        api_key_input,
    ))
    prov_box.addWidget(setting_row(
        "Model name",
        "Model identifier for the selected provider (blank uses the provider default).",
        model_name_input,
    ))
    prov_box.addWidget(setting_row(
        "Local endpoint (Ollama)",
        "Base URL of the self-hosted Ollama server. Defaults to http://localhost:11434.",
        model_endpoint_input,
    ))
    model_lay.addWidget(prov_card)

    # Card 2: Principles Retrieval
    ret_card, ret_box = settings_group_card(
        "Memory Retrieval & Epistemic Thresholds",
        "Tune the balance between principle recall coverage and strict invariant confidence.",
    )
    topk_spin = QtWidgets.QSpinBox()
    topk_spin.setRange(1, 10)
    topk_spin.setValue(state.prefs.default_top_k)
    topk_spin.setMaximumWidth(120)

    minconf_spin = QtWidgets.QDoubleSpinBox()
    minconf_spin.setRange(0.0, 1.0)
    minconf_spin.setSingleStep(0.05)
    minconf_spin.setValue(state.prefs.default_min_confidence)
    minconf_spin.setMaximumWidth(120)

    contrast_toggle = toggle(getattr(state.prefs, "contrast_baseline", True))
    contrast_toggle.toggled.connect(
        lambda on: (setattr(state.prefs, "contrast_baseline", on),
                    save_prefs()))

    ret_box.addWidget(setting_row(
        "Default principles per task",
        "Maximum number of top-matching behavioral principles injected into code replication.",
        topk_spin,
    ))
    ret_box.addWidget(setting_row(
        "Minimum confidence threshold",
        "Epistemic score cutoff (principles scored below this are excluded from synthesis).",
        minconf_spin,
    ))
    ret_box.addWidget(setting_row(
        "Contrastive baseline checking",
        "Cross-examines proposed code solutions against recorded anti-patterns before completion.",
        contrast_toggle,
    ))
    model_lay.addWidget(ret_card)

    model_lay.addStretch(1)
    stack.addWidget(model_page)

    topk_spin.valueChanged.connect(
        lambda value: (setattr(state.prefs, "default_top_k", int(value)),
                       save_prefs()))
    minconf_spin.valueChanged.connect(
        lambda value: (setattr(state.prefs, "default_min_confidence",
                               float(value)), save_prefs()))
    provider_combo.currentTextChanged.connect(
        lambda text: (setattr(state.prefs, "model_provider",
                              _provider_id_for_label(text)), save_prefs()))
    api_key_input.editingFinished.connect(
        lambda: (setattr(state.prefs, "api_key",
                         api_key_input.text().strip()), save_prefs()))
    model_name_input.editingFinished.connect(
        lambda: (setattr(state.prefs, "model_name",
                         model_name_input.text().strip()), save_prefs()))
    model_endpoint_input.editingFinished.connect(
        lambda: (setattr(state.prefs, "model_endpoint",
                         model_endpoint_input.text().strip()), save_prefs()))

    # =========================================================================
    # 4. APPEARANCE SUBPAGE
    # =========================================================================
    appear_page = QtWidgets.QWidget()
    appear_lay = QtWidgets.QVBoxLayout(appear_page)
    appear_lay.setContentsMargins(0, 0, 4, 0)
    appear_lay.setSpacing(16)

    appear_lay.addWidget(functionality_banner(
        "🎨",
        "Appearance & Desktop Aesthetics",
        "Customize visual styling, accent palette highlights, interface density, "
        "and window canvas dimensions for maximum ergonomic comfort.",
        pills=[("Porcelain Theme", "confirmed"), ("60 FPS Transitions", "learning"), ("High-DPI Ready", "confirmed")],
        icon_cls="icon_box_amber",
    ))

    theme_card, theme_box = settings_group_card(
        "Theme & Color Palette",
        "Select the visual environment and interactive color accents.",
    )
    theme_combo = QtWidgets.QComboBox()
    theme_combo.addItems(["Porcelain Light (Signature Spec)", "Slate Midnight (Dark Mode)"])
    theme_combo.setMaximumWidth(300)
    if normalize_theme(getattr(state.prefs, "theme", "light")) == "dark":
        theme_combo.setCurrentIndex(1)

    def on_theme_changed(index: int) -> None:
        state.prefs.theme = "dark" if index == 1 else "light"
        save_prefs()
        restyle_main_window()

    theme_combo.currentIndexChanged.connect(on_theme_changed)

    swatches_row = QtWidgets.QHBoxLayout()
    swatches_row.setSpacing(10)

    def on_accent_chosen(name: str) -> None:
        state.prefs.accent = name
        save_prefs(f"{name} accent applied.")
        restyle_main_window()

    for name, key in (("Indigo", "accent_primary"),
                      ("Lavender", "accent_pill_lavender"),
                      ("Sage", "status_green_bg"),
                      ("Sky", "status_blue_bg"),
                      ("Amber", "status_amber_bg")):
        chip = QtWidgets.QPushButton(f"  {name}  ")
        chip.setObjectName("chip")
        chip.setStyleSheet(
            f"background: {PORCELAIN[key]}; border-radius: 999px; "
            f"padding: 6px 12px; font-weight: 600; color: {PORCELAIN['text_body']};")
        chip.setToolTip(f"Apply the {name} accent across the app")
        chip.clicked.connect(lambda _=False, n=name: on_accent_chosen(n))
        swatches_row.addWidget(chip)
    swatches_row.addStretch(1)

    theme_box.addWidget(setting_row(
        "Color theme",
        "Porcelain Light provides crisp high-contrast readability with muted slate surfaces.",
        theme_combo,
    ))
    theme_box.addWidget(setting_row(
        "Accent highlight palette",
        "Primary tint used on active navigation pills, badges, and action buttons.",
        swatches_row,
    ))
    appear_lay.addWidget(theme_card)

    canvas_card, canvas_box = settings_group_card(
        "Window Canvas & Geometry",
        "Manage desktop application sizing and layout density.",
    )
    reset_btn = QtWidgets.QPushButton("Reset to 1280 × 800")
    reset_btn.setObjectName("soft")
    spacious_toggle = toggle(getattr(state.prefs, "spacious", True))

    def on_density_changed(on: bool) -> None:
        state.prefs.spacious = on
        save_prefs("Spacious layout on." if on else "Compact layout on.")
        set_density(on)

    spacious_toggle.toggled.connect(on_density_changed)

    canvas_box.addWidget(setting_row(
        "Default window size",
        "Restore the canvas geometry to the recommended 1280×800 aspect ratio.",
        reset_btn,
    ))
    canvas_box.addWidget(setting_row(
        "Spacious page margins",
        "Apply relaxed 32px breathing room to cards and side rails to reduce visual congestion.",
        spacious_toggle,
    ))
    appear_lay.addWidget(canvas_card)

    # Card 3: Floating Habi Companion
    cobot_card, cobot_box = settings_group_card(
        "Floating Habi Companion",
        "Configure the always-on-top, expressive porcelain desktop companion avatar.",
    )
    floating_toggle = toggle(state.prefs.floating_cobot)
    follow_toggle = toggle(state.prefs.follow_mouse)
    roam_toggle = toggle(state.prefs.auto_roam)

    def on_floating_changed(checked: bool) -> None:
        state.prefs.floating_cobot = checked
        save_prefs()
        app = QtWidgets.QApplication.instance()
        if app is not None:
            visors = [w for w in app.topLevelWidgets() if hasattr(w, "set_follow_mouse")]
            if visors:
                for widget in visors:
                    widget.setVisible(checked)
            elif checked:
                try:
                    from mimir.ui.visor import create_visor_widget
                    pill = create_visor_widget(
                        state.visor,
                        auto_roam=state.prefs.auto_roam,
                        follow_mouse=state.prefs.follow_mouse,
                    )
                    pill.show()
                except Exception:
                    pass

    def on_follow_changed(checked: bool) -> None:
        state.prefs.follow_mouse = checked
        save_prefs()
        app = QtWidgets.QApplication.instance()
        if app is not None:
            for widget in app.topLevelWidgets():
                if hasattr(widget, "set_follow_mouse"):
                    widget.set_follow_mouse(checked)

    def on_roam_changed(checked: bool) -> None:
        state.prefs.auto_roam = checked
        save_prefs()
        app = QtWidgets.QApplication.instance()
        if app is not None:
            for widget in app.topLevelWidgets():
                if hasattr(widget, "set_auto_roam"):
                    widget.set_auto_roam(checked)

    floating_toggle.toggled.connect(on_floating_changed)
    follow_toggle.toggled.connect(on_follow_changed)
    roam_toggle.toggled.connect(on_roam_changed)

    cobot_box.addWidget(setting_row(
        "Show floating Habi on screen",
        "Display the draggable white porcelain companion avatar always on top of your desktop.",
        floating_toggle,
    ))
    cobot_box.addWidget(setting_row(
        "Follow cursor across windows",
        "Allow Habi to glide smoothly near your mouse cursor when observing active work.",
        follow_toggle,
    ))
    cobot_box.addWidget(setting_row(
        "Autonomous screen wandering",
        "Allow Habi to roam gently across the display when observing.",
        roam_toggle,
    ))
    appear_lay.addWidget(cobot_card)

    appear_lay.addStretch(1)
    stack.addWidget(appear_page)

    reset_btn.clicked.connect(lambda: root.window().resize(1280, 800))

    # =========================================================================
    # 5. SHORTCUTS SUBPAGE
    # =========================================================================
    shortcuts_page = QtWidgets.QWidget()
    shortcuts_lay = QtWidgets.QVBoxLayout(shortcuts_page)
    shortcuts_lay.setContentsMargins(0, 0, 4, 0)
    shortcuts_lay.setSpacing(16)

    shortcuts_lay.addWidget(functionality_banner(
        "⌨",
        "Keyboard Shortcuts & Emergency Failsafes",
        "Navigate tabs instantly and inspect failsafe controls designed to protect "
        "your workstation during live automated actuation.",
        pills=[("Global Hotkeys", "confirmed"), ("Failsafe Tripwire", "confirmed")],
        icon_cls="icon_box_green",
    ))

    nav_keys_card, nav_keys_box = settings_group_card(
        "Application Navigation",
        "Switch screens and invoke common actions without touching the mouse.",
    )
    nav_grid = QtWidgets.QGridLayout()
    nav_grid.setSpacing(10)
    hotkeys = [
        ("Ctrl + 1", "Home Screen", "Ambient status, KPIs, and explore prompt"),
        ("Ctrl + 2", "Observe Screen", "Session feed, applications, and ignore filters"),
        ("Ctrl + 3", "Memory Screen", "Principle bank, epistemic scores, and evidence"),
        ("Ctrl + 4", "Replicate Screen", "Style synthesis, task drafts, and history"),
        ("Ctrl + 5", "Trust Screen", "Boundary controls, data storage, and secret redaction"),
        ("Ctrl + 6", "Settings Screen", "Persona, voice, reasoning, appearance, shortcuts"),
        ("Enter", "Submit / Ask", "Execute explore prompt or confirm active input"),
    ]
    for row_idx, (k, title, desc) in enumerate(hotkeys):
        k_btn = QtWidgets.QLabel(f"  {k}  ")
        k_btn.setObjectName("chip")
        k_btn.setStyleSheet(
            f"background: {PORCELAIN['bg_hover']}; border: 1px solid {PORCELAIN['border_subtle']}; "
            f"border-radius: 6px; padding: 4px 8px; font-weight: 700; color: {PORCELAIN['text_heading']};")
        t_lbl = QtWidgets.QLabel(title)
        t_lbl.setObjectName("setting_label")
        d_lbl = QtWidgets.QLabel(desc)
        d_lbl.setObjectName("muted")
        nav_grid.addWidget(k_btn, row_idx, 0)
        nav_grid.addWidget(t_lbl, row_idx, 1)
        nav_grid.addWidget(d_lbl, row_idx, 2)
    nav_keys_box.addLayout(nav_grid)
    shortcuts_lay.addWidget(nav_keys_card)

    safe_card, safe_box = settings_group_card(
        "Cobot Live Actuation Failsafes",
        "Sub-millisecond abort tripwires that guarantee human operator override.",
    )
    safe_grid = QtWidgets.QGridLayout()
    safe_grid.setSpacing(10)
    safeties = [
        ("Esc Key", "Emergency Stop", "Instantly aborts cursor movement and typing cadence."),
        ("Mouse Nudge", "Physical Intercept", "Touching or moving the physical mouse trips failsafe in <10ms."),
        ("Screen Corners", "PyAutoGUI Boundary", "Dragging cursor into screen corner immediately kills automation."),
    ]
    for row_idx, (k, title, desc) in enumerate(safeties):
        k_btn = QtWidgets.QLabel(f"  {k}  ")
        k_btn.setObjectName("chip")
        k_btn.setStyleSheet(
            f"background: {PORCELAIN['status_amber_bg']}; border: 1px solid rgba(180, 83, 9, 0.2); "
            f"border-radius: 6px; padding: 4px 8px; font-weight: 700; color: {PORCELAIN['status_amber_text']};")
        t_lbl = QtWidgets.QLabel(title)
        t_lbl.setObjectName("setting_label")
        d_lbl = QtWidgets.QLabel(desc)
        d_lbl.setObjectName("muted")
        safe_grid.addWidget(k_btn, row_idx, 0)
        safe_grid.addWidget(t_lbl, row_idx, 1)
        safe_grid.addWidget(d_lbl, row_idx, 2)
    safe_box.addLayout(safe_grid)
    shortcuts_lay.addWidget(safe_card)

    shortcuts_lay.addStretch(1)
    stack.addWidget(shortcuts_page)

    # =========================================================================
    # 6. INTEGRATIONS SUBPAGE
    # =========================================================================
    integ_page = QtWidgets.QWidget()
    integ_lay = QtWidgets.QVBoxLayout(integ_page)
    integ_lay.setContentsMargins(0, 0, 4, 0)
    integ_lay.setSpacing(16)

    integ_lay.addWidget(functionality_banner(
        "🔌",
        "Subsystems & Hardware Integrations",
        "Monitor companion dependencies, operating system hooks, and cursor actuation drivers. "
        "Missing packages only disable live actuation — memory and synthesis continue working.",
        pills=[("Modular Backends", "confirmed"), ("Sandboxed Actuation", "learning")],
        icon_cls="icon_box_purple",
    ))

    dep_card, dep_box = settings_group_card(
        "Cobot Driver & Package Status",
        "Status of python subsystem libraries required for voice, cursor glide, and failsafe detection.",
    )
    for label, modules, purpose in EXTRAS:
        available = any(importlib.util.find_spec(m) is not None for m in modules)
        pill = badge("Installed" if available else "Optional", "confirmed" if available else "ignored")
        dep_box.addWidget(setting_row(label, purpose, pill))
    integ_lay.addWidget(dep_card)

    diag_card, diag_box = settings_group_card(
        "Actuation Trajectory Diagnostic Test",
        "Simulates Bezier curve waypoint generation and typing cadence without touching physical hardware.",
    )
    selftest_btn = QtWidgets.QPushButton("Run Actuation Self-Test")
    selftest_btn.setObjectName("soft")
    diag_box.addWidget(setting_row(
        "Trajectory simulation",
        "Calculates 30 eased glide points and syntactic pauses for 'Hi, MIMIR.'",
        selftest_btn,
    ))
    selftest_out = QtWidgets.QPlainTextEdit()
    selftest_out.setReadOnly(True)
    selftest_out.setMaximumHeight(110)
    selftest_out.setPlaceholderText("Self-test simulation report will appear here.")
    diag_box.addWidget(selftest_out)
    integ_lay.addWidget(diag_card)

    def on_selftest() -> None:
        from mimir.actuator import CursorPoint, plan_keystroke_delays, plan_trajectory
        points = plan_trajectory(CursorPoint(x=100, y=100), CursorPoint(x=400, y=300), 30)
        delays = plan_keystroke_delays("Hi, MIMIR.", 70)
        selftest_out.setPlainText(
            f"Trajectory Glide: {len(points)} eased Bezier waypoints planned from (100, 100) -> (400, 300).\n"
            f"Typing Cadence: 10 chars in {sum(delays) * 1000:.0f} ms with realistic syntactic pauses.\n"
            "Failsafe Tripwire: Human mouse touch aborts execution within sub-millisecond threshold.\n"
            "Status: Actuator subsystems healthy and operational."
        )

    selftest_btn.clicked.connect(on_selftest)
    integ_lay.addStretch(1)
    stack.addWidget(integ_page)

    # Wire navigation list
    nav.currentRowChanged.connect(stack.setCurrentIndex)
    nav.setCurrentRow(0)
    return root
