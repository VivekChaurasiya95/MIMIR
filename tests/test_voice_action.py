"""Tests for voice-to-action (plan section 4, step 3).

Covers the Action Registry (pending/authorize/expiry/permissions), the
structured intent parser (APPROVE_PENDING_ACTION binding, ambiguity and
expiry clarification), the background TranscriptionWorker, and the
Qt-free VoiceActionService end to end (bus events + TTS feedback).

All tests run headless with injected fakes; no microphone, Whisper
model, network TTS, Qt, or event loop is required.
"""

import queue
import sys
import threading
import time
import unittest
from typing import Optional

from mimir.actions import (
    APPROVE_PENDING_ACTION,
    ActionRegistry,
    ActionStatus,
    RegistryError,
)
from mimir.devstate import DeveloperStateEngine
from mimir.events import Event, EventBus, EventType
from mimir.voice import VoiceCoordinator
from mimir.voice_action import (
    STRUCTURED_INTENTS,
    StructuredIntent,
    TranscriptionWorker,
    VoiceActionError,
    VoiceActionService,
    parse_structured_intent,
)


def _voice() -> VoiceCoordinator:
    return VoiceCoordinator(
        stt_backend=lambda timeout: None, tts_backend=lambda text: None
    )


def _status(
    registry: ActionRegistry,
    action_id: str,
    now: Optional[float] = None,
) -> ActionStatus:
    spec = registry.get(action_id, now=now)
    assert spec is not None
    return spec.status


class _Collector:
    def __init__(self, bus: EventBus) -> None:
        self.events: list[Event] = []
        bus.subscribe(handler=self._record, name="test-collector")

    def _record(self, event: Event) -> None:
        self.events.append(event)

    def of_type(self, event_type: EventType) -> list[Event]:
        return [e for e in self.events if e.event_type is event_type]


def _service(
    bus: EventBus,
    registry: ActionRegistry,
    said: list[str],
    **kwargs,
):
    defaults = {"speak_fn": said.append, "speak_async": False}
    defaults.update(kwargs)
    return VoiceActionService(
        _voice(), registry, bus=bus, session_id="sess_va", **defaults
    )


class RegistryLifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = EventBus()
        self.collector = _Collector(self.bus)
        self.registry = ActionRegistry(bus=self.bus, session_id="sess_va")

    def test_propose_defaults_and_emission(self) -> None:
        spec = self.registry.propose(
            "push code", target="github", now=1000.0
        )
        self.assertTrue(spec.action_id.startswith("action_"))
        self.assertEqual(spec.status, ActionStatus.PENDING)
        self.assertEqual(spec.required_permissions, ("voice-confirm",))
        self.assertEqual(spec.expires_at, 1000.0 + 300.0)
        proposed = self.collector.of_type(EventType.ACTION_PROPOSED)
        self.assertEqual(len(proposed), 1)
        self.assertEqual(proposed[0].payload["action_id"], spec.action_id)
        self.assertEqual(proposed[0].session_id, "sess_va")

    def test_propose_validation(self) -> None:
        with self.assertRaises(RegistryError):
            self.registry.propose("   ")
        with self.assertRaises(RegistryError):
            self.registry.propose("push", confidence=0.0)
        with self.assertRaises(RegistryError):
            self.registry.propose("push", confidence=2.0)
        with self.assertRaises(RegistryError):
            self.registry.propose("push", ttl_s=0.0)
        with self.assertRaises(RegistryError):
            ActionRegistry(default_ttl_s=-1.0)

    def test_get_unknown_and_bad_id(self) -> None:
        self.assertIsNone(self.registry.get("action_missing"))
        with self.assertRaises(RegistryError):
            self.registry.get("  ")

    def test_list_pending_oldest_first(self) -> None:
        first = self.registry.propose("a", now=1000.0)
        second = self.registry.propose("b", now=1001.0)
        pending = self.registry.list_pending(now=1002.0)
        self.assertEqual(
            [s.action_id for s in pending],
            [first.action_id, second.action_id],
        )

    def test_authorize_ok_binds_specific_action(self) -> None:
        first = self.registry.propose("push code", now=1000.0)
        second = self.registry.propose("run tests", now=1000.0)
        outcome = self.registry.authorize(
            second.action_id,
            granted_permissions=("voice-confirm",),
            now=1001.0,
        )
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.action_id, second.action_id)
        self.assertEqual(
            _status(self.registry, first.action_id, now=1001.0),
            ActionStatus.PENDING,
        )
        self.assertEqual(
            _status(self.registry, second.action_id, now=1001.0),
            ActionStatus.AUTHORIZED,
        )
        authorized = self.collector.of_type(EventType.ACTION_AUTHORIZED)
        self.assertEqual(len(authorized), 1)
        self.assertEqual(
            authorized[0].payload["authorization_type"],
            "explicit_voice_confirmation",
        )

    def test_authorize_unknown(self) -> None:
        outcome = self.registry.authorize(
            "action_missing", granted_permissions=("voice-confirm",)
        )
        self.assertFalse(outcome.ok)
        self.assertIn("unknown", outcome.reason)

    def test_authorize_missing_permissions_denies_without_mutating(
        self,
    ) -> None:
        spec = self.registry.propose(
            "type password",
            required_permissions=("voice-confirm", "desktop-control"),
            now=1000.0,
        )
        outcome = self.registry.authorize(
            spec.action_id,
            granted_permissions=("voice-confirm",),
            now=1001.0,
        )
        self.assertFalse(outcome.ok)
        self.assertIn("desktop-control", outcome.reason)
        # Still pending: the caller can clarify instead of losing the entry.
        self.assertEqual(
            _status(self.registry, spec.action_id, now=1001.0),
            ActionStatus.PENDING,
        )
        retry = self.registry.authorize(
            spec.action_id,
            granted_permissions=("voice-confirm", "desktop-control"),
            now=1002.0,
        )
        self.assertTrue(retry.ok)

    def test_expired_cannot_be_approved_again(self) -> None:
        spec = self.registry.propose("push code", ttl_s=60.0, now=1000.0)
        outcome = self.registry.authorize(
            spec.action_id,
            granted_permissions=("voice-confirm",),
            now=2000.0,
        )
        self.assertFalse(outcome.ok)
        self.assertIn("expired", outcome.reason)
        self.assertEqual(
            _status(self.registry, spec.action_id), ActionStatus.EXPIRED
        )
        again = self.registry.authorize(
            spec.action_id,
            granted_permissions=("voice-confirm",),
            now=2001.0,
        )
        self.assertFalse(again.ok)
        self.assertIn("expired", again.reason)
        self.assertEqual(self.registry.list_pending(now=2001.0), [])

    def test_executed_cannot_be_approved_again(self) -> None:
        spec = self.registry.propose("push code", now=1000.0)
        first = self.registry.authorize(
            spec.action_id, granted_permissions=("voice-confirm",),
            now=1001.0,
        )
        self.assertTrue(first.ok)
        # Double authorization is rejected (status + one-shot claim).
        second = self.registry.authorize(
            spec.action_id, granted_permissions=("voice-confirm",),
            now=1002.0,
        )
        self.assertFalse(second.ok)
        self.assertIn("already", second.reason)
        self.assertTrue(self.registry.mark_executing(spec.action_id))
        self.assertTrue(self.registry.complete(spec.action_id))
        third = self.registry.authorize(
            spec.action_id, granted_permissions=("voice-confirm",),
            now=1003.0,
        )
        self.assertFalse(third.ok)
        self.assertIn("completed", third.reason)

    def test_terminal_transitions(self) -> None:
        spec = self.registry.propose("push code", now=1000.0)
        self.assertFalse(self.registry.complete("action_missing"))
        self.assertFalse(self.registry.mark_executing(spec.action_id))
        self.registry.authorize(
            spec.action_id, granted_permissions=("voice-confirm",),
            now=1001.0,
        )
        self.assertTrue(self.registry.mark_executing(spec.action_id))
        self.assertTrue(self.registry.complete(spec.action_id))
        # Terminal: no further transitions.
        self.assertFalse(self.registry.complete(spec.action_id))
        self.assertFalse(self.registry.fail(spec.action_id))
        self.assertFalse(self.registry.cancel(spec.action_id))
        failed = self.registry.propose("other", now=1000.0)
        self.assertTrue(self.registry.fail(failed.action_id, "boom"))
        self.assertEqual(
            _status(self.registry, failed.action_id), ActionStatus.FAILED
        )
        cancelled = self.registry.propose("third", now=1000.0)
        self.assertTrue(self.registry.cancel(cancelled.action_id))
        self.assertEqual(
            _status(self.registry, cancelled.action_id),
            ActionStatus.CANCELLED,
        )
        completions = self.collector.of_type(EventType.ACTION_COMPLETED)
        self.assertEqual(len(completions), 1)

    def test_concurrent_authorize_exactly_one_wins(self) -> None:
        spec = self.registry.propose("push code", now=1000.0)
        outcomes: queue.Queue = queue.Queue()

        def attempt() -> None:
            outcomes.put(
                self.registry.authorize(
                    spec.action_id,
                    granted_permissions=("voice-confirm",),
                    now=1001.0,
                )
            )

        threads = [threading.Thread(target=attempt) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5.0)
        results = [outcomes.get_nowait() for _ in threads]
        self.assertEqual(sum(1 for r in results if r.ok), 1)


class StructuredIntentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = EventBus()
        self.registry = ActionRegistry(bus=self.bus)
        self.voice = _voice()

    def parse(self, text: str, **kwargs) -> StructuredIntent:
        return parse_structured_intent(
            text, self.voice, self.registry, **kwargs
        )

    def test_legacy_mapping_matrix(self) -> None:
        cases = [
            ("stop right now", "STOP", False),
            ("go to sleep", "SLEEP", False),
            ("what did you learn?", "STATUS_INQUIRY", False),
            ("push my code", "PUSH_CODE", False),
            ("never touch .env", "FORBID", False),
            ("hello there", "UNKNOWN", True),
        ]
        for utterance, expected, clarifies in cases:
            with self.subTest(utterance=utterance):
                parsed = self.parse(utterance)
                self.assertEqual(parsed.intent, expected)
                self.assertEqual(
                    parsed.needs_clarification, clarifies
                )
                self.assertIn(parsed.intent, STRUCTURED_INTENTS)

    def test_unknown_carries_speakable_prompt(self) -> None:
        parsed = self.parse("remind me later")
        self.assertTrue(parsed.needs_clarification)
        self.assertIn("status", parsed.clarification)

    def test_approve_single_pending_binds(self) -> None:
        spec = self.registry.propose("push code", now=1000.0)
        parsed = self.parse("go ahead", now=1001.0)
        self.assertEqual(parsed.intent, APPROVE_PENDING_ACTION)
        self.assertFalse(parsed.needs_clarification)
        self.assertEqual(parsed.target_action_id, spec.action_id)
        self.assertEqual(
            parsed.authorization_type, "explicit_voice_confirmation"
        )

    def test_approve_with_no_pending_asks_clarification(self) -> None:
        parsed = self.parse("go ahead", now=1001.0)
        self.assertTrue(parsed.needs_clarification)
        self.assertEqual(parsed.target_action_id, "")
        self.assertIn("no pending action", parsed.clarification)

    def test_approve_ambiguous_lists_candidates(self) -> None:
        first = self.registry.propose(
            "push code", target="github", now=1000.0
        )
        second = self.registry.propose(
            "run tests", target="terminal", now=1001.0
        )
        parsed = self.parse("go ahead", now=1002.0)
        self.assertTrue(parsed.needs_clarification)
        self.assertEqual(parsed.target_action_id, "")
        self.assertEqual(
            set(parsed.candidates),
            {first.action_id, second.action_id},
        )
        self.assertIn(first.action_id, parsed.clarification)
        self.assertIn(second.action_id, parsed.clarification)

    def test_approve_text_mention_disambiguates(self) -> None:
        self.registry.propose("push code", target="github", now=1000.0)
        self.registry.propose("run tests", target="terminal", now=1001.0)
        parsed = self.parse("yes, go ahead with the push", now=1002.0)
        self.assertFalse(parsed.needs_clarification)
        bound = self.registry.get(parsed.target_action_id, now=1002.0)
        assert bound is not None
        self.assertEqual(bound.operation, "push code")

    def test_approve_devstate_focus_disambiguates(self) -> None:
        engine = DeveloperStateEngine(bus=self.bus)
        engine.update({"focus_file": "src/b.py"}, now=999.0)
        self.registry.propose(
            "edit file", file_or_element="src/a.py", now=1000.0
        )
        wanted = self.registry.propose(
            "edit file", file_or_element="src/b.py", now=1001.0
        )
        parsed = self.parse("go ahead", devstate=engine, now=1002.0)
        self.assertFalse(parsed.needs_clarification)
        self.assertEqual(parsed.target_action_id, wanted.action_id)

    def test_approve_explicit_id(self) -> None:
        spec = self.registry.propose("push code", now=1000.0)
        parsed = self.parse(f"go ahead with {spec.action_id}", now=1001.0)
        self.assertFalse(parsed.needs_clarification)
        self.assertEqual(parsed.target_action_id, spec.action_id)

    def test_approve_unknown_id_asks_clarification(self) -> None:
        parsed = self.parse("go ahead with action_nope", now=1001.0)
        self.assertTrue(parsed.needs_clarification)
        self.assertIn("action_nope", parsed.clarification)

    def test_approve_expired_id_asks_clarification(self) -> None:
        spec = self.registry.propose("push code", ttl_s=60.0, now=1000.0)
        parsed = self.parse(f"go ahead with {spec.action_id}", now=2000.0)
        self.assertTrue(parsed.needs_clarification)
        self.assertIn("expired", parsed.clarification)

    def test_approve_completed_id_asks_clarification(self) -> None:
        spec = self.registry.propose("push code", now=1000.0)
        self.registry.authorize(
            spec.action_id, granted_permissions=("voice-confirm",),
            now=1001.0,
        )
        self.registry.complete(spec.action_id)
        parsed = self.parse(f"go ahead with {spec.action_id}", now=1002.0)
        self.assertTrue(parsed.needs_clarification)
        self.assertIn("completed", parsed.clarification)

    def test_parse_is_read_only(self) -> None:
        spec = self.registry.propose("push code", now=1000.0)
        self.parse("go ahead", now=1001.0)
        self.assertEqual(
            _status(self.registry, spec.action_id, now=1001.0),
            ActionStatus.PENDING,
        )

    def test_invalid_inputs_rejected(self) -> None:
        with self.assertRaises(VoiceActionError):
            parse_structured_intent("  ", self.voice, self.registry)
        with self.assertRaises(VoiceActionError):
            parse_structured_intent("go", "not-a-coordinator", self.registry)  # type: ignore[arg-type]
        with self.assertRaises(VoiceActionError):
            parse_structured_intent("go", self.voice, object())  # type: ignore[arg-type]


class TranscriptionWorkerTest(unittest.TestCase):
    def tearDown(self) -> None:
        for worker in getattr(self, "_workers", []):
            worker.stop()

    def _track(self, worker: TranscriptionWorker) -> TranscriptionWorker:
        self._workers = getattr(self, "_workers", [])
        self._workers.append(worker)
        return worker

    def test_submit_transcribes_off_caller_thread(self) -> None:
        bus = EventBus()
        collector = _Collector(bus)
        caller = threading.get_ident()
        seen: list[int] = []

        def fake_transcribe(audio):
            seen.append(threading.get_ident())
            self.assertEqual(audio, b"wav-bytes")
            return "push my code"

        worker = self._track(
            TranscriptionWorker(fake_transcribe, bus=bus,
                              session_id="sess_va")
        )
        worker.start()
        future = worker.submit(b"wav-bytes", correlation_id="corr_1")
        self.assertEqual(future.result(timeout=5.0), "push my code")
        self.assertEqual(len(seen), 1)
        self.assertNotEqual(seen[0], caller)
        completed = collector.of_type(EventType.TRANSCRIPTION_COMPLETED)
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0].payload["text"], "push my code")
        self.assertTrue(completed[0].payload["heard"])
        self.assertEqual(completed[0].correlation_id, "corr_1")

    def test_silence_maps_to_empty_heard_false(self) -> None:
        bus = EventBus()
        collector = _Collector(bus)
        worker = self._track(TranscriptionWorker(lambda audio: None, bus=bus))
        worker.start()
        self.assertIsNone(worker.submit(b"quiet").result(timeout=5.0))
        completed = collector.of_type(EventType.TRANSCRIPTION_COMPLETED)
        self.assertEqual(len(completed), 1)
        self.assertFalse(completed[0].payload["heard"])

    def test_failure_delivered_via_future(self) -> None:
        worker = self._track(
            TranscriptionWorker(_exploding_transcribe)
        )
        worker.start()
        future = worker.submit(b"wav")
        with self.assertRaises(RuntimeError):
            future.result(timeout=5.0)

    def test_submit_requires_start(self) -> None:
        worker = self._track(TranscriptionWorker(lambda audio: "hi"))
        with self.assertRaises(VoiceActionError):
            worker.submit(b"wav")

    def test_queue_full_fails_fast(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        def blocking(audio):
            entered.set()
            self.assertTrue(release.wait(timeout=5.0))
            return "late"

        worker = self._track(
            TranscriptionWorker(blocking, max_queue=1)
        )
        worker.start()
        try:
            first = worker.submit(b"one")
            self.assertTrue(entered.wait(timeout=5.0))
            worker.submit(b"two")  # occupies the single queue slot
            with self.assertRaises(VoiceActionError):
                worker.submit(b"three")
        finally:
            release.set()
        self.assertEqual(first.result(timeout=5.0), "late")

    def test_invalid_construction(self) -> None:
        with self.assertRaises(VoiceActionError):
            TranscriptionWorker("not-callable")  # type: ignore[arg-type]
        with self.assertRaises(VoiceActionError):
            TranscriptionWorker(lambda audio: None, max_queue=0)


def _exploding_transcribe(audio):
    raise RuntimeError("whisper offline")


class VoiceActionServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = EventBus()
        self.collector = _Collector(self.bus)
        self.registry = ActionRegistry(bus=self.bus, session_id="sess_va")
        self.said: list[str] = []

    def test_approve_flow_authorizes_requests_and_speaks(self) -> None:
        spec = self.registry.propose(
            "push code", target="github", now=1000.0
        )
        service = _service(self.bus, self.registry, self.said)
        decision = service.handle_transcript(
            "go ahead", correlation_id="corr_9", now=1001.0
        )
        self.assertEqual(decision.authorized_action_id, spec.action_id)
        assert decision.authorization is not None
        self.assertTrue(decision.authorization.ok)
        self.assertEqual(
            _status(self.registry, spec.action_id),
            ActionStatus.AUTHORIZED,
        )
        parsed = self.collector.of_type(EventType.INTENT_PARSED)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].payload["intent"], APPROVE_PENDING_ACTION)
        self.assertEqual(
            parsed[0].payload["target_action_id"], spec.action_id
        )
        self.assertEqual(parsed[0].correlation_id, "corr_9")
        confirmations = self.collector.of_type(
            EventType.ACTION_CONFIRMATION_RECEIVED
        )
        self.assertEqual(len(confirmations), 1)
        self.assertTrue(confirmations[0].payload["ok"])
        requested = self.collector.of_type(EventType.ACTION_REQUESTED)
        self.assertEqual(len(requested), 1)
        self.assertEqual(requested[0].payload["action_id"], spec.action_id)
        # TTS feedback names the bound action.
        self.assertEqual(len(self.said), 1)
        self.assertIn("push code", self.said[0])
        self.assertIn("github", self.said[0])
        self.assertEqual(decision.reply, self.said[0])
        self.assertEqual(decision.speak_error, "")

    def test_ambiguous_approval_speaks_clarification(self) -> None:
        self.registry.propose("push code", target="github", now=1000.0)
        self.registry.propose("run tests", target="terminal", now=1001.0)
        service = _service(self.bus, self.registry, self.said)
        decision = service.handle_transcript("go ahead", now=1002.0)
        self.assertEqual(decision.authorized_action_id, "")
        self.assertIn("pending actions", decision.reply)
        self.assertEqual(self.said, [decision.reply])
        self.assertEqual(
            self.collector.of_type(EventType.ACTION_REQUESTED), []
        )
        confirmations = self.collector.of_type(
            EventType.ACTION_CONFIRMATION_RECEIVED
        )
        self.assertEqual(len(confirmations), 1)
        self.assertFalse(confirmations[0].payload["ok"])

    def test_expired_approval_speaks_clarification(self) -> None:
        spec = self.registry.propose("push code", ttl_s=60.0, now=1000.0)
        service = _service(self.bus, self.registry, self.said)
        decision = service.handle_transcript(
            f"go ahead with {spec.action_id}", now=2000.0
        )
        self.assertEqual(decision.authorized_action_id, "")
        self.assertIn("expired", decision.reply)
        self.assertEqual(self.said, [decision.reply])
        self.assertEqual(
            self.collector.of_type(EventType.ACTION_REQUESTED), []
        )

    def test_permission_denial_speaks_reason(self) -> None:
        spec = self.registry.propose(
            "type password",
            required_permissions=("voice-confirm", "desktop-control"),
            now=1000.0,
        )
        service = _service(self.bus, self.registry, self.said)
        decision = service.handle_transcript("go ahead", now=1001.0)
        self.assertEqual(decision.authorized_action_id, "")
        self.assertIn("desktop-control", decision.reply)
        self.assertEqual(
            _status(self.registry, spec.action_id, now=1001.0),
            ActionStatus.PENDING,
        )

    def test_non_approval_intents_use_reply_fn(self) -> None:
        service = _service(
            self.bus, self.registry, self.said,
            reply_fn=lambda intent: f"custom:{intent.intent}",
        )
        decision = service.handle_transcript("stop now")
        self.assertEqual(decision.reply, "custom:STOP")
        self.assertEqual(self.said, ["custom:STOP"])
        self.assertEqual(
            self.collector.of_type(EventType.ACTION_REQUESTED), []
        )

    def test_broken_reply_fn_falls_back(self) -> None:
        def broken(intent):
            raise RuntimeError("llm down")

        service = _service(
            self.bus, self.registry, self.said, reply_fn=broken
        )
        decision = service.handle_transcript("go to sleep")
        self.assertIn("Resting", decision.reply)
        self.assertEqual(self.said, [decision.reply])

    def test_unknown_speaks_clarification(self) -> None:
        service = _service(self.bus, self.registry, self.said)
        decision = service.handle_transcript("remind me later")
        self.assertTrue(decision.structured_intent.needs_clarification)
        self.assertEqual(self.said, [decision.reply])

    def test_speak_failure_recorded_not_raised(self) -> None:
        def exploding(text: str) -> None:
            raise RuntimeError("speaker offline")

        service = _service(
            self.bus, self.registry, self.said, speak_fn=exploding
        )
        decision = service.handle_transcript("stop now")
        self.assertIn("speaker offline", decision.speak_error)
        self.assertEqual(self.said, [])

    def test_async_speak_does_not_block(self) -> None:
        spoken = threading.Event()
        received: list[str] = []

        def slow_speak(text: str) -> None:
            received.append(text)
            spoken.set()

        service = _service(
            self.bus, self.registry, self.said,
            speak_fn=slow_speak, speak_async=True,
        )
        started = time.monotonic()
        service.handle_transcript("stop now")
        self.assertLess(time.monotonic() - started, 5.0)
        self.assertTrue(spoken.wait(timeout=5.0))
        self.assertEqual(len(received), 1)

    def test_devstate_receives_conversation_context(self) -> None:
        engine = DeveloperStateEngine(bus=self.bus, session_id="sess_va")
        engine.bind(self.bus)
        self.addCleanup(engine.unbind)
        service = _service(
            self.bus, self.registry, self.said, devstate=engine
        )
        service.handle_transcript("push my code")
        context = engine.state.get("conversation_context")
        self.assertEqual(context["last_intent"], "PUSH_CODE")
        self.assertEqual(context["last_utterance"], "push my code")

    def test_handle_audio_end_to_end(self) -> None:
        # Real clock: handle_audio transcribes + resolves at time.time().
        spec = self.registry.propose("push code", target="github")
        worker = TranscriptionWorker(
            lambda audio: "go ahead", bus=self.bus, session_id="sess_va"
        )
        worker.start()
        self.addCleanup(worker.stop)
        service = _service(
            self.bus, self.registry, self.said, worker=worker
        )
        decision = service.handle_audio(b"wav-bytes")
        self.assertEqual(decision.authorized_action_id, spec.action_id)
        self.assertEqual(len(self.said), 1)
        self.assertEqual(
            len(self.collector.of_type(EventType.TRANSCRIPTION_COMPLETED)), 1
        )
        self.assertEqual(len(self.collector.of_type(EventType.INTENT_PARSED)), 1)

    def test_handle_audio_silence_speaks_retry(self) -> None:
        worker = TranscriptionWorker(
            lambda audio: None, bus=self.bus, session_id="sess_va"
        )
        worker.start()
        self.addCleanup(worker.stop)
        service = _service(
            self.bus, self.registry, self.said, worker=worker
        )
        decision = service.handle_audio(b"quiet")
        self.assertIn("No speech heard", decision.reply)
        self.assertEqual(self.said, [decision.reply])

    def test_handle_audio_failure_speaks_apology(self) -> None:
        worker = TranscriptionWorker(
            _exploding_transcribe, bus=self.bus, session_id="sess_va"
        )
        worker.start()
        self.addCleanup(worker.stop)
        service = _service(
            self.bus, self.registry, self.said, worker=worker
        )
        decision = service.handle_audio(b"wav")
        self.assertIn("transcription failed", decision.reply)
        self.assertEqual(self.said, [decision.reply])

    def test_handle_audio_without_worker_raises(self) -> None:
        service = _service(self.bus, self.registry, self.said)
        with self.assertRaises(VoiceActionError):
            service.handle_audio(b"wav")

    def test_invalid_construction(self) -> None:
        with self.assertRaises(VoiceActionError):
            VoiceActionService("nope", self.registry)  # type: ignore[arg-type]
        with self.assertRaises(VoiceActionError):
            VoiceActionService(_voice(), "nope")  # type: ignore[arg-type]


class QtIndependenceTest(unittest.TestCase):
    def test_voice_stack_runs_without_qt(self) -> None:
        import pathlib
        import subprocess

        root = pathlib.Path(__file__).resolve().parent.parent
        code = (
            "import sys; "
            "from mimir.actions import ActionRegistry; "
            "from mimir.events import EventBus; "
            "from mimir.voice import VoiceCoordinator; "
            "from mimir.voice_action import "
            "TranscriptionWorker, VoiceActionService; "
            "bus = EventBus(); "
            "registry = ActionRegistry(bus=bus); "
            "registry.propose('push code'); "
            "worker = TranscriptionWorker(lambda audio: 'go ahead', bus=bus); "
            "worker.start(); "
            "service = VoiceActionService("
            "VoiceCoordinator(stt_backend=lambda t: None, "
            "tts_backend=lambda t: None), "
            "registry, bus=bus, worker=worker, "
            "speak_fn=lambda text: None, speak_async=False); "
            "decision = service.handle_audio(b'wav'); "
            "worker.stop(); "
            "assert decision.authorized_action_id, 'no approval bound'; "
            "assert 'PyQt6' not in sys.modules, 'PyQt6 leaked'; "
            "assert 'PySide6' not in sys.modules, 'PySide6 leaked'; "
            "print('qt-free-ok')"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            timeout=120,
            cwd=str(root),
        )
        self.assertEqual(
            proc.returncode, 0,
            msg=proc.stderr.decode("utf-8", "replace"),
        )
        self.assertIn("qt-free-ok", proc.stdout.decode("utf-8", "replace"))

    def test_voice_sources_reference_no_qt_or_desktop_state(self) -> None:
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent
        for name in ("mimir/voice.py", "mimir/voice_action.py",
                     "mimir/actions.py"):
            text = (root / name).read_text(encoding="utf-8")
            for banned in ("PyQt6", "PySide6", "desktop_app", "qasync"):
                self.assertNotIn(
                    banned, text, msg=f"{name} references {banned}"
                )


if __name__ == "__main__":
    unittest.main()
