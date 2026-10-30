"""Live LLM provider adapters (Qt-free, stdlib-only).

Each builder returns a :data:`mimir.actor.LLMFn` callable (prompt string in,
solution string out) that :meth:`desktop_app.state.AppState.replicate` can
use for live synthesis. Network access uses :mod:`urllib` so no third-party
HTTP dependency is required; failures surface as :class:`LLMError` with an
actionable message, and callers fall back to the offline scaffold.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable

__all__ = [
    "DEFAULT_GEMINI_MODEL",
    "DEFAULT_OLLAMA_ENDPOINT",
    "DEFAULT_OLLAMA_MODEL",
    "DEFAULT_OPENAI_MODEL",
    "DEFAULT_ANTHROPIC_MODEL",
    "LLMFn",
    "LLMError",
    "build_anthropic_client",
    "build_gemini_client",
    "build_ollama_client",
    "build_openai_client",
    "normalize_provider",
]

#: Pluggable LLM completion: prompt string in, solution string out.
LLMFn = Callable[[str], str]

DEFAULT_OLLAMA_ENDPOINT = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "llama3.2:1b"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_ANTHROPIC_MODEL = "claude-3-5-sonnet-latest"


class LLMError(Exception):
    """Base error for live LLM provider failures."""


def normalize_provider(provider: str) -> str:
    """Map a provider label or id to a short provider id.

    Accepts persisted ids (``"offline"``, ``"openai"``, ``"anthropic"``,
    ``"gemini"``, ``"ollama"``) as well as the long Settings-combo labels
    (e.g. ``"Gemini 2.5 Flash Bridge (Hybrid Cloud - Optional)"``).
    Unknown or empty values resolve to ``"offline"`` (graceful fallback).
    """
    text = (provider or "").strip().lower()
    if not text:
        return "offline"
    if "ollama" in text or "llama" in text:
        return "ollama"
    if "openai" in text or "gpt" in text:
        return "openai"
    if "anthropic" in text or "claude" in text:
        return "anthropic"
    if "gemini" in text or "google" in text:
        return "gemini"
    if text in ("offline", "openai", "anthropic", "gemini", "ollama"):
        return text
    if "offline" in text or "heuristic" in text or "scaffold" in text:
        return "offline"
    return "offline"


def _non_empty(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field}: expected a non-empty string")
    return value.strip()


def _post_json(url: str, payload: dict[str, Any],
               headers: dict[str, str],
               timeout_sec: float = 60.0) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers,
                                     method="POST")
    try:
        with urllib.request.urlopen(request,
                                    timeout=timeout_sec) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            detail = ""
        raise LLMError(
            f"provider request failed (HTTP {exc.code}): {detail or exc}"
        ) from exc
    except urllib.error.URLError as exc:
        raise LLMError(f"provider unreachable: {exc}") from exc
    except TimeoutError as exc:
        raise LLMError(f"provider timed out: {exc}") from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise LLMError(f"provider returned invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise LLMError("provider returned an unexpected response shape")
    return data


def build_ollama_client(endpoint: str = DEFAULT_OLLAMA_ENDPOINT,
                        model: str = DEFAULT_OLLAMA_MODEL,
                        timeout_sec: float = 120.0) -> LLMFn:
    """Build an LLM function over a self-hosted Ollama server (no API key)."""
    endpoint = _non_empty(endpoint, field="endpoint").rstrip("/")
    model = _non_empty(model, field="model")

    def complete(prompt: str) -> str:
        prompt = _non_empty(prompt, field="prompt")
        data = _post_json(f"{endpoint}/api/generate",
                          {"model": model, "prompt": prompt,
                           "stream": False},
                          {"Content-Type": "application/json"},
                          timeout_sec=timeout_sec)
        text = (data.get("response") or "").strip()
        if not text:
            raise LLMError(f"ollama returned no response text: {data!r}"[:300])
        return text

    return complete


def build_openai_client(api_key: str,
                        model: str = DEFAULT_OPENAI_MODEL,
                        endpoint: str = "https://api.openai.com/v1/chat/completions",
                        timeout_sec: float = 60.0) -> LLMFn:
    """Build an LLM function over the OpenAI chat-completions API."""
    api_key = _non_empty(api_key, field="api_key")
    model = _non_empty(model, field="model")
    endpoint = _non_empty(endpoint, field="endpoint")

    def complete(prompt: str) -> str:
        prompt = _non_empty(prompt, field="prompt")
        data = _post_json(endpoint,
                          {"model": model,
                           "messages": [{"role": "user",
                                         "content": prompt}]},
                          {"Content-Type": "application/json",
                           "Authorization": f"Bearer {api_key}"},
                          timeout_sec=timeout_sec)
        try:
            text = data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError, AttributeError):
            raise LLMError(
                f"openai returned an unexpected shape: {data!r}"[:300]
            ) from None
        if not text:
            raise LLMError("openai returned empty content")
        return text

    return complete


def build_gemini_client(api_key: str,
                        model: str = DEFAULT_GEMINI_MODEL,
                        timeout_sec: float = 60.0) -> LLMFn:
    """Build an LLM function over the Google Gemini generateContent API."""
    api_key = _non_empty(api_key, field="api_key")
    model = _non_empty(model, field="model")

    def complete(prompt: str) -> str:
        prompt = _non_empty(prompt, field="prompt")
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent?key={api_key}")
        data = _post_json(url,
                          {"contents": [{"parts": [{"text": prompt}]}]},
                          {"Content-Type": "application/json"},
                          timeout_sec=timeout_sec)
        try:
            text = data["candidates"][0]["content"]["parts"][0][
                "text"].strip()
        except (KeyError, IndexError, TypeError, AttributeError):
            raise LLMError(
                f"gemini returned an unexpected shape: {data!r}"[:300]
            ) from None
        if not text:
            raise LLMError("gemini returned empty content")
        return text

    return complete


def build_anthropic_client(api_key: str,
                           model: str = DEFAULT_ANTHROPIC_MODEL,
                           endpoint: str = "https://api.anthropic.com/v1/messages",
                           timeout_sec: float = 60.0,
                           max_tokens: int = 2048) -> LLMFn:
    """Build an LLM function over the Anthropic messages API."""
    api_key = _non_empty(api_key, field="api_key")
    model = _non_empty(model, field="model")
    endpoint = _non_empty(endpoint, field="endpoint")

    def complete(prompt: str) -> str:
        prompt = _non_empty(prompt, field="prompt")
        data = _post_json(endpoint,
                          {"model": model, "max_tokens": max_tokens,
                           "messages": [{"role": "user",
                                         "content": prompt}]},
                          {"Content-Type": "application/json",
                           "x-api-key": api_key,
                           "anthropic-version": "2023-06-01"},
                          timeout_sec=timeout_sec)
        try:
            blocks = data["content"]
            text = "".join(b.get("text", "") for b in blocks
                           if isinstance(b, dict)).strip()
        except (KeyError, TypeError, AttributeError):
            raise LLMError(
                f"anthropic returned an unexpected shape: {data!r}"[:300]
            ) from None
        if not text:
            raise LLMError("anthropic returned empty content")
        return text

    return complete
