"""Replicate screen: style synthesis + recent tasks (spec 5.4)."""

from __future__ import annotations

from typing import Any

from ..presenters import format_replication_report, rel_time_ago
from ..state import AppState, get_llm_fn
from ._layout import (
    ClickableCard,
    actions_row,
    badge,
    card,
    functionality_banner,
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
        "Describe what you want, and Habitous will use your learned principles."))

    layout.addWidget(functionality_banner(
        "⧉",
        "Authentic Style Synthesis",
        "Describe any task, feature implementation, or refactor. Habitous automatically selects your "
        "highest-confidence behavioral principles and drafts code that matches your authentic conventions.",
        pills=[("Style Matching", "confirmed"), ("Principle Injection", "confirmed"), ("Contrastive Guardrails", "learning")],
        icon_cls="icon_box_green",
    ))

    prompt_box, prompt_lay = card()
    prompt_head = QtWidgets.QHBoxLayout()
    prompt_label = QtWidgets.QLabel("What would you like Habi to create or change?")
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
    conf_spin.setDecimals(2)
    conf_spin.setValue(state.prefs.default_min_confidence)
    conf_spin.setMaximumWidth(80)
    conf_spin.setToolTip(
        "Minimum principle confidence for this draft "
        "(saved as the Model default)")
    generate_btn = QtWidgets.QPushButton("Generate →")
    generate_btn.setObjectName("generate_btn")
    target.addWidget(QtWidgets.QLabel("Project"))
    target.addWidget(project_combo, 1)
    target.addWidget(QtWidgets.QLabel("Use principles"))
    target.addWidget(principles_combo)
    target.addWidget(QtWidgets.QLabel("Min conf"))
    target.addWidget(conf_spin)
    target.addStretch(1)
    target.addWidget(generate_btn)
    prompt_lay.addLayout(target)
    engine_note = QtWidgets.QLabel("")
    engine_note.setObjectName("muted")
    engine_note.setWordWrap(True)
    prompt_lay.addWidget(engine_note)
    layout.addWidget(prompt_box)

    verdict = QtWidgets.QLabel("")
    verdict.setObjectName("muted")
    verdict.setVisible(False)
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
    drive_btn = QtWidgets.QPushButton("⚡ Apply with Habi")
    drive_btn.setObjectName("soft")
    drive_btn.setEnabled(False)
    drive_btn.setToolTip(
        "Habi glides the cursor and types this solution with human "
        "cadence. ESC or a mouse nudge stops it instantly.")
    out_lay.addLayout(actions_row(copy_btn, drive_btn))
    out_box.setVisible(False)
    layout.addWidget(out_box)

    feed_head = QtWidgets.QHBoxLayout()
    feed_head.addWidget(overline("Recent tasks"))
    feed_head.addStretch(1)
    view_all = QtWidgets.QPushButton("View all →")
    view_all.setObjectName("chip")
    feed_head.addWidget(view_all)
    layout.addLayout(feed_head)
    feed = QtWidgets.QVBoxLayout()
    feed.setSpacing(10)
    layout.addLayout(feed)
    layout.addStretch(1)

    last_solution = {"text": ""}
    show_all = {"on": False}
    _workers: list[Any] = []

    def sync_engine() -> None:
        try:
            live = get_llm_fn(state.prefs) is not None
        except Exception:
            live = False
        if live:
            provider = (state.prefs.model_provider or "").strip() or "live"
            model = (state.prefs.model_name or "").strip()
            engine_note.setText(
                f"Engine: live synthesis via {provider}"
                + (f" ({model})" if model else "")
                + " — falls back to the offline scaffold if unreachable.")
        else:
            engine_note.setText(
                "Engine: offline heuristic scaffold — set a provider in "
                "Settings → Model for live synthesis.")

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
        blocked = conf_spin.blockSignals(True)
        conf_spin.setValue(state.prefs.default_min_confidence)
        conf_spin.blockSignals(blocked)

    def on_conf_changed(value: float) -> None:
        state.prefs.default_min_confidence = float(value)
        try:
            state.prefs.save()
        except OSError:
            pass

    def open_task(task: dict) -> None:
        """Reload a feed entry into the prompt for review or re-run."""
        task_edit.setPlainText(task.get("title", ""))
        project = (task.get("project") or "").strip()
        if project:
            index = project_combo.findText(project)
            if index >= 0:
                project_combo.setCurrentIndex(index)
            else:
                project_combo.setEditText(project)
        task_edit.setFocus()
        status.showMessage("Task loaded — press Generate to re-run.", 4000)

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
        verdict.setVisible(bool(text))

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
            empty_box, empty_lay = card()
            empty_title = QtWidgets.QLabel("No tasks yet")
            empty_title.setObjectName("h2")
            empty_hint = QtWidgets.QLabel(
                "Describe a task above and press Generate — your styled "
                "drafts and their history will appear here.")
            empty_hint.setObjectName("muted")
            empty_hint.setWordWrap(True)
            focus_btn = QtWidgets.QPushButton("Describe your first task")
            focus_btn.setObjectName("ink")
            focus_btn.clicked.connect(
                lambda: (task_edit.setFocus(),
                         task_edit.selectAll()))
            empty_lay.addWidget(empty_title)
            empty_lay.addWidget(empty_hint)
            empty_lay.addLayout(actions_row(focus_btn))
            feed.addWidget(empty_box)
            return
        shown = tasks if show_all["on"] else tasks[:FEED_PREVIEW]
        for task in shown:
            done = task.get("verdict") == "Completed"
            row = ClickableCard("task_row",
                                on_click=lambda t=task: open_task(t))
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

    _gen_worker: list[Any] = []  # single-flight inference worker

    def _snapshot_principles() -> list[dict]:
        try:
            return [p.to_dict() for p in state.list_principles()]
        except Exception:
            return []

    def _llm_prefs_snapshot() -> dict:
        prefs = state.prefs
        return {
            "model_provider": getattr(prefs, "model_provider", "offline"),
            "model_name": getattr(prefs, "model_name", ""),
            "model_endpoint": getattr(prefs, "model_endpoint", ""),
            "api_key": getattr(prefs, "api_key", ""),
            "contrast_baseline": getattr(prefs, "contrast_baseline", True),
        }

    def _release_gen_worker(worker: Any) -> None:
        generate_btn.setEnabled(True)
        try:
            _gen_worker.remove(worker)
        except ValueError:
            pass
        try:
            worker.deleteLater()
        except Exception:
            pass

    def _render_generate_error(exc: BaseException) -> None:
        status.showMessage(f"Replicate failed: {exc}", 8000)
        output.setPlainText(f"Replicate failed: {exc}")
        set_verdict("", "muted")
        copy_btn.setEnabled(False)
        drive_btn.setEnabled(False)

    def on_generate() -> None:
        # Computational isolation (plan section 6): synthesis runs on a
        # ReplicateWorker thread with its own store — the UI thread only
        # renders the result. Single-flight: a second click while busy
        # is ignored instead of stacking LLM calls.
        if _gen_worker:
            status.showMessage("Already generating — one moment.", 4000)
            return
        task = task_edit.toPlainText().strip()
        if not task:
            status.showMessage("Describe a task first.", 4000)
            return
        top_k = TOP_K_OPTIONS[max(0, principles_combo.currentIndex())]
        scope = project_combo.currentText().strip() or None
        from ..replicate_worker import create_replicate_worker
        generate_btn.setEnabled(False)
        set_verdict("Drafting your solution…", "muted")
        status.showMessage("Drafting your solution…", 4000)
        worker = create_replicate_worker(
            task, _snapshot_principles(),
            target_scope=scope,
            min_confidence=conf_spin.value(),
            top_k=top_k,
            llm_prefs=_llm_prefs_snapshot(),
            parent=root,
        )
        _gen_worker.append(worker)

        def _finished(payload: object) -> None:
            _release_gen_worker(worker)
            data = payload if isinstance(payload, dict) else {}
            result = data.get("result")
            fell_back = bool(data.get("fell_back"))
            live_error = str(data.get("live_error") or "")
            if result is None:
                _render_generate_error(RuntimeError("empty synthesis"))
                return
            if fell_back and live_error:
                status.showMessage(
                    f"Live provider failed ({live_error}); "
                    "used offline scaffold.", 8000)
            project = project_combo.currentText().strip()
            try:
                _render_result(result, fell_back, task, project)
            except Exception as render_exc:
                # Never let a render bug escape a Qt slot (native abort).
                _render_generate_error(render_exc)

        def _failed(message: str) -> None:
            _release_gen_worker(worker)
            _render_generate_error(RuntimeError(message))

        def _cancelled() -> None:
            _release_gen_worker(worker)
            set_verdict("", "muted")
            status.showMessage("Generation cancelled.", 4000)

        worker.replicate_finished.connect(_finished)
        worker.replicate_error.connect(_failed)
        worker.replicate_cancelled.connect(_cancelled)
        worker.start()

    def _render_result(result: Any, fell_back: bool, task: str,
                       project: str) -> None:
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
        if fell_back:
            set_verdict((verdict.text() + " (offline fallback — live "
                         "provider unreachable.)").strip(), "muted")
        output.setPlainText(report)
        out_box.setVisible(True)
        last_solution["text"] = result.replicated_solution
        copy_btn.setEnabled(True)
        drive_btn.setEnabled(True)
        compliant = bool(result.compliance_report.get("compliant", True))
        state.prefs = state.prefs.record_task(
            task[:60], project,
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

    def _offer_invariant(reason: str) -> None:
        """Offer instant negative-invariant creation after an E-stop."""
        text, ok = QtWidgets.QInputDialog.getText(
            root.window(), "Stopped by E-stop",
            f"Cobot stopped ({reason}).\nDescribe what to avoid, to save "
            "it as a memory rule (Cancel to skip):")
        invariant = (text or "").strip()
        if not ok or not invariant:
            return
        from mimir.principles import (
            BehavioralPrinciple,
            ContextTrigger,
            EpistemicStatus,
        )
        import time
        principle = BehavioralPrinciple(
            id=f"bp_drive_forbid_{int(time.time())}",
            name="Drive correction",
            category="workflow",
            context_trigger=ContextTrigger(
                scope="Cobot driving", language_agnostic=True),
            latent_invariant=invariant,
            anti_patterns=("Repeating the interrupted drive",),
            epistemic_status=EpistemicStatus.new(0.70, supporting=1),
            inspectable_traces=("drive_estop_correction",),
        )
        try:
            state.store.save(principle)
        except Exception as exc:
            status.showMessage(f"Could not save rule: {exc}", 8000)
            return
        status.showMessage("Saved as a Behavioral Memory rule.", 5000)

    def on_drive() -> None:
        solution = last_solution["text"].strip()
        if not solution:
            return
        from mimir.actuator import DesktopActuator
        from mimir.desktop import TargetSpec
        from ..actuate_worker import (
            create_drive_worker,
            format_countdown,
            list_drive_targets,
        )
        safety = getattr(state, "safety", None)
        registry = getattr(state, "action_registry", None)
        if safety is not None:
            try:
                latched = safety.estopped
            except Exception:
                latched = False
            if latched:
                status.showMessage(
                    "Safety stop is active — reauthorize before driving. "
                    "Run any drive to be offered a resume.", 8000)
                _offer_resume()
                return
        drive_btn.setEnabled(False)
        copy_btn.setEnabled(False)
        dialog = QtWidgets.QDialog(root.window())
        dialog.setWindowTitle("Cobot drive")
        dialog.setModal(False)
        dialog_lay = QtWidgets.QVBoxLayout(dialog)
        # Target selection (plan section 5): enumerate live windows and
        # pre-select the foreground app, so the drive runs inside a
        # validated focus session instead of typing at whatever happens
        # to have focus. Headless / pywin32-less hosts fall back to the
        # legacy caller-owns-focus behavior with an honest note.
        targets, foreground = list_drive_targets()
        target_combo = QtWidgets.QComboBox()
        target_combo.addItem("Type where focus already is (no validation)",
                             None)
        for handle, label in targets:
            target_combo.addItem(f"{label}  [#{handle}]", handle)
        if foreground is not None:
            index = target_combo.findData(foreground)
            if index >= 0:
                target_combo.setCurrentIndex(index)
        if targets:
            dialog_lay.addWidget(QtWidgets.QLabel("Drive target:"))
            dialog_lay.addWidget(target_combo)
        else:
            note = QtWidgets.QLabel(
                "Target detection unavailable on this host — Habi will "
                "type at the current focus.")
            note.setWordWrap(True)
            note.setObjectName("muted")
            dialog_lay.addWidget(note)
        countdown_label = QtWidgets.QLabel(format_countdown(3))
        countdown_label.setWordWrap(True)
        dialog_lay.addWidget(countdown_label)
        cancel_btn = QtWidgets.QPushButton("Cancel drive")
        cancel_btn.setObjectName("soft")
        dialog_lay.addWidget(cancel_btn)

        def _selected_target() -> Any:
            handle = target_combo.currentData()
            if not targets or handle is None:
                return None
            try:
                return TargetSpec(handle=int(handle))
            except (TypeError, ValueError):
                return None

        target = _selected_target()
        target_label = (
            target_combo.currentText() if target is not None else "")
        spec = None
        if registry is not None:
            try:
                spec = registry.propose(
                    "drive_text",
                    target=target_label[:200],
                    reason="replicate-tab compliant drive",
                    supporting_observations=(
                        f"solution_chars={len(solution)}",),
                    # The open dialog + countdown IS the explicit user
                    # gesture; no extra permission is required beyond a
                    # bound action id and a nominal safety latch.
                    required_permissions=(),
                )
            except Exception:
                spec = None
        action_id = spec.action_id if spec is not None else ""
        worker = create_drive_worker(
            solution, parent=root, target=target,
            authorized=True, verify_cursor=target is not None,
            safety=safety,
            actuator_factory=lambda: DesktopActuator(
                safety=safety, event_bus=state.event_bus),
        )
        _workers.append(worker)

        def _settle(outcome: str, reason: str = "") -> None:
            if registry is None or not action_id:
                return
            try:
                if outcome == "complete":
                    registry.complete(action_id)
                elif outcome == "fail":
                    registry.fail(action_id, reason)
                else:
                    registry.cancel(action_id, reason)
            except Exception:
                pass

        def _release() -> None:
            drive_btn.setEnabled(True)
            copy_btn.setEnabled(True)
            try:
                _workers.remove(worker)
            except ValueError:
                pass
            try:
                worker.deleteLater()
            except Exception:
                pass
            try:
                dialog.close()
            except Exception:
                pass

        def _tick(left: int) -> None:
            countdown_label.setText(format_countdown(left))
            status.showMessage(format_countdown(left), 1500)

        def _started() -> None:
            countdown_label.setText(
                "Driving — ESC or a mouse nudge stops me instantly.")
            try:
                state.visor.set_state("driving")
            except Exception:
                pass
            # The dialog countdown is the explicit GUI confirmation:
            # authorize + mark executing for the audit trail. A denial
            # (latched E-stop) aborts before any input lands — and the
            # actuator-level gates stay fail-closed regardless of this
            # bookkeeping race with the worker thread.
            if registry is not None and action_id:
                try:
                    outcome = registry.authorize(
                        action_id,
                        authorization_type="explicit_gui_confirmation",
                        granted_permissions=("gui-confirm",),
                    )
                except Exception:
                    outcome = None
                if outcome is None or not outcome.ok:
                    try:
                        worker.cancel()
                    except Exception:
                        pass
                    return
                try:
                    registry.mark_executing(action_id)
                except Exception:
                    pass

        def _finished(report: dict) -> None:
            _settle("complete")
            _release()
            try:
                state.visor.set_state("resting")
            except Exception:
                pass
            status.showMessage(
                f"Cobot typed {report.get('typed_chars', 0)} char(s).", 5000)

        def _interrupted(reason: str) -> None:
            if "countdown" in reason.lower():
                _settle("cancel", reason)
            else:
                _settle("fail", reason)
            _release()
            try:
                state.visor.set_state("alert")
            except Exception:
                pass
            status.showMessage(f"Cobot stopped: {reason}", 8000)
            _offer_invariant(reason)
            _offer_resume()

        def _error(message: str) -> None:
            _settle("fail", message)
            _release()
            status.showMessage(
                f"Cobot drive unavailable: {message}", 8000)
            _offer_resume()

        cancel_btn.clicked.connect(worker.cancel)
        worker.countdown_tick.connect(_tick)
        worker.drive_started.connect(_started)
        worker.drive_finished.connect(_finished)
        worker.drive_interrupted.connect(_interrupted)
        worker.drive_error.connect(_error)
        dialog.show()
        worker.start()

    def _offer_resume() -> None:
        """Offer deliberate reauthorization after a safety stop."""
        safety = getattr(state, "safety", None)
        if safety is None:
            return
        try:
            latched = safety.estopped
        except Exception:
            return
        if not latched:
            return
        answer = QtWidgets.QMessageBox.question(
            root.window(), "Safety stop active",
            "Habi is E-stopped: no drive or approved action can run.\n\n"
            "Reauthorize and resume normal operation?",
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No)
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        try:
            resumed = safety.reset(
                authorization="desktop-user: drive-dialog reauthorization",
                actor="desktop-user",
            )
        except Exception as exc:
            status.showMessage(f"Resume refused: {exc}", 8000)
            return
        if resumed:
            status.showMessage("Safety stop cleared — Habi is ready.", 5000)

    def on_mic() -> None:
        try:
            from ..voice_worker import create_listen_worker
        except ImportError:
            status.showMessage("Listening… speak now.", 4000)
            ok, heard = state.try_listen(timeout_sec=8.0)
            if ok:
                task_edit.setPlainText(
                    (task_edit.toPlainText() + " " + heard).strip())
                task_edit.setFocus()
            else:
                status.showMessage(f"Microphone: {heard}", 8000)
            return
        status.showMessage("Listening… speak now.", 9000)
        mic_btn.setEnabled(False)
        worker = create_listen_worker(state.voice_coordinator(),
                                      timeout_sec=8.0, parent=root)
        _workers.append(worker)

        def _done(ok: bool, heard: str) -> None:
            mic_btn.setEnabled(True)
            if ok:
                task_edit.setPlainText(
                    (task_edit.toPlainText() + " " + heard).strip())
                task_edit.setFocus()
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

    conf_spin.valueChanged.connect(on_conf_changed)
    generate_btn.clicked.connect(on_generate)
    copy_btn.clicked.connect(on_copy)
    drive_btn.clicked.connect(on_drive)
    mic_btn.clicked.connect(on_mic)
    view_all.clicked.connect(
        lambda: (show_all.update(on=not show_all["on"]), render_feed()))

    def refresh_content() -> None:
        sync_project_items()
        sync_defaults()
        sync_engine()
        render_feed()

    root.refresh_content = refresh_content
    refresh_content()
    return root
