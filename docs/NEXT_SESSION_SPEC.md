# MIMIR: Development Blueprint & Implementation Specification
> **Phase IV: Live Embodiment Integration & Grand Demonstration Readiness**  
> *Bridging the Cognitive Core, Robotic Actuator, and Expressive Desktop App for an End-to-End Live Experience*

---

## 1. Executive Status: Core Engine & Desktop App UI Verified

MIMIR's entire cognitive brain, physical embodiment layer, and desktop companion GUI are fully built and verified with **170 out of 170 unit tests passing (100%)**:

```
========================= COMPLETED ARCHITECTURE =========================
[Phase I-II] Cognitive Core : episodes, telemetry, detective, store, actor
                              83 / 83 Unit Tests Passing (100%)         ✅
[Phase III]  Embodiment     : actuator, voice, visor, unified CLI, demo
                              58 / 58 Unit Tests Passing (100%)         ✅
[Phase III]  Desktop UI     : uncluttered tabs, banners, smiling visor
                              29 / 29 Unit Tests Passing (100%)         ✅
--------------------------------------------------------------------------
Total Verified Test Suite   : 170 / 170 Unit Tests Passing (100%)       ✅
==========================================================================
```

### Current Milestone Achievements
1. **Cognitive Learning Engine (`mimir/`)**:
   - Continuous Git observation with AST difference extraction and strict privacy redaction.
   - Inductive clustering detective mining behavioral invariants and contra-indicators.
   - Dual vector memory store (SQLite + TF-IDF cosine similarity) with epistemic confidence tracking.
   - Persona-aligned replication actor steering task synthesis from retrieved principles.
2. **Robotic Physical Embodiment (`mimir/actuator.py`, `mimir/voice.py`, `mimir/ui/visor.py`)**:
   - Compliant OS actuator with cubic Bézier cursor trajectories and syntactic typing cadence.
   - Backdrivable emergency killswitch halting execution within $<10$ ms on human touch or `ESC`.
   - Voice coordinator with Whisper STT, TTS synthesis, and semantic intent parsing.
   - Scripted 4-scene golden showcase demo in `demo_live_cobot.py`.
3. **Desktop App Experience (`desktop_app/`)**:
   - Decluttered, spacious design across all 6 primary tabs (Home, Observe, Memory, Replicate, Trust, Settings).
   - Dedicated functionality banners explaining capabilities and guardrails for every tab and subpage.
   - Living, personified `CobotVisorWidget` avatar with natural blinking, organic head geometry, state color shifts, and an interactive smile.

---

## 2. Phase IV Objectives: Closing the Bridge for Live Demonstration

All core algorithms and UI surfaces exist, but the physical actuation, live voice loop, and real-time LLM synthesis need to be wired directly into the Desktop App for a cohesive live demonstration.

```
                      MIMIR LIVE INTEGRATION BRIDGE
  ┌────────────────────────────────────────────────────────────────────────┐
  │                           DESKTOP COMPANION                            │
  │  [Home Cockpit]    [Observe Feed]    [Memory Store]    [Replicate Studio]
  └───────┬──────────────────┬──────────────────┬──────────────────┬───────┘
          │                  │                  │                  │
          ▼                  ▼                  ▼                  ▼
  ┌────────────────────────────────────────────────────────────────────────┐
  │                       PHASE IV INTEGRATION MODULES                     │
  ├────────────────────────────────────────────────────────────────────────┤
  │ 1. In-App Live Showcase  : 1-click execution of the 4 golden scenes    │
  │ 2. Compliant Drive Action: "⚡ Apply / Drive with Cobot" in Replicate    │
  │ 3. Live LLM Provider     : Connect Settings API keys to real synthesis │
  │ 4. Spoken Voice Loop     : Real-time TTS speech & audio wave visor     │
  │ 5. Instant Persona Seed  : 1-click loading of rich developer profiles  │
  └────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Detailed Implementation Specifications

### 3.1 Feature 1: Interactive 1-Click Live Cobot Showcase (`desktop_app/showcase.py`)

#### Purpose
Provide a single, prominent **"▶ Run Live Cobot Showcase"** button in the Home tab that executes the complete 4-scene golden script with real-time visual feedback on the desktop UI and smiling cobot visor face.

#### Technical Specifications
```python
"""desktop_app/showcase.py specification."""

from __future__ import annotations
from enum import Enum
from typing import Callable, Optional
from PyQt6 import QtCore

class ShowcaseScene(Enum):
    OBSERVE = "1_observe"
    VOICE_COORDINATE = "2_voice"
    COMPLIANT_DRIVE = "3_drive"
    ESTOP_CORRECTION = "4_estop"
    COMPLETE = "complete"

class ShowcaseRunner(QtCore.QObject):
    """Orchestrates the 4 live showcase scenes on a background QThread.
    
    Signals:
        scene_changed(ShowcaseScene, str explanation)
        visor_state_requested(str state_name)
        log_message(str text)
        showcase_completed(bool success)
    """
    scene_changed = QtCore.pyqtSignal(str, str)
    visor_state_requested = QtCore.pyqtSignal(str)
    log_message = QtCore.pyqtSignal(str)
    showcase_completed = QtCore.pyqtSignal(bool)

    def run_scene_1_observe(self) -> None: ...
    def run_scene_2_voice(self) -> None: ...
    def run_scene_3_drive(self, simulate_hardware: bool = False) -> None: ...
    def run_scene_4_estop(self) -> None: ...
```

#### Visual Flow:
1. **Scene 1 (Observe)**: Visor turns blue (observing) $\rightarrow$ ingests commits $\rightarrow$ turns purple (thinking) $\rightarrow$ extracts test/formatting principles into Memory.
2. **Scene 2 (Voice Coordinate)**: Visor turns green (listening) $\rightarrow$ companion speaks aloud via TTS: *"I noticed you write test-backed changes. Would you like me to handle your current changes?"*
3. **Scene 3 (Compliant Driving)**: Visor turns amber (driving) $\rightarrow$ cursor glides smoothly across screen $\rightarrow$ types commit message and terminal commands with human cadence.
4. **Scene 4 (E-Stop Brake)**: User nudges mouse or hits `ESC` $\rightarrow$ Visor flashes red (alert) $\rightarrow$ speaks: *"Stopped. What was about to go wrong?"* $\rightarrow$ registers new invariant into Behavioral Memory.

---

### 3.2 Feature 2: Compliant Driving in Replicate Tab (`desktop_app/tabs/replicate.py`)

#### Purpose
Allow the user to click **"⚡ Apply / Drive with Cobot"** on any synthesized code solution. MIMIR smoothly glides the cursor to the target editor window, pauses briefly, and types the code compliant with human cadence, protected by the `<10` ms backdrivable E-stop.

#### Implementation Details:
* Add `drive_btn = QPushButton("⚡ Apply with Cobot")` alongside `Copy solution`.
* Hook into `mimir/actuator.py::DesktopActuator`.
* Provide a floating countdown overlay or notification ("Starting cobot drive in 3s... Press ESC or touch mouse to stop").
* If human hand nudges the mouse or presses `ESC`, raise `CobotInterruptedError`, flash visor to alert (`#EF4444`), and offer instant negative invariant creation.

---

### 3.3 Feature 3: Live LLM Provider Integration (`desktop_app/state.py`)

#### Purpose
Currently, `state.replicate()` uses offline heuristic scaffolding because `llm_fn=None`. Wire the provider choices from Settings (OpenAI, Anthropic, Gemini, Local Ollama) to supply a live completion callable.

#### Implementation Details:
```python
def get_llm_fn(prefs: DesktopPrefs) -> Optional[Callable[[str], str]]:
    """Builds a pluggable LLM completion function based on user preferences.
    
    Returns None if no API key is supplied or mode is set to 100% Offline.
    """
    if prefs.model_provider == "ollama":
        return build_ollama_client(endpoint=prefs.model_endpoint, model=prefs.model_name)
    elif prefs.model_provider == "openai" and prefs.api_key:
        return build_openai_client(api_key=prefs.api_key, model=prefs.model_name)
    elif prefs.model_provider == "gemini" and prefs.api_key:
        return build_gemini_client(api_key=prefs.api_key, model=prefs.model_name)
    return None  # Graceful fallback to offline scaffold
```

---

### 3.4 Feature 4: Live Audio Loop & Spoken TTS Feedback (`desktop_app/voice_worker.py`)

#### Purpose
Enable real audio output and microphone listening in the desktop app:
* When the user presses the `🎙` mic button on Home or Replicate, capture speech via Whisper/SpeechRecognition on a non-blocking `QThread`.
* When MIMIR answers via `state.respond()`, trigger voice speech through `VoiceCoordinator.speak()` so the companion answers audibly.
* Visor mouth curve and eyes animate dynamically while speech audio is playing.

---

### 3.5 Feature 5: One-Click Demo Persona Seeding (`desktop_app/tabs/memory.py`)

#### Purpose
Ensure that whenever MIMIR is opened for a demo on a fresh machine or without git history, the user can click **"Seed Demo Persona"** to populate rich, authentic behavioral principles:
* **Persona A: Senior Clean Architecture Dev** (Strict unit testing, dependency inversion, conventional commits).
* **Persona B: Rust Systems Engineer** (Zero-unsafe invariant, comprehensive docstrings, explicit error propagation).
* **Persona C: Fast Web Prototyper** (Accessible HTML semantics, Tailwind utility hierarchy, defensive prop typing).

---

## 4. Execution Plan & Target Files

| Step | Task | Target Files | Verification Method |
| :--- | :--- | :--- | :--- |
| **1** | Implement In-App Showcase Runner | `desktop_app/showcase.py`, `desktop_app/tabs/home.py` | Unit tests + GUI smoke test |
| **2** | Wire Actuator to Replicate Tab | `desktop_app/tabs/replicate.py`, `desktop_app/actuate_worker.py` | E-stop interrupt test |
| **3** | Implement Live LLM Adapter | `desktop_app/llm.py`, `desktop_app/state.py` | Offline fallback + live synthesis test |
| **4** | Connect TTS Voice Synthesis & Mic | `desktop_app/voice_worker.py`, `desktop_app/tabs/home.py` | Audio output + Visor state reaction |
| **5** | Add 1-Click Persona Seeding | `desktop_app/personas.py`, `desktop_app/tabs/memory.py` | Memory table population check |
| **6** | Run Complete Regression Suite | `tests/` | 170+ tests passing (100%) |

---

## 5. Verification Protocol
```bash
# 1. Run unit test suite
python -m unittest discover tests

# 2. Run Desktop App smoke test
python -m unittest tests.test_desktop_app

# 3. Test CLI Grand Showcase
python demo_live_cobot.py --repo .

# 4. Launch Desktop App for visual & audio inspection
python -m desktop_app
```
