"""Hardware + backend diagnostics for ``mimir doctor`` (Qt-free).

Phase 0 health gate: microphone capture, Whisper transcription,
ffmpeg resolution, local Ollama / Llama 3.2 reachability, and
principle-store health. Every probe fails closed to ``(False, hint)``
— :func:`run_doctor` never raises for backend reasons and never
writes to the configured store file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from .llama_intent import (
    DEFAULT_LLAMA_MODEL,
    DEFAULT_LOCAL_ENDPOINT,
    get_available_local_models,
    is_ollama_available,
)
from .voice import _ensure_ffmpeg, audio_backend_status

__all__ = [
    "DEFAULT_WANT_MODEL",
    "DoctorReport",
    "check_ffmpeg",
    "check_microphone",
    "check_ollama",
    "check_storage",
    "check_whisper",
    "run_doctor",
]

#: Llama model the desktop prefers (prefix match accepts ``llama3.2`` too).
DEFAULT_WANT_MODEL = DEFAULT_LLAMA_MODEL


@dataclass
class DoctorReport:
    """One ``mimir doctor`` run: printable lines + overall verdict."""

    lines: list[str] = field(default_factory=list)
    ok: bool = True


def _line(report: DoctorReport, passed: bool, text: str) -> None:
    tag = "PASS" if passed else "FAIL"
    report.lines.append(f"[{tag}] {text}")
    if not passed:
        report.ok = False


def check_microphone() -> tuple[bool, str]:
    """Probe mic capture backends (never raises)."""
    try:
        status = audio_backend_status()
    except Exception as exc:
        return (False, f"Microphone probe failed ({exc}).")
    if status.get("available"):
        backend = status.get("backend") or "unknown"
        names = list(status.get("microphones") or [])
        detail = f"{len(names)} input device(s)" if names else "input ready"
        return (True, f"Microphone hardware open ({backend}, {detail}).")
    hint = str(status.get("hint") or "no capture backend.").strip()
    return (False, f"Microphone unavailable - {hint}")


def check_whisper() -> tuple[bool, str]:
    """Check on-device Whisper import (never raises)."""
    try:
        import whisper  # noqa: F401
    except ImportError:
        return (False, "Whisper missing - pip install openai-whisper.")
    except Exception as exc:
        return (False, f"Whisper unusable ({exc}).")
    return (True, "Whisper ready.")


def check_ffmpeg() -> tuple[bool, str]:
    """Resolve an ffmpeg binary for Whisper (never raises)."""
    try:
        found = _ensure_ffmpeg()
    except Exception as exc:
        return (False, f"ffmpeg probe failed ({exc}).")
    if found:
        return (True, f"ffmpeg available ({found}).")
    return (False, "ffmpeg missing - pip install imageio-ffmpeg, "
            "or install FFmpeg and add it to PATH.")


def check_ollama(endpoint: str = DEFAULT_LOCAL_ENDPOINT,
                 want_model: str = DEFAULT_WANT_MODEL,
                 timeout_s: float = 2.0) -> tuple[bool, str]:
    """Check the loopback Ollama daemon + Llama 3.2 model (never raises)."""
    try:
        if not is_ollama_available(endpoint, timeout_s=timeout_s):
            return (False, f"Ollama down at {endpoint} - run "
                    "'ollama serve' (Settings -> Model -> Start Ollama).")
        models = get_available_local_models(endpoint, timeout_s=timeout_s)
    except Exception as exc:
        return (False, f"Ollama probe failed ({exc}).")
    if not models:
        return (True, f"Ollama live at {endpoint} but no models "
                f"installed - run 'ollama pull {want_model}'.")
    prefix = want_model.split(":")[0]
    hit = next((m for m in models
                if m == want_model or m.startswith(prefix)), None)
    if hit is None:
        return (True, f"Ollama live at {endpoint} ({len(models)} "
                f"model(s)) but {want_model} missing - run "
                f"'ollama pull {want_model}'.")
    return (True, f"Local Ollama Llama 3.2 connected ({hit}).")


def check_storage(store_path: Optional[str] = None) -> tuple[bool, str]:
    """Verify the principle store round-trips (never writes, never raises).

    Uses an in-memory store for the write round-trip, then checks the
    configured store path (when given and not ``:memory:``) is readable
    when present and creatable when absent — without creating it.
    """
    try:
        from .store import PrincipleStore
    except Exception as exc:
        return (False, f"Storage import failed ({exc}).")
    try:
        store = PrincipleStore(":memory:")
        try:
            store.list_all()
        finally:
            store.close()
    except Exception as exc:
        return (False, f"Storage round-trip failed ({exc}).")
    if store_path and store_path != ":memory:":
        try:
            target = os.path.abspath(store_path)
            if os.path.exists(target):
                if not os.access(target, os.R_OK | os.W_OK):
                    return (False, f"Storage file not writable: {target}.")
                return (True, f"Storage healthy ({target}).")
            parent = os.path.dirname(target) or "."
            if not os.path.isdir(parent) or not os.access(parent, os.W_OK):
                return (False, f"Storage directory not writable: {parent}.")
            return (True, f"Storage healthy (store not created yet; "
                    f"{parent} writable).")
        except Exception as exc:
            return (False, f"Storage path check failed ({exc}).")
    return (True, "Storage healthy (:memory: round-trip ok).")


def run_doctor(store_path: Optional[str] = None,
               endpoint: str = DEFAULT_LOCAL_ENDPOINT) -> DoctorReport:
    """Run every Phase 0 health check (never raises)."""
    report = DoctorReport()
    try:
        _line(report, *check_microphone())
        _line(report, *check_whisper())
        _line(report, *check_ffmpeg())
        _line(report, *check_ollama(endpoint=endpoint))
        _line(report, *check_storage(store_path))
    except Exception as exc:  # a diagnostic must never crash its caller
        _line(report, False, f"doctor harness failed ({exc}).")
    return report


# ---------------------------------------------------------------------------
# MIMIR 3.0 cobot health gate (Gen 2.5): Ollama, git, python env, storage.
# Additive; the Phase 0 checks above are untouched.
# ---------------------------------------------------------------------------

__all__ += ["check_git", "check_python_env", "run_cobot_doctor"]


def check_git(repo: str = ".") -> tuple[bool, str]:
    """Verify git exists and ``repo`` is a working tree (never raises)."""
    import subprocess

    try:
        version = subprocess.run(
            ["git", "--version"], capture_output=True, text=True, timeout=5
        )
    except Exception:
        return (False, "git missing - install Git for Windows.")
    if version.returncode != 0:
        return (False, "git binary broken - reinstall Git.")
    try:
        inside = subprocess.run(
            ["git", "-C", repo, "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception as exc:
        return (False, f"git probe failed ({exc}).")
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return (False, f"{os.path.abspath(repo)} is not a git working tree.")
    label = version.stdout.strip() or "git"
    return (True, f"{label}; repo ready at {os.path.abspath(repo)}.")


def check_python_env() -> tuple[bool, str]:
    """Verify interpreter + pytest runner (never raises)."""
    import sys

    if sys.version_info < (3, 11):
        return (False, f"Python {sys.version.split()[0]} < 3.11 - upgrade.")
    try:
        import pytest  # noqa: F401
    except ImportError:
        return (False, "pytest missing - pip install pytest.")
    except Exception as exc:
        return (False, f"pytest unusable ({exc}).")
    return (True, f"Python {sys.version.split()[0]} + pytest ready.")


def run_cobot_doctor(store_path: Optional[str] = None,
                     endpoint: str = DEFAULT_LOCAL_ENDPOINT,
                     repo: str = ".") -> DoctorReport:
    """Run the Gen 2.5 health gate: Ollama, git, python, storage."""
    report = DoctorReport()
    try:
        _line(report, *check_ollama(endpoint=endpoint))
        _line(report, *check_git(repo))
        _line(report, *check_python_env())
        _line(report, *check_storage(store_path))
    except Exception as exc:  # a diagnostic must never crash its caller
        _line(report, False, f"cobot doctor harness failed ({exc}).")
    return report
