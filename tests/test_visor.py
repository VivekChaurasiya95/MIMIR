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
                self.assertEqual(pill.toolTip(), "Habi: Driving")
                controller.toggle_rest()
                app.processEvents()
                self.assertIn("#555555", pill._dot.styleSheet())
            finally:
                pill.close()
                pill.deleteLater()
        finally:
            app.processEvents()

    def test_widget_renders_personified_face(self):
        name, _, QtWidgets = _qt_bindings()
        controller = VisorController()
        if name is None:
            with self.assertRaisesRegex(VisorError, "PyQt6"):
                create_visor_widget(controller)
            return
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        try:
            pill = create_visor_widget(controller)
            try:
                # Face paint state tracks the controller; the legacy dot
                # stays as a hidden shim carrying the spec color.
                self.assertEqual(pill._color, controller.color)
                self.assertEqual(pill._state, controller.state)
                self.assertIn(controller.color, pill._dot.styleSheet())
                controller.set_state(VisorState.LISTENING)
                app.processEvents()
                self.assertEqual(pill._color, "#10B981")
                self.assertEqual(pill._state, VisorState.LISTENING)
                self.assertIn("#10B981", pill._dot.styleSheet())
                self.assertEqual(pill.toolTip(), "Habi: Listening")
            finally:
                pill.close()
                pill.deleteLater()
        finally:
            app.processEvents()

    def test_widget_face_background_is_light(self):
        name, _, QtWidgets = _qt_bindings()
        if name is None:
            self.skipTest("PyQt6/PySide6 not installed")
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        try:
            pill = create_visor_widget(VisorController())
            try:
                pill.show()
                app.processEvents()
                shot = pill.grab().toImage()
                w, h = shot.width(), shot.height()
                for x, y in ((w // 4, h // 2), (3 * w // 4, h // 2),
                             (w // 2, h // 5)):
                    c = shot.pixelColor(x, y)
                    self.assertGreater(c.red(), 200, (x, y))
                    self.assertGreater(c.green(), 200, (x, y))
                    self.assertGreater(c.blue(), 200, (x, y))
            finally:
                pill.close()
                pill.deleteLater()
        finally:
            app.processEvents()

    def test_widget_face_is_oval(self):
        name, _, QtWidgets = _qt_bindings()
        if name is None:
            self.skipTest("PyQt6/PySide6 not installed")
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        try:
            pill = create_visor_widget(VisorController())
            try:
                pill.show()
                app.processEvents()
                shot = pill.grab().toImage()
                w, h = shot.width(), shot.height()
                # Inside a rounded rect but outside the inscribed ellipse.
                edge = shot.pixelColor(int(w * 0.16), int(h * 0.125))
                self.assertLess(edge.alpha(), 30)
                # Top-center stays solid face.
                top = shot.pixelColor(w // 2, int(h * 0.125))
                self.assertGreater(top.alpha(), 200)
                self.assertGreater(top.red(), 200)
            finally:
                pill.close()
                pill.deleteLater()
        finally:
            app.processEvents()

    def test_visor_floats_and_drags(self):
        name, QtCore, QtWidgets = _qt_bindings()
        if name is None:
            self.skipTest("PyQt6/PySide6 not installed")
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        try:
            pill = create_visor_widget(VisorController())
            try:
                pill.show()
                app.processEvents()
                pill._motion_timer.stop()
                # Idle float is alive but bounded.
                self.assertEqual(pill._bob_dy(0.0), 0)
                self.assertEqual(pill._bob_dy(1.5), 4)
                self.assertEqual(pill._bob_dy(-1.5), -4)
                # Drag moves the pill by the cursor delta.
                class _Press:
                    def __init__(self, x, y):
                        self._p = QtCore.QPointF(float(x), float(y))

                    def globalPosition(self):
                        return self._p

                base = pill.pos()
                pill.mousePressEvent(
                    _Press(base.x() + 20, base.y() + 20))
                self.assertTrue(pill._dragging)
                pill.mouseMoveEvent(
                    _Press(base.x() + 60, base.y() + 40))
                self.assertEqual(pill.pos().x(), base.x() + 40)
                self.assertEqual(pill.pos().y(), base.y() + 20)
                pill.mouseReleaseEvent(object())
                self.assertFalse(pill._dragging)
                self.assertEqual(pill._anchor, pill.pos())
            finally:
                pill.close()
                pill.deleteLater()
        finally:
            app.processEvents()

    def test_visor_auto_roam_permit_and_glide(self):
        name, QtCore, QtWidgets = _qt_bindings()
        if name is None:
            self.skipTest("PyQt6/PySide6 not installed")
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        try:
            pill = create_visor_widget(VisorController())
            try:
                pill.show()
                app.processEvents()
                pill._motion_timer.stop()
                self.assertFalse(pill._auto_roam)
                self.assertIsNone(pill._move_target)
                pill.set_auto_roam(True)
                self.assertTrue(pill._auto_roam)
                pill.set_auto_roam(False)
                self.assertFalse(pill._auto_roam)
                # glide_to walks the anchor to the target over ticks.
                pill._anchor = QtCore.QPoint(pill.pos())
                target = QtCore.QPoint(pill._anchor.x() + 30,
                                       pill._anchor.y())
                pill.glide_to(target.x(), target.y())
                for _ in range(15):
                    pill._tick_motion()
                self.assertEqual(pill._anchor, target)
                self.assertIsNone(pill._move_target)
            finally:
                pill.close()
                pill.deleteLater()
        finally:
            app.processEvents()

    def test_visor_follow_mouse(self):
        name, QtCore, QtWidgets = _qt_bindings()
        if name is None:
            self.skipTest("PyQt6/PySide6 not installed")
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        try:
            pill = create_visor_widget(VisorController())
            try:
                pill.show()
                app.processEvents()
                pill._motion_timer.stop()
                self.assertFalse(pill._follow_mouse)
                pill.set_follow_mouse(True)
                self.assertTrue(pill._follow_mouse)
                # Ticking motion should gracefully execute without raising
                pill._tick_motion()
                pill.set_follow_mouse(False)
                self.assertFalse(pill._follow_mouse)
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
