"""Home cockpit: serif greeting, status card, explore bar, KPIs (spec 5.1)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ..presenters import greeting_for_hour
from ..state import AppState
from ._layout import (
    ClickableCard,
    CobotVisorWidget,
    elevate,
    functionality_banner,
    overline,
    page,
)
from ._qt import load_qt

__all__ = ["build_home_tab"]

CHIP_PROMPTS = (
    ("✦ Explain this code", "Explain this code: "),
    ("◫ Plan a feature", "Help me plan a feature: "),
    ("☼ Brainstorm ideas", "Brainstorm ideas for: "),
)


def build_home_tab(state: AppState, navigate: Callable[[str], None],
                   status: Any, watcher: Any) -> Any:
    _, QtCore, _, QtWidgets = load_qt()
    root, layout = page()

    top = QtWidgets.QHBoxLayout()
    top.setSpacing(24)
    hero = QtWidgets.QVBoxLayout()
    hero.setSpacing(6)
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

    # Co-bot Visor and Status Area (Unboxed, airy, and uncluttered)
    status_area = QtWidgets.QWidget()
    status_area.setObjectName("status_area")
    status_area.setStyleSheet("background: transparent; border: 0;")
    sc = QtWidgets.QVBoxLayout(status_area)
    sc.setContentsMargins(0, 0, 0, 0)
    sc.setSpacing(6)

    visor_row = QtWidgets.QHBoxLayout()
    visor_row.setSpacing(12)

    # Personified Cute 3D Spherical Co-bot Visor Face
    visor_face = CobotVisorWidget(width=58, height=48, on_click=lambda: on_watch())
    visor_face.setToolTip("MIMIR Co-bot Visor — Click to toggle observation")
    visor_row.addWidget(visor_face)

    status_text_col = QtWidgets.QVBoxLayout()
    status_text_col.setSpacing(1)
    state_line = QtWidgets.QLabel("")
    state_line.setObjectName("h2")
    context_line = QtWidgets.QLabel("")
    context_line.setObjectName("muted")
    status_text_col.addWidget(state_line)
    status_text_col.addWidget(context_line)
    visor_row.addLayout(status_text_col)
    sc.addLayout(visor_row)

    btn_row = QtWidgets.QHBoxLayout()
    btn_row.setSpacing(8)
    btn_row.addStretch(1)
    companion_btn = QtWidgets.QPushButton("✨ Habi Companion")
    companion_btn.setObjectName("soft")
    companion_btn.setToolTip("Show or hide the floating on-screen Habi companion")

    def on_toggle_companion() -> None:
        app = QtWidgets.QApplication.instance()
        if app is not None:
            visors = [w for w in app.topLevelWidgets() if hasattr(w, "set_follow_mouse")]
            if visors:
                now_vis = not visors[0].isVisible()
                for v in visors:
                    v.setVisible(now_vis)
                state.prefs.floating_cobot = now_vis
                try:
                    state.prefs.save()
                except Exception:
                    pass
                status.showMessage("Floating Habi " + ("active on screen" if now_vis else "hidden"), 3000)
            else:
                try:
                    from mimir.ui.visor import create_visor_widget
                    pill = create_visor_widget(
                        state.visor,
                        auto_roam=state.prefs.auto_roam,
                        follow_mouse=state.prefs.follow_mouse,
                    )
                    pill.show()
                    state.prefs.floating_cobot = True
                    try:
                        state.prefs.save()
                    except Exception:
                        pass
                    status.showMessage("Floating Habi active on screen", 3000)
                except Exception as exc:
                    status.showMessage(f"Could not launch Habi: {exc}", 5000)

    companion_btn.setFixedHeight(34)
    companion_btn.setStyleSheet("background: #FFFFFF; color: #0F172A; border: 1px solid #CBD5E1; border-radius: 8px; padding: 6px 14px; font-weight: 500; font-size: 12px;")
    companion_btn.clicked.connect(on_toggle_companion)
    btn_row.addWidget(companion_btn)
    watch_btn = QtWidgets.QPushButton("")
    watch_btn.setObjectName("start_obs_btn")
    watch_btn.setFixedHeight(34)
    btn_row.addWidget(watch_btn)
    sc.addLayout(btn_row)

    top.addWidget(status_area)
    layout.addLayout(top)

    layout.addWidget(functionality_banner(
        "✦",
        "Habitous Intelligent Developer Companion",
        "Habitous continuously observes how you code, mines invariant behavioral standards into "
        "inspectable principles, and replicates your authentic style when generating new solutions.",
        pills=[("100% On-Device", "confirmed"), ("Continuous Telemetry", "learning"), ("Adaptive Style", "confirmed")],
        icon_cls="icon_box_purple",
    ))

    # 1-click live showcase: the 4 golden scenes on a background thread.
    showcase_row = QtWidgets.QHBoxLayout()
    showcase_row.setSpacing(12)
    showcase_btn = QtWidgets.QPushButton("▶ Run Live Habi Showcase")
    showcase_btn.setObjectName("generate_btn")
    showcase_btn.setFixedHeight(34)
    showcase_btn.setToolTip(
        "Run Habi's 4-scene live demonstration: observe, voice, drive, E-stop.")
    showcase_status = QtWidgets.QLabel(
        "Experience Habi observe, speak, drive, and learn live.")
    showcase_status.setObjectName("muted")
    showcase_row.addWidget(showcase_btn)
    showcase_row.addWidget(showcase_status)
    showcase_row.addStretch(1)
    layout.addLayout(showcase_row)

    showcase_log = QtWidgets.QPlainTextEdit()
    showcase_log.setReadOnly(True)
    showcase_log.setMaximumHeight(85)
    showcase_log.setMaximumWidth(700)
    showcase_log.setPlaceholderText("Live showcase progress appears here.")
    showcase_log.setVisible(False)
    layout.addWidget(showcase_log)

    explore_pill = QtWidgets.QFrame()
    explore_pill.setObjectName("explore_pill")
    explore_pill.setMaximumWidth(700)
    explore_pill.setFixedHeight(46)
    pill_lay = QtWidgets.QHBoxLayout(explore_pill)
    pill_lay.setContentsMargins(16, 6, 8, 6)
    pill_lay.setSpacing(12)
    sparkle = QtWidgets.QLabel("✦")
    sparkle.setObjectName("faint")
    explore = QtWidgets.QLineEdit()
    explore.setObjectName("explore_input")
    explore.setPlaceholderText("What would you like to explore?")
    explore.setClearButtonEnabled(True)
    mic_btn = QtWidgets.QPushButton("🎙")
    mic_btn.setObjectName("mic_btn")
    mic_btn.setFixedSize(32, 32)
    mic_btn.setToolTip("Speak instead of typing (needs a microphone)")
    pill_lay.addWidget(sparkle)
    pill_lay.addWidget(explore, 1)
    pill_lay.addWidget(mic_btn)
    layout.addWidget(explore_pill)

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
    chips_wrap = QtWidgets.QWidget()
    chips_wrap.setMaximumWidth(700)
    cw_lay = QtWidgets.QHBoxLayout(chips_wrap)
    cw_lay.setContentsMargins(0, 0, 0, 0)
    cw_lay.addLayout(chips)
    layout.addWidget(chips_wrap)

    reply_box = QtWidgets.QFrame()
    reply_box.setObjectName("card")
    reply_box.setMaximumWidth(700)
    reply_box.setVisible(False)
    rl = QtWidgets.QHBoxLayout(reply_box)
    rl.setContentsMargins(14, 10, 14, 10)
    rl.setSpacing(10)
    reply_icon = QtWidgets.QLabel("✦")
    reply_icon.setObjectName("icon_box_purple")
    reply = QtWidgets.QLabel("")
    reply.setObjectName("subtle")
    reply.setWordWrap(True)
    rl.addWidget(reply_icon)
    rl.addWidget(reply, 1)
    layout.addWidget(reply_box)

    kpi_row = QtWidgets.QHBoxLayout()
    kpi_row.setSpacing(14)
    kpi_cards: list[Any] = []

    def kpi(icon: str, caption: str, target: str, icon_cls: str = "icon_box_purple") -> Any:
        card_widget = ClickableCard("kpi_card",
                                    on_click=lambda: navigate(target))
        card_widget.setFixedSize(210, 72)
        lay = QtWidgets.QHBoxLayout(card_widget)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(12)
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
        lay.addWidget(glyph)
        lay.addLayout(text, 1)
        kpi_cards.append(number)
        return card_widget

    layout.addWidget(overline("WORKSPACE OVERVIEW"))
    kpi_row.addWidget(kpi("📖", "Habits learned", "memory", "icon_box_purple"))
    kpi_row.addWidget(kpi("ᯤ", "Sessions watched", "observe", "icon_box_blue"))
    kpi_row.addWidget(kpi("📈", "Confidence score", "memory", "icon_box_green"))
    kpi_row.addStretch(1)
    layout.addLayout(kpi_row)
    layout.addStretch(1)

    # Live worker refs (showcase + voice): kept alive for their lifetime.
    _workers: list[Any] = []
    _showcase: dict[str, Any] = {"thread": None, "runner": None}

    def refresh_content() -> None:
        greet.setText(greeting_for_hour(datetime.now().hour))
        name.setText(state.prefs.user_name or "there")
        tagline.setText(
            f"I'm {state.prefs.assistant_name}. I observe how you work, "
            "learn your style, and apply it when you need me.")
        if watcher.watching:
            state_line.setText("Observing")
            context_line.setText(
                f"Watching {Path(state.prefs.last_repo).name if state.prefs.last_repo else 'workspace'}")
            watch_btn.setText("⏹ Stop Observing")
            watch_btn.setStyleSheet(
                "background: #DC2626; color: white; border: 0; border-radius: 8px; "
                "padding: 6px 14px; font-weight: 600; font-size: 12px; min-height: 20px;")
            visor_face.set_state("observing")
        else:
            state_line.setText("Resting")
            context_line.setText("Ready to observe")
            watch_btn.setText("▷ Start Observing")
            watch_btn.setStyleSheet(
                "background: #4F46E5; color: white; border: 0; border-radius: 8px; "
                "padding: 6px 14px; font-weight: 600; font-size: 12px; min-height: 20px;")
            visor_face.set_state("resting")
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
            status.showMessage("Observation paused. Habi is resting.", 4000)
            return
        repo = state.prefs.last_repo or str(Path.cwd().resolve())
        roots = list(state.prefs.active_roots(state.default_roots()))
        try:
            repo_resolved = str(Path(repo).resolve())
            if repo_resolved not in roots:
                roots.append(repo_resolved)
            if repo_resolved not in state.prefs.last_roots:
                state.prefs.last_roots = state.prefs.last_roots + (repo_resolved,)
                state.prefs.save()
        except Exception:
            if repo not in roots:
                roots.append(repo)
        # Prefer the non-blocking first pass (the UI never freezes
        # on a slow repo); stub watchers in tests only implement start().
        start_async = getattr(watcher, "start_async", None)
        if start_async is None:
            ok, message = watcher.start(repo, roots)
            if not ok:
                status.showMessage(
                    f"Couldn't start observing: {message}", 8000)
            else:
                status.showMessage(
                    "Habitous active observation started for "
                    f"{Path(repo).name}.", 4000)
            refresh_content()
            return

        def _started(ok: bool, message: str) -> None:
            if not ok:
                status.showMessage(
                    f"Couldn't start observing: {message}", 8000)
            else:
                status.showMessage(
                    "Habitous active observation started for "
                    f"{Path(repo).name}.", 4000)
            watch_btn.setEnabled(True)
            refresh_content()

        watch_btn.setEnabled(False)
        status.showMessage("Starting observation…", 4000)
        start_async(repo, roots, 10, _started)

    def _speak_answer(answer: str) -> None:
        """Speak a companion reply aloud without blocking the UI."""
        try:
            from ..voice_worker import create_speak_worker
        except ImportError:
            return
        try:
            worker = create_speak_worker(state.voice_coordinator(), answer,
                                         parent=root)
        except (ValueError, Exception):
            return
        _workers.append(worker)

        def _started() -> None:
            try:
                visor_face.set_state("listening")
            except Exception:
                pass

        def _over() -> None:
            try:
                _workers.remove(worker)
            except ValueError:
                pass
            try:
                worker.deleteLater()
            except Exception:
                pass
            refresh_content()

        worker.speak_started.connect(_started)
        worker.speak_finished.connect(_over)
        worker.speak_error.connect(lambda _message: _over())
        worker.start()

    def on_ask() -> None:
        text = explore.text().strip()
        if not text:
            return
        parsed = state.parse_utterance(text)
        if parsed.get("intent") and parsed["intent"] not in ("unknown", None):
            answer = state.respond(parsed["intent"], parsed["text"])
        else:
            hits = state.search(text, top_k=2, min_confidence=0.0)
            if hits:
                habits_desc = "; ".join(h.name for h in hits)
                answer = f"Relevant habits in your memory: {habits_desc}. Head to the Replicate tab to apply them to this task."
            else:
                answer = f"Habi is active and learning from your workspace. Ask me to explain code, plan a feature, or run Replicate to draft solutions."
        reply.setText(f"{state.prefs.assistant_name}: {answer}")
        reply_box.setVisible(True)
        status.showMessage("Habi answered.", 4000)
        _speak_answer(answer)

    def on_mic() -> None:
        try:
            from ..voice_worker import create_listen_worker
        except ImportError:
            status.showMessage("Listening… speak now.", 4000)
            ok, heard = state.try_listen(timeout_sec=6.0)
            if ok:
                explore.setText(heard)
                explore.setFocus()
            else:
                status.showMessage(f"Microphone: {heard}", 8000)
            return
        status.showMessage("Listening… speak now.", 6000)
        mic_btn.setEnabled(False)
        worker = create_listen_worker(state.voice_coordinator(),
                                      timeout_sec=6.0, parent=root)
        _workers.append(worker)

        def _done(ok: bool, heard: str) -> None:
            mic_btn.setEnabled(True)
            if ok:
                explore.setText(heard)
                explore.setFocus()
                status.showMessage("Heard you — press Enter to ask.", 4000)
            else:
                status.showMessage(f"Microphone: {heard}", 8000)
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

    def on_showcase_log(text: str) -> None:
        low = text.lower()
        # Filter out internal/debug warnings so user sees clean, high-value progress
        if any(ign in low for ign in (
            "tts unavailable", "speech note", "live backends unavailable",
            "simulated glide", "git status note", "no trip:", "observe note"
        )):
            return

        cleaned = text
        for tag, rep in [
            ("[1_observe] Silent observation — ingesting commits.", "👁️ Step 1 · Silent Observation — Ingesting workspace commits..."),
            ("[2_voice] Voice coordination — reporting learned habits.", "🎙️ Step 2 · Voice Coordination — Reporting learned coding style..."),
            ("[3_drive] Compliant driving — gliding and typing.", "⚡ Step 3 · Autopilot Driving — Demonstrating cursor glide and keyboard typing..."),
            ("[4_estop] E-stop drill — human override correction.", "🛡️ Step 4 · Safety Override — Testing emergency stop and learning from human correction..."),
            ("MIMIR:", "Habi:"),
            ("Showcase complete.", "✓ Showcase complete — New principles are now saved in Memory."),
        ]:
            cleaned = cleaned.replace(tag, rep)

        showcase_log.setVisible(True)
        showcase_log.appendPlainText(cleaned)

    def on_showcase_scene(scene: str, explanation: str) -> None:
        clean_exp = explanation.replace("MIMIR", "Habi")
        showcase_status.setText(clean_exp)
        status.showMessage(f"Showcase: {clean_exp}", 5000)

    def on_showcase_visor(state_name: str) -> None:
        try:
            visor_face.set_state(state_name)
        except Exception:
            pass

    def on_showcase_done(success: bool) -> None:
        showcase_btn.setEnabled(True)
        showcase_btn.setText("▶ Run Live Habi Showcase")
        showcase_status.setText(
            "Showcase complete — Habi's new principles are in Memory."
            if success else "Showcase stopped early — see activity details.")
        refresh_content()
        status.showMessage(
            "Showcase complete — see Memory for new principles."
            if success else "Showcase stopped early — see log.", 8000)
        thread = _showcase.get("thread")
        if thread is not None:
            try:
                thread.quit()
                thread.wait(3000)
            except Exception:
                pass
        _showcase["thread"] = None
        _showcase["runner"] = None

    def on_showcase() -> None:
        from ..showcase import ShowcaseRunner
        showcase_btn.setEnabled(False)
        showcase_btn.setText("■ Showcase running…")
        showcase_log.clear()
        showcase_log.setVisible(True)
        showcase_status.setText("Running the 4 golden scenes…")
        runner = ShowcaseRunner(state, speak_live=True)
        thread = QtCore.QThread(root)
        thread._mimir_runner = runner  # found by MainWindow.shutdown
        runner.moveToThread(thread)
        thread.started.connect(lambda: runner.run_all())
        runner.scene_changed.connect(on_showcase_scene)
        runner.visor_state_requested.connect(on_showcase_visor)
        runner.log_message.connect(on_showcase_log)
        runner.showcase_completed.connect(on_showcase_done)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(runner.deleteLater)
        _showcase["thread"] = thread
        _showcase["runner"] = runner
        thread.start()

    watch_btn.clicked.connect(on_watch)
    explore.returnPressed.connect(on_ask)
    mic_btn.clicked.connect(on_mic)
    showcase_btn.clicked.connect(on_showcase)
    root.refresh_content = refresh_content  # called by the shell on navigate
    refresh_content()
    return root
