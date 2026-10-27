# MIMIR: Final Development Blueprint & Implementation Specification
> **Phase III: The Embodied Digital Cobot (Days 7 through 10)**  
> *From Cognitive Brain to Physical Desktop Actuation, Voice, and Visor Presence*

---

## 1. Executive Status: The Entire Cognitive Brain is Complete

The complete cognitive learning engine of MIMIR is built and verified with **83 out of 83 unit tests passing (100%)**:

```
========================= COMPLETED COGNITIVE CORE =========================
[Day 1] Data Contracts     : mimir/episodes.py, mimir/principles.py (13 tests) ✅
[Day 2] Observation        : mimir/telemetry.py (12 tests)                  ✅
[Day 3] Inductive Engine   : mimir/detective.py (23 tests)                  ✅
[Day 4-5] Vector Memory    : mimir/store.py (17 tests)                      ✅
[Day 6] Replication Actor  : mimir/actor.py (18 tests)                      ✅
----------------------------------------------------------------------------
Total Cognitive Tests      : 83 / 83 Unit Tests Passing (100%)              ✅
============================================================================
```

### The Final Frontier (Days 7–10)
All remaining work focuses on **Embodiment**: giving MIMIR physical hands, natural speech, an expressive desktop presence, and an end-to-end live demonstration.

```
                      COGNITIVE CORE (DONE)
            [Observe ──► Infer ──► Remember ──► Replicate]
                               │
                               ▼
            ┌──────────────────────────────────────────────┐
            │        THE DIGITAL COBOT EMBODIMENT          │
            ├──────────────────────────────────────────────┤
            │ Day 7   : Compliant Desktop Actuator         │
            │           (Smooth cursor, typing, killswitch)│
            │ Days 8-9: Voice Bridge & Expressive Visor UI │
            │           (Whisper/TTS + 6-State Avatar)     │
            │ Day 10  : Grand Live Showcase & Unified CLI  │
            │           (End-to-end interactive Cobot demo)│
            └──────────────────────────────────────────────┘
```

---

## 2. Day 7: Physical Compliant Desktop Actuator (`mimir/actuator.py`)

### Purpose
Give MIMIR physical hands to interact directly with the operating system, engineered with **Gen 2.5 robotic compliance** (smooth kinematic curves, human typing cadence, and instant yield on human touch).

### Technical Specifications

```python
"""mimir/actuator.py structural specification."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Optional, Sequence, Tuple
import threading
import time

@dataclass(frozen=True)
class CursorPoint:
    x: int
    y: int

@dataclass(frozen=True)
class ExecutionPlan:
    """A series of atomic actions the cobot will execute."""
    actions: tuple[str, ...]
    target_window: Optional[str] = None
    terminal_command: Optional[str] = None

class ActuatorError(Exception):
    """Base error for physical actuation failures."""

class CobotInterruptedError(ActuatorError):
    """Raised immediately when the human touches the mouse or hits ESC."""

class DesktopActuator:
    """Kinematically smooth and backdrivable OS actuator."""

    def __init__(self, human_override_callback: Optional[Callable[[], None]] = None) -> None:
        self.human_override_callback = human_override_callback
        self._interrupted = threading.Event()
        self._arm_failsafe()

    def _arm_failsafe(self) -> None:
        """Hooks global pynput/keyboard listeners for ESC and mouse displacement."""
        ...

    def move_cursor_smooth(
        self,
        target: CursorPoint,
        duration_sec: float = 0.6,
        steps: int = 30,
    ) -> None:
        """Moves cursor along a smooth cubic Bézier trajectory with natural acceleration.
        
        Checks self._interrupted at every step; raises CobotInterruptedError immediately
        if human hand touches physical mouse.
        """
        ...

    def type_natural(self, text: str, base_wpm: int = 70) -> None:
        """Types text with organic inter-key latency and micro-pauses at punctuation.
        
        Pauses longer at '(', '{', ',', '\n' to mimic human syntactic hesitation.
        """
        ...

    def click_point(self, point: CursorPoint) -> None:
        """Moves smoothly to target point, pauses 80ms, and clicks."""
        ...

    def run_safe_command(
        self,
        command: Sequence[str],
        cwd: Optional[str] = None,
        timeout_sec: float = 30.0,
    ) -> Tuple[int, str, str]:
        """Safely executes subprocess terminal command (e.g., git status/push)."""
        ...

    def abort(self) -> None:
        """Trip the emergency killswitch manually."""
        self._interrupted.set()
```

### Test Requirements (`tests/test_actuator.py`)
1. **Kinematic Interpolation:** Verify Bézier coordinates produce smooth monotonic easing curves.
2. **Interruptibility:** Verify that setting `_interrupted` immediately halts `move_cursor_smooth` and `type_natural` within $<10$ ms.
3. **Typing Rhythm:** Verify syntactic pauses are inserted after punctuation marks.
4. **Safe Subprocess Execution:** Verify non-blocking terminal execution and stdout/stderr capture.

---

## 3. Days 8 & 9: Voice Bridge & Expressive Visor UI

### 3.1 Voice Coordination Engine (`mimir/voice.py`)

#### Purpose
Hands-free coordination so the user can speak naturally to MIMIR without touching textboxes.

#### Technical Specifications
```python
"""mimir/voice.py structural specification."""

from typing import Callable, Optional

class VoiceCoordinator:
    """Manages low-latency Speech-to-Text and Text-to-Speech loops."""

    def __init__(
        self,
        stt_backend: str = "whisper_local",
        tts_backend: str = "edge_tts",
    ) -> None:
        self.stt_backend = stt_backend
        self.tts_backend = tts_backend

    def listen_phrase(self, timeout_sec: float = 5.0) -> Optional[str]:
        """Listens via microphone and returns transcribed text string."""
        ...

    def speak(self, text: str, on_start: Optional[Callable[[], None]] = None) -> None:
        """Synthesizes and plays natural audio voice response."""
        ...

    def parse_intent(self, text: str) -> dict[str, str]:
        """Extracts intent (e.g., 'push_code', 'status_inquiry', 'sleep', 'stop')."""
        ...
```

---

### 3.2 The Expressive Visor Avatar (`mimir/ui/visor.py`)

#### Purpose
A sleek, borderless, floating desktop avatar that lives on your screen and visually signals MIMIR’s inner cognitive state in real time.

#### Visual State Machine:
* 😴 **RESTING (`#555555`):** Subdued, eyes closed, zero telemetry, zero audio listening.
* 🧐 **OBSERVING (`#3B82F6`):** Curious animated eyes, watching background Git commits.
* 🎙️ **LISTENING (`#10B981`):** Reactive audio wave pulsing to human voice.
* 🤔 **THINKING (`#8B5CF6`):** Gear / brain pulse, querying vector memory or inducing rules.
* 🤖 **DRIVING (`#F59E0B`):** Focused amber visor, actively gliding cursor or running terminal tasks.
* ✋ **ALERT / INTERRUPTED (`#EF4444`):** Red alert pulse, emergency stop tripped, awaiting feedback.

#### Technical Implementation:
* Implemented via **PyQt6 / PySide6** or lightweight HTML/Canvas webview as an `always-on-top`, `frameless`, draggable circular pill.
* Global hotkey listener (`Ctrl+Shift+M`) triggers instantaneous toggle between **RESTING** and **OBSERVING/LISTENING**.

---

## 4. Day 10: The Grand Live Showcase & Unified CLI

### 4.1 The Unified CLI (`mimir/cli.py`)
Provides full command-line orchestration:
```bash
# Start the full Digital Cobot desktop presence (Visor + Voice + Actuator)
python -m mimir.cli cobot

# Passive observation mode (observe commits in background)
python -m mimir.cli watch --repo .

# Inspect learned Behavioral Principles
python -m mimir.cli inspect

# Run replication benchmark on a novel problem
python -m mimir.cli replicate --task "Write a resilient payment gateway client"
```

### 4.2 The Live Showcase Demo (`demo_live_cobot.py`)

#### The Golden Script:
1. **Scene 1 (Silent Observation):** MIMIR ingests 10 commit diffs from the repo and confirms 3 extracted principles into its vector store.
2. **Scene 2 (Voice Coordinate):**
   * *User:* *"Hey MIMIR, have you learned how I commit and push my code?"*
   * *MIMIR (Speaks via TTS, Visor pulses green):* *"Yes! I noticed you run tests first, format commits using conventional syntax, and push to feature branches. Would you like me to handle your current changes?"*
3. **Scene 3 (Compliant Driving):**
   * *User:* *"Go ahead."*
   * *MIMIR (Visor turns amber):* The mouse glides across the screen to the terminal window, tests run, the commit is crafted in the user's authentic voice, and `git push` is prepared.
4. **Scene 4 (The E-Stop Live Correction):**
   * *User hits `ESC` or nudges mouse.*
   * *MIMIR instantly halts ($<5$ ms), visor flashes red, and speaks:* *"Stopped. What was about to go wrong?"*
   * *User:* *"Never stage .env files."*
   * *MIMIR:* *"Understood. Invariant registered: forbid staging .env. Adding to your Behavioral Memory."*

---

## 5. Execution Order for Remaining Work

```bash
# Step 1: Implement Day 7 Actuator
# Target: mimir/actuator.py and tests/test_actuator.py

# Step 2: Implement Days 8-9 Voice & Visor
# Target: mimir/voice.py, mimir/ui/visor.py, and tests/test_voice.py

# Step 3: Implement Day 10 CLI & Grand Demo
# Target: mimir/cli.py and demo_live_cobot.py

# Step 4: Run Complete Suite
python -m unittest discover -s tests
```
