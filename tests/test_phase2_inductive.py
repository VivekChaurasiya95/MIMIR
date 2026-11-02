"""Tests for Phase 2 authentic inductive reasoning.

Covers the LLM-first Detective path (prompt guidance + secret
redaction), the demoted FallbackOfflineStub, the standardized
LLMProvider, episode linkage, store/state calibration APIs, the
LearnWorker mining pass, and the Review & Calibration Deck helpers.
Qt worker/UI tests run offscreen only when PyQt6 or PySide6 is
installed, otherwise they skip.
"""

import json
import os
import unittest
from unittest import mock

from desktop_app.llm import (
    DEFAULT_OLLAMA_ENDPOINT,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_PROVIDER,
    LLMProvider,
    build_llm_client,
    default_model_for,
    provider_from_prefs,
)
from desktop_app.prefs import DesktopPrefs
from desktop_app.presenters import format_provenance, review_queue
from desktop_app.state import AppState, get_llm_fn
from mimir.detective import (
    EpisodeCluster,
    FallbackOfflineStub,
    LLMError,
    LLMOutputError,
    build_inference_prompt,
    infer_from_cluster,
    infer_from_cluster_with_llm,
    infer_principles,
)
from mimir.episodes import (
    BehavioralEpisode,
    EpisodeContext,
    EpisodeOutcome,
    FileTransition,
)
from mimir.principles import (
    BehavioralPrinciple,
    ContextTrigger,
    EpistemicStatus,
)
from mimir.store import PrincipleNotFoundError, PrincipleStore


def _episode(eid="ep_p2_001", summary="isolate db errors into domain type",
             diff="+raise DomainError"):
    return BehavioralEpisode(
        id=eid,
        source="git",
        repo="/repo",
        source_ref="aaa111",
        observed_at="2026-10-27T08:30:00Z",
        actor="Ada",
        summary=summary,
        context=EpisodeContext(scope="payments", language="python"),
        transitions=[
            FileTransition(path="payments/db.py", status="modified",
                           diff=diff, additions=1, deletions=0)
        ],
        outcome=EpisodeOutcome(build="pass", tests="pass"),
        redactions_applied=0,
        redaction_kinds=[],
    )


def _cluster(*episodes):
    members = episodes or (_episode("ep_p2_a"), _episode("ep_p2_b"))
    return EpisodeCluster(id="cl_p2", members=members, language="python",
                          scope="payments")


def _llm_payload(**overrides):
    payload = {
        "name": "LLM Inferred Principle",
        "category": "architecture",
        "context_trigger": {"scope": "payments",
                            "language_agnostic": False},
        "latent_invariant": "Always do X in the observed setting.",
        "anti_patterns": ["Doing Y instead"],
    }
    payload.update(overrides)
    return json.dumps(payload)


def _principle(pid="bp_p2_001", confidence=0.65, contradictions=0,
               traces=("ep_p2_001",)):
    return BehavioralPrinciple(
        id=pid,
        name=f"Principle {pid}",
        category="architecture",
        context_trigger=ContextTrigger(scope="payments",
                                       language_agnostic=False),
        latent_invariant="Isolate db errors behind domain errors.",
        anti_patterns=["Letting raw errors propagate"],
        epistemic_status=EpistemicStatus(
            confidence_score=confidence,
            supporting_episodes_count=2,
            contradictions_count=contradictions,
            first_inferred="2026-10-12T10:00:00Z",
            last_validated="2026-10-27T08:30:00Z",
        ),
        inspectable_traces=list(traces),
    )


def _needs_qt(test):
    """Skip the test when Qt bindings are unavailable (offscreen)."""
    try:
        from desktop_app.tabs._qt import load_qt
        load_qt()
    except ImportError:
        test.skipTest("PyQt6/PySide6 not installed")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from desktop_app.tabs._qt import load_qt
    _, QtCore, _, QtWidgets = load_qt()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    try:
        test._qt_app_ref = app
    except AttributeError:
        pass
    return QtCore, QtWidgets, app


def _wait_for_any(signals, QtCore, timeout_ms=15000):
    """Run a local event loop until any signal fires (or timeout)."""
    loop = QtCore.QEventLoop()
    box = {}

    def _make(name):
        def _got(*args):
            box["name"] = name
            box["args"] = args
            loop.quit()
        return _got

    handlers = [(signal, _make(name)) for name, signal in signals]
    for signal, handler in handlers:
        signal.connect(handler)
    QtCore.QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    for signal, handler in handlers:
        try:
            signal.disconnect(handler)
        except (TypeError, RuntimeError):
            pass
    return box


class FallbackOfflineStubTest(unittest.TestCase):
    def test_stub_infers_template_principle(self):
        principle = FallbackOfflineStub.infer(_cluster())
        self.assertIn("Defensive Error Isolation", principle.name)
        self.assertEqual(principle.category, "architecture")
        self.assertEqual(
            set(principle.inspectable_traces), {"ep_p2_a", "ep_p2_b"})
        self.assertEqual(
            principle.epistemic_status.supporting_episodes_count, 2)

    def test_legacy_alias_delegates_to_stub(self):
        cluster = _cluster()
        self.assertEqual(infer_from_cluster(cluster),
                         FallbackOfflineStub.infer(cluster))

    def test_stub_rejects_non_cluster(self):
        with self.assertRaises(ValueError):
            FallbackOfflineStub.infer({"id": "cl_x"})
        with self.assertRaises(ValueError):
            infer_from_cluster("nope")

    def test_stub_is_static_only(self):
        with self.assertRaises(TypeError):
            FallbackOfflineStub()

    def test_templates_owned_by_stub(self):
        self.assertIn("error_handling", FallbackOfflineStub.TEMPLATES)
        self.assertIn("testing", FallbackOfflineStub.TEMPLATES)

    def test_none_llm_fn_uses_stub(self):
        principles = infer_principles(
            [_episode("ep_p2_a"), _episode("ep_p2_b")])
        self.assertEqual(len(principles), 1)
        self.assertIn("Defensive Error Isolation", principles[0].name)

    def test_stub_exported_from_package(self):
        import mimir
        self.assertIs(mimir.FallbackOfflineStub, FallbackOfflineStub)


class InferencePromptTest(unittest.TestCase):
    def test_prompt_names_invariant_trigger_antipattern(self):
        prompt = build_inference_prompt(_cluster())
        self.assertIn("LATENT INVARIANT", prompt)
        self.assertIn("CONTEXT TRIGGER", prompt)
        self.assertIn("ANTI-PATTERNS", prompt)
        self.assertIn("JSON ONLY", prompt)
        self.assertIn("ep_p2_a", prompt)

    def test_prompt_redacts_diff_secrets(self):
        secret = "ghp_abcdefghij1234567890"
        episode = _episode("ep_p2_s", diff=f'+connect("{secret}")')
        prompt = build_inference_prompt(_cluster(episode))
        self.assertNotIn(secret, prompt)
        self.assertIn("[REDACTED:github_token]", prompt)

    def test_prompt_redacts_summary_secrets(self):
        key = "AKIAIOSFODNN7EXAMPLE"
        episode = _episode("ep_p2_k", summary=f"rotate {key} now")
        prompt = build_inference_prompt(_cluster(episode))
        self.assertNotIn(key, prompt)
        self.assertIn("[REDACTED:aws_access_key]", prompt)

    def test_prompt_redacts_assigned_secrets(self):
        episode = _episode("ep_p2_w", diff='+api_key = "hunter2-live"')
        prompt = build_inference_prompt(_cluster(episode))
        self.assertNotIn("hunter2-live", prompt)
        self.assertIn("[REDACTED:assigned_secret]", prompt)


class EpisodeLinkageTest(unittest.TestCase):
    def test_stub_links_all_member_ids(self):
        cluster = _cluster(_episode("ep_p2_c"), _episode("ep_p2_a"),
                           _episode("ep_p2_b"))
        principle = FallbackOfflineStub.infer(cluster)
        self.assertEqual(list(principle.inspectable_traces),
                         ["ep_p2_a", "ep_p2_b", "ep_p2_c"])

    def test_llm_path_links_all_member_ids(self):
        cluster = _cluster(_episode("ep_p2_c"), _episode("ep_p2_a"))
        principle = infer_from_cluster_with_llm(
            cluster, lambda prompt: _llm_payload())
        self.assertEqual(list(principle.inspectable_traces),
                         ["ep_p2_a", "ep_p2_c"])

    def test_learn_persists_episode_links(self):
        state = AppState(store_path=":memory:")
        try:
            stored = state.learn(
                [_episode("ep_p2_a"), _episode("ep_p2_b")], llm_fn=None)
            self.assertEqual(len(stored), 1)
            self.assertEqual(set(stored[0].inspectable_traces),
                             {"ep_p2_a", "ep_p2_b"})
            self.assertEqual(state.learned_ids, {"ep_p2_a", "ep_p2_b"})
        finally:
            state.close()


class LLMProviderTest(unittest.TestCase):
    def test_default_is_on_device_llama(self):
        self.assertEqual(DEFAULT_PROVIDER.id, "ollama")
        self.assertEqual(DEFAULT_PROVIDER.model, DEFAULT_OLLAMA_MODEL)
        self.assertEqual(DEFAULT_PROVIDER.model, "llama3.2:1b")
        self.assertEqual(DEFAULT_PROVIDER.endpoint,
                         DEFAULT_OLLAMA_ENDPOINT)
        self.assertTrue(DEFAULT_PROVIDER.is_live)
        self.assertFalse(DEFAULT_PROVIDER.needs_api_key)

    def test_default_models(self):
        self.assertEqual(default_model_for("ollama"), "llama3.2:1b")
        self.assertEqual(default_model_for("openai"), "gpt-4o-mini")
        self.assertEqual(default_model_for("gemini"), "gemini-2.5-flash")
        self.assertEqual(default_model_for("anthropic"),
                         "claude-3-5-sonnet-latest")
        self.assertEqual(default_model_for("offline"), "")
        self.assertEqual(default_model_for("mystery"), "")

    def test_blank_fields_fill_provider_defaults(self):
        provider = LLMProvider(id="ollama", model="", endpoint="")
        self.assertEqual(provider.model, "llama3.2:1b")
        self.assertEqual(provider.endpoint, DEFAULT_OLLAMA_ENDPOINT)

    def test_unknown_id_normalizes_to_offline(self):
        provider = LLMProvider(id="mystery-llm", model="x")
        self.assertEqual(provider.id, "offline")
        self.assertFalse(provider.is_live)
        self.assertIsNone(provider.build(api_key="k"))

    def test_cloud_needs_key(self):
        for pid in ("openai", "gemini", "anthropic"):
            provider = LLMProvider(id=pid, model="m")
            self.assertTrue(provider.needs_api_key)
            self.assertTrue(provider.is_live)
            self.assertIsNone(provider.build(api_key=""))
            self.assertTrue(callable(provider.build(api_key="k")))

    def test_ollama_needs_no_key(self):
        provider = LLMProvider(id="ollama", model="", endpoint="")
        self.assertTrue(callable(provider.build()))

    def test_build_llm_client_shortcut(self):
        self.assertIsNone(build_llm_client("offline"))
        self.assertTrue(callable(build_llm_client("ollama")))
        self.assertTrue(callable(
            build_llm_client("Local Ollama / Llama 3 Bridge (Self-Hosted)")))
        self.assertIsNone(build_llm_client("openai"))
        self.assertTrue(
            callable(build_llm_client("openai", api_key="k")))
        self.assertIsNone(build_llm_client("mystery", api_key="k"))

    def test_provider_from_prefs(self):
        prefs = DesktopPrefs(model_provider="gemini", model_name="",
                             model_endpoint="")
        provider = provider_from_prefs(prefs)
        self.assertEqual(provider.id, "gemini")
        self.assertEqual(provider.model, "gemini-2.5-flash")
        label = DesktopPrefs(
            model_provider="Gemini 2.5 Flash Bridge (Hybrid Cloud - Optional)")
        self.assertEqual(provider_from_prefs(label).id, "gemini")

    def test_get_llm_fn_uses_standard_provider(self):
        self.assertIsNone(get_llm_fn(DesktopPrefs()))
        self.assertTrue(
            callable(get_llm_fn(DesktopPrefs(model_provider="ollama"))))
        self.assertIsNone(
            get_llm_fn(DesktopPrefs(model_provider="openai", api_key="")))
        self.assertTrue(callable(get_llm_fn(
            DesktopPrefs(model_provider="openai", api_key="k"))))


class StoreCalibrationTest(unittest.TestCase):
    def test_get_roundtrip_and_missing(self):
        store = PrincipleStore(":memory:")
        try:
            store.save(_principle("bp_p2_get", 0.70))
            self.assertEqual(store.get("bp_p2_get").id, "bp_p2_get")
            with self.assertRaises(PrincipleNotFoundError):
                store.get("bp_nope")
        finally:
            store.close()

    def test_set_confidence_absolute_keeps_counts(self):
        store = PrincipleStore(":memory:")
        try:
            store.save(_principle("bp_p2_set", 0.60))
            store.set_confidence("bp_p2_set", 0.95, "deck_review")
            got = store.get("bp_p2_set")
            self.assertAlmostEqual(
                got.epistemic_status.confidence_score, 0.95)
            self.assertEqual(
                got.epistemic_status.supporting_episodes_count, 2)
            self.assertEqual(got.epistemic_status.contradictions_count, 0)
            self.assertIn("deck_review", got.inspectable_traces)
            # Idempotent evidence: setting twice appends once.
            store.set_confidence("bp_p2_set", 0.50, "deck_review")
            got = store.get("bp_p2_set")
            self.assertEqual(
                list(got.inspectable_traces).count("deck_review"), 1)
        finally:
            store.close()

    def test_set_confidence_validates(self):
        store = PrincipleStore(":memory:")
        try:
            store.save(_principle("bp_p2_v", 0.60))
            for bad in (-0.1, 1.5, float("nan")):
                with self.assertRaises(ValueError, msg=f"value={bad!r}"):
                    store.set_confidence("bp_p2_v", bad, "ev")
            with self.assertRaises(PrincipleNotFoundError):
                store.set_confidence("bp_nope", 0.5, "ev")
        finally:
            store.close()

    def test_state_calibration_apis(self):
        state = AppState(store_path=":memory:")
        try:
            state.store.save(_principle("bp_p2_s", 0.60))
            self.assertEqual(state.get_principle("bp_p2_s").id, "bp_p2_s")
            refreshed = state.set_confidence("bp_p2_s", 0.88, "deck")
            self.assertAlmostEqual(
                refreshed.epistemic_status.confidence_score, 0.88)
            with self.assertRaises(PrincipleNotFoundError):
                state.get_principle("bp_nope")
        finally:
            state.close()

    def test_state_update_principle_edits_and_reembeds(self):
        state = AppState(store_path=":memory:")
        try:
            state.store.save(_principle("bp_p2_e", 0.70))
            updated = state.update_principle(
                "bp_p2_e", name="Renamed rule",
                latent_invariant="Retry flaky webhooks exactly three times.",
                anti_patterns=["Blind infinite retries"])
            self.assertEqual(updated.name, "Renamed rule")
            self.assertIn("three times", updated.latent_invariant)
            self.assertEqual(updated.id, "bp_p2_e")
            self.assertEqual(list(updated.inspectable_traces),
                             ["ep_p2_001"])
            self.assertAlmostEqual(
                updated.epistemic_status.confidence_score, 0.70)
            hits = state.search("flaky webhooks retry",
                                top_k=5, min_confidence=0.0)
            self.assertEqual([p.id for p in hits], ["bp_p2_e"])
            with self.assertRaises(ValueError):
                state.update_principle("bp_p2_e", name="   ")
            with self.assertRaises(PrincipleNotFoundError):
                state.update_principle("bp_nope", name="x")
        finally:
            state.close()


class LearnPassTest(unittest.TestCase):
    def test_explicit_llm_mines_with_episode_ids(self):
        from desktop_app.observe_worker import run_learn_pass
        payload = run_learn_pass(
            [_episode("ep_p2_a").to_dict(),
             _episode("ep_p2_b").to_dict()],
            llm_fn=lambda prompt: _llm_payload())
        self.assertFalse(payload["fell_back"])
        self.assertEqual(payload["live_error"], "")
        self.assertEqual(payload["episode_ids"], ["ep_p2_a", "ep_p2_b"])
        self.assertEqual(len(payload["principles"]), 1)
        self.assertEqual(payload["principles"][0]["name"],
                         "LLM Inferred Principle")
        self.assertEqual(
            payload["principles"][0]["inspectable_traces"],
            ["ep_p2_a", "ep_p2_b"])

    def test_explicit_llm_failure_propagates(self):
        from desktop_app.observe_worker import run_learn_pass

        def _boom(prompt):
            raise LLMError("backend down")

        with self.assertRaises(LLMError):
            run_learn_pass([_episode("ep_p2_a").to_dict()], llm_fn=_boom)

    def test_explicit_bad_output_propagates(self):
        from desktop_app.observe_worker import run_learn_pass
        with self.assertRaises(LLMOutputError):
            run_learn_pass([_episode("ep_p2_a").to_dict()],
                           llm_fn=lambda prompt: "not json {{{")

    def test_offline_prefs_use_stub(self):
        from desktop_app.observe_worker import run_learn_pass
        payload = run_learn_pass(
            [_episode("ep_p2_a").to_dict(),
             _episode("ep_p2_b").to_dict()],
            llm_prefs={"model_provider": "offline"})
        self.assertFalse(payload["fell_back"])
        self.assertEqual(len(payload["principles"]), 1)
        self.assertIn("Defensive Error Isolation",
                      payload["principles"][0]["name"])

    def test_auto_failure_falls_back_to_stub(self):
        from desktop_app.observe_worker import run_learn_pass

        def _boom(prompt):
            raise LLMError("daemon stopped")

        with mock.patch("desktop_app.observe_worker._resolve_learn_llm",
                        return_value=_boom):
            payload = run_learn_pass(
                [_episode("ep_p2_a").to_dict()],
                llm_prefs={"model_provider": "ollama"})
        self.assertTrue(payload["fell_back"])
        self.assertIn("daemon stopped", payload["live_error"])
        self.assertEqual(len(payload["principles"]), 1)

    def test_auto_bad_output_falls_back_to_stub(self):
        from desktop_app.observe_worker import run_learn_pass
        with mock.patch("desktop_app.observe_worker._resolve_learn_llm",
                        return_value=lambda prompt: "not json {{{"):
            payload = run_learn_pass([_episode("ep_p2_a").to_dict()])
        self.assertTrue(payload["fell_back"])
        self.assertEqual(len(payload["principles"]), 1)

    def test_force_offline_ignores_explicit_fn(self):
        from desktop_app.observe_worker import run_learn_pass

        def _boom(prompt):
            raise AssertionError("must not be called")

        payload = run_learn_pass([_episode("ep_p2_a").to_dict()],
                                 llm_fn=_boom, force_offline=True)
        self.assertFalse(payload["fell_back"])
        self.assertEqual(len(payload["principles"]), 1)

    def test_rejects_bad_inputs(self):
        from desktop_app.observe_worker import run_learn_pass
        with self.assertRaises(ValueError):
            run_learn_pass("nope")
        with self.assertRaises(ValueError):
            run_learn_pass([{"id": "x"}], min_cluster_size=0)
        with self.assertRaises(ValueError):
            run_learn_pass([_episode().to_dict()], llm_fn="nope")


class ReviewQueueTest(unittest.TestCase):
    def test_low_confidence_queues_first(self):
        low = _principle("bp_p2_q1", 0.40)
        mid = _principle("bp_p2_q2", 0.70)
        high = _principle("bp_p2_q3", 0.95)
        self.assertEqual([p.id for p in review_queue([high, low, mid])],
                         ["bp_p2_q1", "bp_p2_q2"])

    def test_contradictions_queue_despite_high_confidence(self):
        flagged = _principle("bp_p2_qf", 0.95, contradictions=2)
        clean = _principle("bp_p2_qc", 0.95)
        self.assertEqual([p.id for p in review_queue([clean, flagged])],
                         ["bp_p2_qf"])

    def test_ties_break_by_contradictions_then_id(self):
        mild = _principle("bp_p2_qb", 0.70, contradictions=0)
        hot = _principle("bp_p2_qa", 0.70, contradictions=3)
        self.assertEqual([p.id for p in review_queue([mild, hot])],
                         ["bp_p2_qa", "bp_p2_qb"])

    def test_custom_threshold(self):
        mid = _principle("bp_p2_qt", 0.70)
        self.assertEqual(review_queue([mid], min_confidence=0.50), [])
        self.assertEqual(len(review_queue([mid], min_confidence=0.80)), 1)


class FormatProvenanceTest(unittest.TestCase):
    def test_resolves_session_episodes(self):
        episode = _episode("ep_p2_001", summary="fix: isolate db errors")
        text = format_provenance(_principle(traces=("ep_p2_001",)),
                                 [episode])
        self.assertIn("1 trace(s)", text)
        self.assertIn("ep_p2_001", text)
        self.assertIn("fix: isolate db errors", text)
        self.assertIn("payments", text)

    def test_unknown_traces_show_raw(self):
        text = format_provenance(_principle(traces=("external_ev",)), [])
        self.assertIn("external_ev", text)
        self.assertIn("outside this session", text)

    def test_counts_and_empty_traces(self):
        text = format_provenance(
            _principle("bp_p2_pt", 0.80, 1, traces=()))
        self.assertIn("+2/-1 evidence", text)
        self.assertIn("no traces recorded", text)
        self.assertIn("First inferred", text)


class LearnWorkerQtTest(unittest.TestCase):
    def test_worker_mines_off_thread(self):
        QtCore, _, _ = _needs_qt(self)
        from desktop_app.observe_worker import create_learn_worker
        worker = create_learn_worker(
            [_episode("ep_p2_w").to_dict()],
            llm_fn=lambda prompt: _llm_payload(), parent=None)
        try:
            worker.start()
            box = _wait_for_any(
                [("finished", worker.learn_finished),
                 ("error", worker.learn_error),
                 ("cancelled", worker.learn_cancelled)], QtCore)
        finally:
            try:
                worker.cancel()
                worker.wait(2000)
            except Exception:
                pass
        self.assertEqual(box.get("name"), "finished",
                         f"worker did not finish: {box}")
        payload = box["args"][0]
        self.assertEqual(payload["episode_ids"], ["ep_p2_w"])
        self.assertEqual(payload["principles"][0]["name"],
                         "LLM Inferred Principle")

    def test_explicit_failure_surfaces_as_error(self):
        QtCore, _, _ = _needs_qt(self)
        from desktop_app.observe_worker import create_learn_worker

        def _boom(prompt):
            raise LLMError("backend down")

        worker = create_learn_worker([_episode("ep_p2_e").to_dict()],
                                     llm_fn=_boom, parent=None)
        try:
            worker.start()
            box = _wait_for_any(
                [("finished", worker.learn_finished),
                 ("error", worker.learn_error),
                 ("cancelled", worker.learn_cancelled)], QtCore)
        finally:
            try:
                worker.cancel()
                worker.wait(2000)
            except Exception:
                pass
        self.assertEqual(box.get("name"), "error",
                         f"worker did not error: {box}")
        self.assertIn("backend down", box["args"][0])


class CalibrationDeckQtTest(unittest.TestCase):
    def _status(self):
        class _Status:
            def __init__(self):
                self.messages = []

            def showMessage(self, text, timeout=0):
                self.messages.append(text)

        return _Status()

    def _build(self, state, QtWidgets):
        from desktop_app.tabs.memory import build_memory_tab
        status = self._status()
        root = build_memory_tab(state, status)
        self._roots.append(root)
        self._status_stub = status
        return root

    def setUp(self):
        self._roots = []

    def tearDown(self):
        for root in self._roots:
            try:
                root.close()
            except Exception:
                pass
        self._roots.clear()

    def _btn(self, root, QtWidgets, text):
        return next(b for b in root.findChildren(QtWidgets.QPushButton)
                    if b.text() == text)

    def test_deck_steps_through_review_queue(self):
        _, QtWidgets, app = _needs_qt(self)
        from desktop_app import AppState
        state = AppState(store_path=":memory:")
        self.addCleanup(state.close)
        state.store.save(_principle("bp_p2_d1", 0.40))
        state.store.save(_principle("bp_p2_d2", 0.70))
        state.store.save(_principle("bp_p2_d3", 0.95))
        root = self._build(state, QtWidgets)
        app.processEvents()
        deck_open = self._btn(root, QtWidgets, "Open in detail \u2192")
        self.assertTrue(deck_open.isEnabled())
        previews = [lbl for lbl in root.findChildren(QtWidgets.QLabel)
                    if "Principle bp_p2_d" in lbl.text()]
        self.assertEqual(len(previews), 1)
        self.assertIn("bp_p2_d1", previews[0].text())
        self._btn(root, QtWidgets, "Next \u2192").click()
        app.processEvents()
        previews = [lbl for lbl in root.findChildren(QtWidgets.QLabel)
                    if "Principle bp_p2_d" in lbl.text()]
        self.assertIn("bp_p2_d2", previews[0].text())
        deck_open.click()
        app.processEvents()
        details = root.findChildren(QtWidgets.QPlainTextEdit)
        self.assertTrue(details)
        self.assertIn("bp_p2_d2", details[0].toPlainText())
        self.assertIn("Provenance:", details[0].toPlainText())

    def test_deck_empty_when_calibrated(self):
        _, QtWidgets, app = _needs_qt(self)
        from desktop_app import AppState
        state = AppState(store_path=":memory:")
        self.addCleanup(state.close)
        state.store.save(_principle("bp_p2_ok", 0.95))
        root = self._build(state, QtWidgets)
        app.processEvents()
        deck_open = self._btn(root, QtWidgets, "Open in detail \u2192")
        self.assertFalse(deck_open.isEnabled())
        subs = [lbl.text() for lbl in
                root.findChildren(QtWidgets.QLabel)]
        self.assertTrue(any("nothing needs review" in t for t in subs))

    def test_set_confidence_button_sets_absolute(self):
        _, QtWidgets, app = _needs_qt(self)
        from desktop_app import AppState
        state = AppState(store_path=":memory:")
        self.addCleanup(state.close)
        state.store.save(_principle("bp_p2_c", 0.60))
        root = self._build(state, QtWidgets)
        app.processEvents()
        cards = root.findChildren(QtWidgets.QFrame, "principle_card")
        self.assertTrue(cards)
        cards[0].clicked.emit()
        app.processEvents()
        spins = root.findChildren(QtWidgets.QDoubleSpinBox)
        self.assertEqual(len(spins), 1)
        spins[0].setValue(0.91)
        self._btn(root, QtWidgets, "Set confidence").click()
        app.processEvents()
        self.assertAlmostEqual(
            state.get_principle("bp_p2_c").epistemic_status.confidence_score,
            0.91)
        self.assertTrue(any("0.91" in m for m in self._status_stub.messages))

    def test_edit_dialog_accept_saves(self):
        _, QtWidgets, app = _needs_qt(self)
        from desktop_app import AppState
        state = AppState(store_path=":memory:")
        self.addCleanup(state.close)
        state.store.save(_principle("bp_p2_x", 0.70))
        root = self._build(state, QtWidgets)
        app.processEvents()
        cards = root.findChildren(QtWidgets.QFrame, "principle_card")
        cards[0].clicked.emit()
        app.processEvents()
        box = QtWidgets.QDialog
        orig_exec = box.exec
        box.exec = lambda self_: box.DialogCode.Accepted
        self.addCleanup(setattr, box, "exec", orig_exec)
        self._btn(root, QtWidgets, "Edit rule\u2026").click()
        app.processEvents()
        self.assertTrue(any("updated" in m.lower()
                            for m in self._status_stub.messages))
        box.exec = lambda self_: box.DialogCode.Rejected
        self._btn(root, QtWidgets, "Edit rule\u2026").click()
        app.processEvents()
        self.assertEqual(len(state.list_principles()), 1)


if __name__ == "__main__":
    unittest.main()
