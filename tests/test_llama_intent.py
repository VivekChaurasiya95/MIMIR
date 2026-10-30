"""Tests for mimir.llama_intent (100% on-device local intent parser)."""

import unittest
from unittest.mock import MagicMock, patch

from mimir.actions import ActionRegistry, ActionSpec
from mimir.llama_intent import (
    DEFAULT_LLAMA_MODEL,
    is_ollama_available,
    query_local_llama_intent,
    select_best_local_model,
)
from mimir.voice import VoiceCoordinator
from mimir.voice_action import parse_structured_intent


class TestLocalLlamaIntent(unittest.TestCase):
    def test_fast_path_stop(self):
        """Emergency stop words must return STOP instantly without LLM query."""
        for phrase in ("stop", "STOP immediately!", "halt right now", "abort action", "emergency pause"):
            res = query_local_llama_intent(phrase)
            self.assertIsNotNone(res)
            self.assertEqual(res["intent"], "STOP")
            self.assertTrue(res.get("fast_path"))

    def test_fast_path_sleep(self):
        """Sleep words must return SLEEP instantly without LLM query."""
        for phrase in ("sleep", "go to sleep", "take a break"):
            res = query_local_llama_intent(phrase)
            self.assertIsNotNone(res)
            self.assertEqual(res["intent"], "SLEEP")
            self.assertTrue(res.get("fast_path"))

    def test_empty_input_returns_none(self):
        self.assertIsNone(query_local_llama_intent(""))
        self.assertIsNone(query_local_llama_intent("   "))

    def test_offline_fallback(self):
        """When endpoint is unreachable, cleanly returns None."""
        res = query_local_llama_intent(
            "Push changes please",
            endpoint="http://127.0.0.1:9999",
            timeout_s=0.2,
        )
        self.assertIsNone(res)

    @patch("mimir.llama_intent.urllib.request.urlopen")
    def test_mocked_llm_approval(self, mock_urlopen):
        """Test parsing an informal approval sentence with pending action."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = (
            b'{"response": "{\\"intent\\": \\"APPROVE_PENDING_ACTION\\", \\"target_action_id\\": \\"action_42\\", \\"confidence\\": 0.99, \\"reply\\": \\"On it.\\"}"}'
        )
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        res = query_local_llama_intent("Yeah looks fantastic, please proceed with that action")
        self.assertIsNotNone(res)
        self.assertEqual(res["intent"], "APPROVE_PENDING_ACTION")
        self.assertEqual(res["target_action_id"], "action_42")

    def test_integration_with_voice_action_fast_path(self):
        coord = VoiceCoordinator()
        reg = ActionRegistry()
        res = parse_structured_intent("STOP IMMEDIATELY", coord, reg)
        self.assertEqual(res.intent, "STOP")


if __name__ == "__main__":
    unittest.main()
