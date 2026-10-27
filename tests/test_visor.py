"""Tests for the expressive visor avatar (Days 8-9).

The state machine is pure headless logic. Widget tests run Qt offscreen
when available; when no Qt binding exists they assert the install-hint
error instead, so both paths stay green on any machine.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest

from mimir.ui.visor import (
    VISOR_COLORS,
    VisorController,
    VisorError,
    VisorState,
    create_visor_widget,
)


def _qt_bindings():
    try:
        from PyQt6 import QtCore, QtWidgets  # noqa: F401

        return ("PyQt6", QtCore, QtWidgets)
    except ImportError:
        pass
    try:
        from PySide6 import QtCore, QtWidgets  # noqa: F401

        return ("PySide6", QtCore, QtWidgets)
    except ImportError:
        pass
    return (None, None, None)


class StateMachineTest(unittest.TestCase):
    def test_defaults_to_resting(self):
        visor = VisorController()
        self.assertEqual(visor.state, VisorState.RESTING)
        self.assertEqual(visor.color, "#555555")
        self.assertEqual(visor.label, "Resting")

    def test_spec_colors_for_all_states(self):
        expected = {
            VisorState.RESTING: "#555555",
            VisorState.OBSERVING: "#3B82F6",
            VisorState.LISTENING: "#10B981",
            VisorState.THINKING: "#8B5CF6",
            VisorState.DRIVING: "#F59E0B",
            VisorState.ALERT: "#EF4444",
        }
        self.assertEqual(VISOR_COLORS, expected)
        visor = VisorController()
        for state, color in expected.items():
            visor.set_state(state)
            self.assertEqual(visor.color, color)

    def test_accepts_state_names_and_rejects_unknown(self):
        visor = VisorController(initial="driving")
        self.assertEqual(visor.state, VisorState.DRIVING)
        visor.set_state(" ALERT ")
        self.assertEqual(visor.state, VisorState.ALERT)
        with self.assertRaises(ValueError):
            visor.set_state("dancing")
        with self.assertRaises(ValueError):
            VisorController(initial="dancing")
        with self.assertRaises(ValueError):
            VisorController(on_change=42)

    def test_notifies_once_and_skips_noops(self):
        events: list[tuple[str, str]] = []
        visor = VisorController(
            on_change=lambda old, new: events.append((old.value, new.value))
        )
        visor.set_state(VisorState.DRIVING)
        visor.set_state(VisorState.DRIVING)
        self.assertEqual(events, [("resting", "driving")])

    def test_hook_failure_never_breaks_machine(self):
        def exploding(old, new):
            raise RuntimeError("ui glitch")

        visor = VisorController(on_change=exploding)
        visor.set_state(VisorState.ALERT)
        self.assertEqual(visor.state, VisorState.ALERT)

    def test_toggle_rest_cycle(self):
        visor = VisorController()
        self.assertEqual(visor.toggle_rest(), VisorState.OBSERVING)
        self.assertEqual(visor.toggle_rest(), VisorState.RESTING)
        visor.set_state(VisorState.DRIVING)
        self.assertEqual(visor.toggle_rest(), VisorState.RESTING)


class WidgetTest(unittest.TestCase):
    def test_widget_follows_controller_or_hint_error(self):
        name, QtCore, QtWidgets = _qt_bindings()
        controller = VisorController()
        if name is None:
            with self.assertRaisesRegex(VisorError, "PyQt6"):
                create_visor_widget(controller)
            return
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        try:
            pill = create_visor_widget(controller)
            try:
                flags = pill.windowFlags()
                self.assertTrue(bool(flags & QtCore.Qt.WindowType.FramelessWindowHint))
                self.assertTrue(
                    bool(flags & QtCore.Qt.WindowType.WindowStaysOnTopHint)
                )
                controller.set_state(VisorState.DRIVING)
                app.processEvents()
                self.assertIn("#F59E0B", pill._dot.styleSheet())
                self.assertEqual(pill.toolTip(), "MIMIR: Driving")
                controller.toggle_rest()
                app.processEvents()
                self.assertIn("#555555", pill._dot.styleSheet())
            finally:
                pill.close()
                pill.deleteLater()
        finally:
            app.processEvents()

    def test_widget_rejects_bad_inputs(self):
        with self.assertRaises(ValueError):
            create_visor_widget("not-a-controller")
        with self.assertRaises(ValueError):
            create_visor_widget(VisorController(), hotkey="  ")


if __name__ == "__main__":
    unittest.main()
