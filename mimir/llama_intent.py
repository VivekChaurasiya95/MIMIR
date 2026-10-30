"""Local Llama natural-language intent parser (100% on-device, zero-cloud).

Connects user voice or text transcripts to a local Llama model (e.g.
llama3.2:1b) running on the on-device Ollama daemon (http://127.0.0.1:11434).
Intuitively interprets natural developer phrasing, resolves context against
DeveloperStateEngine and ActionRegistry, and converts free-form speech into
structured actions.

Safety & Resilience Guarantees:
- Fast-Path Safety: Emergency stop commands ('stop', 'halt', 'pause')
  bypass the LLM entirely and return immediately (0ms).
- 100% Local / Zero-Cloud: Only calls loopback 127.0.0.1; zero telemetry.
- Fail-Closed Fallback: If Ollama is offline, unreachable, or times out,
  cleanly returns None so caller uses the deterministic keyword parser.
- Structured JSON: Enforces Ollama's format='json' for reliable parsing.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any, Optional

DEFAULT_LOCAL_ENDPOINT = "http://127.0.0.1:11434"
DEFAULT_LLAMA_MODEL = "llama3.2:1b"
FALLBACK_LOCAL_MODELS = ("llama3.2:1b", "llama3.2", "mistral:latest", "qwen3:8b", "llama3")

_FAST_PATH_STOP = re.compile(r"\b(stop|halt|abort|kill|freeze|emergency)\b", re.IGNORECASE)
_FAST_PATH_SLEEP = re.compile(r"\b(sleep|rest|standby|take a break)\b", re.IGNORECASE)


def is_ollama_available(endpoint: str = DEFAULT_LOCAL_ENDPOINT, timeout_s: float = 0.5) -> bool:
    """Fast check whether the local Ollama instance is alive."""
    try:
        url = endpoint.rstrip("/") + "/api/tags"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return resp.status == 200
    except Exception:
        return False


def get_available_local_models(endpoint: str = DEFAULT_LOCAL_ENDPOINT, timeout_s: float = 1.0) -> list[str]:
    """List all models currently installed in the local Ollama daemon."""
    try:
        url = endpoint.rstrip("/") + "/api/tags"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            return [m["name"] for m in data.get("models", []) if "name" in m]
    except Exception:
        return []


def select_best_local_model(endpoint: str = DEFAULT_LOCAL_ENDPOINT, preferred: str = DEFAULT_LLAMA_MODEL) -> str:
    """Pick the preferred model if present, or best available local fallback."""
    available = get_available_local_models(endpoint)
    if not available:
        return preferred
    # Exact or prefix match for preferred (e.g. 'llama3.2:1b' matches 'llama3.2:1b' or 'llama3.2')
    for m in available:
        if m == preferred or m.startswith(preferred.split(":")[0]):
            return m
    for fallback in FALLBACK_LOCAL_MODELS:
        for m in available:
            if m == fallback or m.startswith(fallback.split(":")[0]):
                return m
    return available[0]


def query_local_llama_intent(
    text: str,
    *,
    endpoint: str = DEFAULT_LOCAL_ENDPOINT,
    model: Optional[str] = None,
    devstate: Any = None,
    pending_actions: Optional[list[Any]] = None,
    timeout_s: float = 3.5,
) -> Optional[dict[str, Any]]:
    """Query the local Llama model to classify natural language text.

    Returns a dict with keys:
      - 'intent': one of ('APPROVE_PENDING_ACTION', 'PUSH_CODE', 'STATUS_INQUIRY',
                          'FORBID', 'SLEEP', 'STOP', 'UNKNOWN')
      - 'target_action_id': matched action proposal ID or empty string
      - 'confidence': float between 0.0 and 1.0
      - 'reply': speakable natural language feedback
      - 'raw_text': original user text

    Returns None if Ollama is unavailable or times out.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return None

    # Fast-path safety checks: bypass LLM completely for instantaneous response
    if _FAST_PATH_STOP.search(cleaned):
        return {
            "intent": "STOP",
            "target_action_id": "",
            "confidence": 1.0,
            "reply": "Powering down. Say the word if you need me.",
            "raw_text": cleaned,
            "fast_path": True,
        }
    if _FAST_PATH_SLEEP.search(cleaned):
        return {
            "intent": "SLEEP",
            "target_action_id": "",
            "confidence": 1.0,
            "reply": "Resting. Say the word when you need me.",
            "raw_text": cleaned,
            "fast_path": True,
        }

    chosen_model = model or select_best_local_model(endpoint, DEFAULT_LLAMA_MODEL)

    # Gather contextual clues from DeveloperStateEngine
    context_lines = []
    if devstate is not None:
        try:
            snapshot = devstate.snapshot() if hasattr(devstate, "snapshot") else getattr(devstate, "state", None)
            if snapshot is not None:
                if hasattr(snapshot, "active_repo") and snapshot.active_repo:
                    context_lines.append(f"Active Repository: {snapshot.active_repo}")
                if hasattr(snapshot, "active_file") and snapshot.active_file:
                    context_lines.append(f"Active File: {snapshot.active_file}")
                if hasattr(snapshot, "git_dirty") and snapshot.git_dirty:
                    context_lines.append("Git status: uncommitted changes exist")
        except Exception:
            pass

    # Gather pending action descriptions
    actions_lines = []
    if pending_actions:
        for act in pending_actions[:4]:
            act_id = getattr(act, "action_id", "") or str(act)
            op = getattr(act, "operation", "")
            target = getattr(act, "target", "")
            actions_lines.append(f"- ID: {act_id} (Operation: {op}, Target: {target})")

    context_str = "\n".join(context_lines) or "None"
    actions_str = "\n".join(actions_lines) or "No pending actions"

    system_prompt = (
        "You are the Natural Language Intent Parser for Habitous / MIMIR, a 100% on-device developer cobot.\n"
        "Your task is to classify the user's spoken or typed instruction into a structured JSON action.\n\n"
        "AVAILABLE INTENTS:\n"
        "- APPROVE_PENDING_ACTION: User confirms, agrees, or wants to proceed with a proposed action (e.g. 'go ahead', 'do it', 'looks good', 'proceed', 'run that').\n"
        "- PUSH_CODE: User wants to stage, commit, or push changes to git (e.g. 'push my code', 'commit changes', 'ship it').\n"
        "- STATUS_INQUIRY: User asks about memory, learned principles, or what you've noticed (e.g. 'what did you learn', 'status', 'show principles').\n"
        "- FORBID: User defines a negative invariant or rule (e.g. 'never stage .env', 'don't touch migrations').\n"
        "- SLEEP: User tells you to sleep or take a break.\n"
        "- STOP: User wants to halt or abort an ongoing action.\n"
        "- UNKNOWN: Ambiguous request or unrelated conversation.\n\n"
        "OUTPUT FORMAT: You MUST output strictly a JSON object with this exact shape:\n"
        "{\n"
        '  "intent": "<ONE_OF_THE_ABOVE_INTENTS>",\n'
        '  "target_action_id": "<specific action ID from pending actions if approving, else empty>",\n'
        '  "confidence": <float 0.0 to 1.0>,\n'
        '  "reply": "<short, calm, polite spoken confirmation to the user>"\n'
        "}"
    )

    user_prompt = (
        f"CURRENT CONTEXT:\n{context_str}\n\n"
        f"PENDING PROPOSALS:\n{actions_str}\n\n"
        f'USER INSTRUCTION: "{cleaned}"\n\n'
        "Extract intent JSON:"
    )

    payload = {
        "model": chosen_model,
        "prompt": f"{system_prompt}\n\n{user_prompt}",
        "format": "json",
        "stream": False,
        "options": {
            "temperature": 0.1,
            "num_predict": 128,
        },
    }

    try:
        url = endpoint.rstrip("/") + "/api/generate"
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as response:
            raw = response.read().decode("utf-8", errors="replace")
            res_obj = json.loads(raw)
            model_text = (res_obj.get("response") or "").strip()
            parsed = json.loads(model_text)

            raw_intent = str(parsed.get("intent", "UNKNOWN")).strip().upper()
            target_id = str(parsed.get("target_action_id", "")).strip()
            conf = float(parsed.get("confidence", 0.8))
            reply = str(parsed.get("reply", "")).strip()

            # Normalize common synonyms to canonical MIMIR intents (safety and constraints first)
            if "FORBID" in raw_intent or "NEVER" in raw_intent or "INVARIANT" in raw_intent:
                intent = "FORBID"
            elif "STOP" in raw_intent or "HALT" in raw_intent:
                intent = "STOP"
            elif "SLEEP" in raw_intent or "REST" in raw_intent:
                intent = "SLEEP"
            elif "STATUS" in raw_intent or "LEARN" in raw_intent or "MEMORY" in raw_intent:
                intent = "STATUS_INQUIRY"
            elif "APPROVE" in raw_intent or "CONFIRM" in raw_intent:
                intent = "APPROVE_PENDING_ACTION"
            elif "PUSH" in raw_intent or "COMMIT" in raw_intent:
                intent = "PUSH_CODE"
            else:
                intent = raw_intent

            return {
                "intent": intent,
                "target_action_id": target_id,
                "confidence": conf,
                "reply": reply,
                "raw_text": cleaned,
                "model_used": chosen_model,
            }
    except Exception:
        # Graceful fail-closed: return None so deterministic fast fallback takes over
        return None
