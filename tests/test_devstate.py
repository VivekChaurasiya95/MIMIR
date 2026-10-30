"""Tests for the Developer State Engine (plan section 3.3, step 2).

Covers partial-field updates, the verified-vs-inferred distinction,
observation reduction per event type, bus binding, and lock-guarded
concurrent updates. Qt-free and loop-free throughout.
"""

import threading
import unittest

from mimir.devstate import (
    DEVELOPER_STATE_FIELDS,
    DeveloperStateEngine,
    FieldProvenance,
)
from mimir.events import Event, EventBus, EventType, create_event


def _event(event_type: EventType, payload: dict, timestamp: float = 3000.0,
           session_id: str = "sess_dev") -> Event:
    return create_event(event_type, "test.producer", dict(payload),
                        session_id=session_id, timestamp=timestamp)


class _Collector:
    def __init__(self, bus: EventBus) -> None:
        self.events: list[Event] = []
        bus.subscribe(handler=self._record, name="test-collector")

    def _record(self, event: Event) -> None:
        self.events.append(event)

    def of_type(self, event_type: EventType) -> list[Event]:
        return [e for e in self.events if e.event_type is event_type]


class PartialUpdateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = EventBus()
        self.collector = _Collector(self.bus)
        self.engine = DeveloperStateEngine(bus=self.bus,
                                           session_id="sess_dev")

    def test_only_named_fields_change(self) -> None:
        changed = self.engine.update(
            {"focus_file": "a.py", "git_branch": "main"}, now=10.0)
        self.assertEqual(changed, ["focus_file", "git_branch"])
        self.assertEqual(self.engine.state.get("focus_file"), "a.py")
        self.assertEqual(self.engine.state.updated_at["focus_file"], 10.0)
        self.assertNotIn("activity", self.engine.state.updated_at)

    def test_unchanged_values_stay_quiet(self) -> None:
        self.engine.update({"focus_file": "a.py"}, now=10.0)
        self.assertEqual(
            len(self.collector.of_type(EventType.DEVELOPER_STATE_UPDATED)),
            1)
        changed = self.engine.update({"focus_file": "a.py"}, now=11.0)
        self.assertEqual(changed, [])
        self.assertEqual(
            len(self.collector.of_type(EventType.DEVELOPER_STATE_UPDATED)),
            1)

    def test_emission_reports_changed_fields(self) -> None:
        self.engine.update({"focus_file": "a.py", "activity": "editing"},
                           provenance=FieldProvenance.INFERRED,
                           confidence=0.7, now=10.0)
        emitted = self.collector.of_type(EventType.DEVELOPER_STATE_UPDATED)
        self.assertEqual(len(emitted), 1)
        payload = emitted[0].payload
        self.assertEqual(
            payload["changed_fields"], ["activity", "focus_file"])
        self.assertEqual(payload["values"]["focus_file"], "a.py")
        self.assertEqual(payload["provenance"]["activity"], "inferred")
        self.assertEqual(payload["confidence"]["activity"], 0.7)
        self.assertEqual(emitted[0].session_id, "sess_dev")

    def test_unknown_field_rejected(self) -> None:
        with self.assertRaises(KeyError):
            self.engine.update({"mind_reading": True})
        with self.assertRaises(KeyError):
            self.engine.state.get("mind_reading")

    def test_confidence_bounds_enforced(self) -> None:
        for bad in (0.0, -0.5, 1.5, "high"):
            with self.subTest(confidence=bad):
                with self.assertRaises(ValueError):
                    self.engine.update({"activity": "x"}, confidence=bad)  # type: ignore[arg-type]

    def test_reset_clears_state(self) -> None:
        self.engine.update({"focus_file": "a.py"})
        self.engine.reset()
        self.assertEqual(self.engine.state.get("focus_file"), "")
        self.assertEqual(self.engine.state.verified_fields(), [])


class ProvenanceTest(unittest.TestCase):
    def test_observations_verified_inferences_inferred(self) -> None:
        bus = EventBus()
        engine = DeveloperStateEngine(bus=bus)
        engine.apply_observation(_event(
            EventType.FILE_MODIFIED,
            {"path": "a.py", "root": "proj", "kind": "modified"}))
        engine.apply_inference("activity", "debugging", 0.6)
        state = engine.state
        self.assertEqual(
            state.get_provenance("focus_file"), FieldProvenance.VERIFIED)
        self.assertEqual(
            state.get_provenance("activity"), FieldProvenance.INFERRED)
        self.assertIn("focus_file", state.verified_fields())
        self.assertNotIn("activity", state.verified_fields())
        self.assertEqual(state.inferred_fields(), ["activity"])

    def test_inference_never_claims_verified(self) -> None:
        bus = EventBus()
        engine = DeveloperStateEngine(bus=bus)
        engine.apply_inference("activity", "reviewing", 0.9)
        self.assertEqual(
            engine.state.get_provenance("activity"),
            FieldProvenance.INFERRED)

    def test_to_dict_shape(self) -> None:
        bus = EventBus()
        engine = DeveloperStateEngine(bus=bus)
        engine.update({"focus_file": "a.py"}, now=5.0)
        dumped = engine.state.to_dict()
        self.assertEqual(dumped["values"]["focus_file"], "a.py")
        self.assertEqual(dumped["provenance"]["focus_file"], "verified")
        self.assertEqual(dumped["confidence"]["focus_file"], 1.0)
        self.assertEqual(dumped["updated_at"]["focus_file"], 5.0)

    def test_plan_section_33_field_coverage(self) -> None:
        for name in ("active_repo", "focus_file", "recent_ast_changes",
                     "git_branch", "cadence_summary", "activity",
                     "conversation_context", "pending_proposals"):
            self.assertIn(name, DEVELOPER_STATE_FIELDS)


class ReductionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = EventBus()
        self.engine = DeveloperStateEngine(bus=self.bus)

    def test_file_created_sets_focus_and_project(self) -> None:
        changed = self.engine.apply_observation(_event(
            EventType.FILE_CREATED,
            {"path": "proj/a.py", "root": "proj", "kind": "created"}))
        self.assertIn("focus_file", changed)
        self.assertIn("recent_edits", changed)
        self.assertIn("active_project", changed)
        self.assertEqual(self.engine.state.get("focus_file"), "proj/a.py")
        self.assertEqual(
            self.engine.state.get_provenance("focus_file"),
            FieldProvenance.VERIFIED)

    def test_file_deleted_clears_matching_focus(self) -> None:
        self.engine.apply_observation(_event(
            EventType.FILE_MODIFIED, {"path": "a.py", "kind": "modified"}))
        self.engine.apply_observation(_event(
            EventType.FILE_DELETED, {"path": "a.py", "kind": "deleted"}))
        self.assertEqual(self.engine.state.get("focus_file"), "")

    def test_file_deleted_keeps_other_focus(self) -> None:
        self.engine.apply_observation(_event(
            EventType.FILE_MODIFIED, {"path": "a.py", "kind": "modified"}))
        self.engine.apply_observation(_event(
            EventType.FILE_DELETED, {"path": "b.py", "kind": "deleted"}))
        self.assertEqual(self.engine.state.get("focus_file"), "a.py")

    def test_file_without_path_ignored(self) -> None:
        self.assertEqual(
            self.engine.apply_observation(
                _event(EventType.FILE_MODIFIED, {})), [])

    def test_ast_delta_recorded(self) -> None:
        changed = self.engine.apply_observation(_event(
            EventType.AST_DELTA,
            {"file": "m.py", "added_functions": ["b"],
             "modified_functions": ["a"], "removed_functions": [],
             "syntax_valid": True}))
        self.assertIn("recent_ast_changes", changed)
        self.assertEqual(self.engine.state.get("focus_file"), "m.py")
        entry = self.engine.state.get("recent_ast_changes")[-1]
        self.assertEqual(entry["added"], ["b"])
        self.assertEqual(entry["modified"], ["a"])

    def test_git_changed_recorded(self) -> None:
        changed = self.engine.apply_observation(_event(
            EventType.GIT_CHANGED,
            {"repo": "demo", "branch": "main", "head": "abc123",
             "dirty": True, "staged": ["x.py"], "unstaged": ["y.py"]}))
        for name in ("active_repo", "git_branch", "git_head", "git_dirty",
                     "staged_files", "unstaged_files"):
            self.assertIn(name, changed)
        self.assertEqual(self.engine.state.get("git_head"), "abc123")
        self.assertTrue(self.engine.state.get("git_dirty"))

    def test_cadence_recorded_without_raw_input(self) -> None:
        changed = self.engine.apply_observation(_event(
            EventType.CADENCE_UPDATED,
            {"window_s": 60.0, "active_s": 12.0, "idle_s": 48.0,
             "bursts": 2, "corrections": 4}))
        self.assertEqual(changed, ["cadence_summary"])
        summary = self.engine.state.get("cadence_summary")
        self.assertEqual(summary["bursts"], 2)
        self.assertNotIn("keystrokes", repr(summary))

    def test_intent_recorded_as_utterance(self) -> None:
        changed = self.engine.apply_observation(_event(
            EventType.INTENT_PARSED,
            {"intent": "status_inquiry", "text": "what did you learn?"}))
        self.assertEqual(changed, ["conversation_context"])
        context = self.engine.state.get("conversation_context")
        self.assertEqual(context["last_intent"], "status_inquiry")

    def test_proposals_accumulate(self) -> None:
        for task in ("run tests", "format code"):
            self.engine.apply_observation(_event(
                EventType.ACTION_PROPOSED,
                {"task": task, "applied_ids": ["p1"]}))
        pending = self.engine.state.get("pending_proposals")
        self.assertEqual([p["task"] for p in pending],
                         ["run tests", "format code"])

    def test_unrelated_event_ignored(self) -> None:
        self.assertEqual(
            self.engine.apply_observation(
                _event(EventType.ACTION_STARTED, {})), [])
        self.assertEqual(
            self.engine.apply_observation(
                _event(EventType.E_STOP_TRIGGERED, {})), [])


class BusIntegrationTest(unittest.TestCase):
    def test_bind_flows_end_to_end(self) -> None:
        bus = EventBus()
        collector = _Collector(bus)
        engine = DeveloperStateEngine(bus=bus)
        engine.bind()
        try:
            bus.publish(_event(
                EventType.FILE_MODIFIED,
                {"path": "a.py", "root": "proj", "kind": "modified"}))
            self.assertEqual(engine.state.get("focus_file"), "a.py")
            emitted = collector.of_type(EventType.DEVELOPER_STATE_UPDATED)
            self.assertEqual(len(emitted), 1)
            self.assertIn("focus_file",
                          emitted[0].payload["changed_fields"])
        finally:
            engine.unbind()

    def test_unbind_stops_updates(self) -> None:
        bus = EventBus()
        engine = DeveloperStateEngine(bus=bus)
        engine.bind()
        engine.unbind()
        bus.publish(_event(EventType.FILE_MODIFIED, {"path": "a.py"}))
        self.assertEqual(engine.state.get("focus_file"), "")


class ThreadSafetyTest(unittest.TestCase):
    def test_concurrent_updates_stay_consistent(self) -> None:
        bus = EventBus()
        engine = DeveloperStateEngine(bus=bus)
        errors: list[Exception] = []

        def hammer(index: int) -> None:
            try:
                for round_no in range(25):
                    # Inferences own "activity"; observations own files.
                    engine.apply_inference(
                        "activity", f"task-{index}-{round_no}", 0.5)
                    engine.apply_observation(_event(
                        EventType.FILE_MODIFIED,
                        {"path": f"w{index}.py", "kind": "modified"}))
            except Exception as exc:  # pragma: no cover - fail via errors
                errors.append(exc)

        threads = [threading.Thread(target=hammer, args=(i,))
                   for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30.0)
        self.assertEqual(errors, [])
        self.assertIn("focus_file", engine.state.verified_fields())
        self.assertIn("activity", engine.state.inferred_fields())


if __name__ == "__main__":
    unittest.main()
