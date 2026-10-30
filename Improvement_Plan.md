# MIMIR — Comprehensive Hands-Free Subsystem Improvement Plan
## Transitioning from a Reactive Voice Assistant to an Observation-Driven Autonomous Digital Cobot

**Document Type:** Technical Architecture & Improvement Specification  
**Project:** MIMIR  
**Scope:** Hands-Free Subsystem, Sensory Runtime, Cognitive Integration, Desktop Actuation and Runtime Safety

---

# 1. Executive Summary

The current hands-free subsystem of MIMIR suffers from an architectural disconnect between its observation, conversational, cognitive and physical execution components. Although individual capabilities such as voice capture, Whisper-based speech recognition, LLM inference, text-to-speech and desktop actuation exist, they do not operate as one coordinated autonomous system.

Consequently, MIMIR behaves primarily as a reactive voice-triggered chatbot rather than an observation-driven digital cobot capable of continuously understanding the developer's working environment.

The proposed improvement introduces a unified, event-driven execution architecture that connects all existing subsystems through a central asynchronous runtime. The objective is not to rebuild MIMIR or replace its existing technologies, but to reorganize their responsibilities, eliminate blocking dependencies and establish reliable communication between them.

The improved system will continuously observe relevant developer activity, reconstruct the current development context, identify meaningful patterns, interpret conversational instructions, formulate action proposals and execute authorized operations through a validated desktop-control interface.

The central design principle is:

**Observe continuously → Understand context → Infer intent → Validate authorization → Execute precisely → Verify outcome.**

The improvement focuses on six interconnected areas:

1. Continuous developer observation and incremental structural analysis.
2. Unified event-driven communication and runtime orchestration.
3. Integration of voice interaction with the physical execution layer.
4. Reliable desktop focus management and target-aware actuation.
5. Computational isolation and responsive execution.
6. Independent emergency-stop monitoring and execution safety.

# 2. Existing System Limitations

## 2.1 Telemetry and Observation Disconnect

The existing Sensory Layer primarily relies on static filesystem snapshots and post-facto Git information. This approach provides historical information about a project but does not sufficiently capture the developer's active workflow.

The system cannot reliably identify structural code modifications as they happen, correlate successive changes or recognize interaction patterns associated with ongoing development.

As a result, the Inductive Inference Engine receives incomplete operational context and cannot consistently distinguish between the developer's current activity, previous modifications and potential next actions.

### Required improvement

Replace snapshot-oriented observation with continuous, event-driven monitoring.

The Sensory Layer must operate as a background service that captures relevant filesystem events, incremental AST modifications, Git state changes and consent-based interaction timing information.

The output should be structured telemetry events that can be consumed immediately by the cognitive runtime.

## 2.2 Isolated Conversational Architecture

The current Voice Subsystem successfully performs audio capture, speech-to-text conversion through Whisper and response generation through the existing LLM and TTS pipeline.

However, the conversational runtime remains isolated from the physical control architecture.

A recognized instruction can produce a conversational response without reliably reaching the action-planning and desktop-execution components.

This separation prevents voice interaction from functioning as an integrated control interface.

### Required improvement

Introduce an event-driven communication interface between the Voice Subsystem and the central runtime.

Recognized speech must be converted into structured conversational intents. These intents must be resolved against the current developer state and any pending actions before being forwarded to the appropriate execution component.

## 2.3 Window-Focus Gridlock

The current DesktopActuator encounters focus conflicts when attempting to control external applications while the PyQt6 companion application retains foreground focus.

This results in incorrect input destinations, including blank focus areas and MIMIR's own interface widgets.

The underlying problem is insufficient target-window identification and verification before physical input injection.

### Required improvement

Introduce a dedicated Windows desktop-control interface that explicitly identifies, validates and activates the intended target application before executing an operation.

The actuator must verify foreground-window ownership and abort execution if the intended target cannot be confirmed.

MIMIR's companion interface must remain independent of the application being controlled.

## 2.4 Main-Thread Latency

Computationally expensive tasks, particularly Whisper transcription and LLM inference, currently compete with the primary PyQt6 runtime.

This affects interface responsiveness, cursor movement smoothness and the responsiveness of safety monitoring.

The existing architecture does not provide sufficient isolation between computational workloads, user-interface rendering and physical actuation.

### Required improvement

Separate computational workloads into dedicated workers or processes and establish controlled communication between them.

The UI thread must remain responsible for interface rendering and user interaction rather than performing transcription, inference or blocking desktop operations.

Safety monitoring must operate independently of these computational workloads.

# 3. Proposed Unified Architecture

The recommended solution is an Event-Driven Autonomous Runtime that coordinates the existing MIMIR subsystems.

Rather than introducing direct dependencies between every component, the system will use a central event-dispatching mechanism.

Each subsystem will publish structured events and subscribe only to the events relevant to its responsibilities.

The architecture consists of the following interconnected layers.

## 3.1 Sensory Layer — Continuous Developer Observation

The Sensory Layer will become a persistent background observation service responsible for collecting relevant information from the developer's working environment.

It will contain four primary observation components.

### A. Filesystem Watcher

Implement continuous filesystem monitoring using Python's Watchdog library.

The watcher must identify:

- File creation and deletion.
- File modifications.
- File renaming and movement.
- Changes within monitored project directories.
- Relevant source-code changes.
- Repository-level file activity.

Filesystem events must be debounced and normalized to avoid duplicate processing caused by editor save operations, temporary files and operating-system event duplication.

The watcher should monitor explicitly selected project directories and exclude irrelevant locations such as virtual environments, build directories, dependency folders and generated artifacts.

Every detected change must generate a structured event containing the file identity, event type, timestamp and project context.

### B. Incremental AST Observer

Integrate Tree-sitter to identify structural changes in source code.

Instead of reparsing entire repositories whenever a file changes, the observer should maintain incremental syntax-tree representations for monitored files.

It must identify:

- Added functions and methods.
- Removed functions and methods.
- Modified function bodies.
- Class declarations and structural changes.
- Function signature modifications.
- Import and dependency changes.
- Relevant syntax errors introduced during editing.

The observer should generate structural deltas rather than transmitting entire source files to the inference engine.

The existing mathematical representation of structural change, v_Δ, can be retained as the conceptual representation of these updates.

The observer must also handle incomplete syntax trees because developers frequently work with temporarily invalid code.

### C. Git Observer

Integrate GitPython or controlled Git subprocess operations to maintain repository-level awareness.

The observer should track:

- Active repository.
- Current branch.
- Working-tree status.
- Staged and unstaged modifications.
- Commit creation.
- Branch changes.
- Relevant file-level differences.

Git observation should complement live filesystem monitoring rather than replace it.

Uncommitted changes must remain visible to the developer-state representation.

### D. Interaction Cadence Observer

Introduce consent-based interaction telemetry to identify broad developer interaction patterns.

The observer should capture aggregated information such as:

- Typing activity intervals.
- Editing pauses.
- Correction frequency.
- Interaction bursts.
- Idle periods.
- Mouse movement activity where relevant.

The system should maintain timing statistics rather than storing raw keystrokes, passwords, clipboard contents or unrelated application input.

These observations can contribute to the existing v_cadence representation.

The purpose is to help MIMIR understand interaction patterns, not to create a keylogging system.

## 3.2 Central Event Runtime — Unified Communication Layer

Introduce a central asynchronous event runtime that connects the Sensory Layer, Voice Subsystem, Cognitive Layer, Safety Controller and DesktopActuator.

This is the most important architectural change.

Currently, components depend too heavily on direct synchronous execution. The new architecture should use an event-publishing and event-consumption model.

For the initial implementation, use Python asyncio with bounded queues and a centralized event dispatcher. A distributed message broker is unnecessary unless future workload measurements justify it.

### Event structure

All subsystem communication should follow a consistent event contract.

Each event should contain:

- Unique event identifier.
- Event type.
- Source subsystem.
- Timestamp.
- Relevant project or session identifier.
- Event priority.
- Correlation identifier.
- Structured payload.
- Expiration or validity information where applicable.

Example conceptual event:

    {
        "event_id": "evt_1042",
        "event_type": "AST_DELTA",
        "source": "sensory.ast_observer",
        "timestamp": 1793340000,
        "priority": "normal",
        "correlation_id": "session_01",
        "payload": {
            "file": "runtime/actuator.py",
            "added_functions": ["execute_action"],
            "modified_functions": ["validate_target"],
            "removed_functions": []
        }
    }

### Event categories

The runtime should support the following event families:

**Observation events:** FILE_CREATED, FILE_MODIFIED, FILE_DELETED, AST_DELTA, GIT_CHANGED and CADENCE_UPDATED.

**Cognitive events:** DEVELOPER_STATE_UPDATED, PATTERN_DETECTED, INFERENCE_COMPLETED and ACTION_PROPOSED.

**Conversational events:** VOICE_CAPTURED, TRANSCRIPTION_COMPLETED, INTENT_PARSED and ACTION_CONFIRMATION_RECEIVED.

**Execution events:** ACTION_REQUESTED, ACTION_AUTHORIZED, ACTION_STARTED, ACTION_COMPLETED and ACTION_FAILED.

**Safety events:** E_STOP_TRIGGERED, ACTUATION_CANCELLED, TARGET_VALIDATION_FAILED and SAFETY_STATE_CHANGED.

### Runtime responsibilities

The event runtime must:

1. Accept events from registered subsystems.
2. Validate event schemas.
3. Route events to appropriate consumers.
4. Maintain bounded queues.
5. Support event priorities.
6. Prevent duplicate action execution through idempotency controls.
7. Track action and event correlation.
8. Handle consumer failures without crashing the entire application.
9. Record execution outcomes for debugging and auditing.

High-priority safety events must never wait behind ordinary telemetry or inference work.

## 3.3 Developer State Engine — Continuous Context Reconstruction

Introduce a Developer State Engine between observation and inference.

Its responsibility is to maintain an updated representation of the developer's active working context.

The Sensory Layer should not repeatedly send disconnected observations to the LLM. Instead, events should update a structured developer-state representation.

The state should contain:

- Active project and repository.
- Active source file, when reliably known.
- Recent structural modifications.
- Current Git state.
- Recent interaction patterns.
- Current development activity classification.
- Relevant conversational context.
- Pending action proposals.
- Observation timestamps.
- Confidence and uncertainty information.

The state engine should distinguish between verified observations and inferred conclusions.

For example, detecting modifications to a Python function is an observation. Concluding that the developer is debugging that function is an inference.

This distinction is essential for preventing incorrect assumptions.

The state representation should be maintained in memory with controlled persistence where required.

Frequent updates should modify only affected state fields rather than reconstructing the entire state.

## 3.4 Cognitive Layer — Inductive Inference and Action Planning

The existing Inductive Inference Engine should consume the continuously updated developer state.

Its responsibility is to interpret observations, identify patterns and generate relevant action proposals.

It should not execute desktop operations directly.

The cognitive layer should operate through three responsibilities.

### A. Contextual Inference

Correlate recent filesystem events, AST modifications, Git changes and interaction patterns to identify the developer's likely current activity.

The engine should recognize meaningful relationships between modifications rather than treating every event as an independent instruction.

### B. Action Planning

When a relevant opportunity is identified, generate a structured action proposal.

An action proposal should include:

- Proposed operation.
- Intended target application.
- Intended file or interface element.
- Reason for the proposal.
- Supporting observations.
- Confidence level.
- Required permissions.
- Expiration time.

The system should distinguish between suggestions, low-risk authorized operations and actions requiring explicit confirmation.

### C. Contextual Conversation

The cognitive engine should be able to use the current developer state when responding to voice instructions.

This ensures that conversational responses are grounded in the active working context rather than depending entirely on the latest spoken sentence.

The existing Ollama-based inference implementation can remain.

The improvement concerns runtime integration and context management, not mandatory replacement of the underlying model.

# 4. Unified Voice-to-Action Integration

The existing Whisper STT and TTS capabilities should be retained.

However, the voice pipeline must be separated into audio processing, intent interpretation and execution authorization.

### Proposed processing flow

1. Capture audio.
2. Submit audio to the Whisper worker.
3. Receive the transcription asynchronously.
4. Pass the transcription and relevant conversation context to the intent parser.
5. Produce a structured intent.
6. Resolve the intent against the Developer State Engine and Action Registry.
7. Validate whether a corresponding action exists.
8. Determine whether additional clarification or explicit approval is required.
9. Publish an authorized action request to the execution runtime.
10. Return execution feedback through the existing TTS subsystem.

### Structured intent resolution

A voice instruction such as "Go ahead" must not be interpreted as an unrestricted command to control the desktop.

It must resolve against an existing pending action.

Example:

    {
        "intent": "APPROVE_PENDING_ACTION",
        "target_action_id": "action_104",
        "authorization_type": "explicit_voice_confirmation"
    }

The Action Registry should maintain the state of pending operations, including their target, requested permissions, expiry and execution status.

If no valid pending action exists, MIMIR should ask for clarification instead of guessing.

Confirmation must be bound to a specific action and target. It must not silently authorize unrelated subsequent operations.

Voice processing must also remain available independently of the PyQt6 interface state.

# 5. Desktop Actuation and Window-Focus Correction

The DesktopActuator requires a dedicated target-management and execution interface.

The objective is to eliminate incorrect input destinations and make physical desktop interaction predictable.

### A. Window Identification

Integrate pywin32 to identify and manage Windows application windows.

Use the appropriate Win32 interfaces to:

- Enumerate available windows.
- Retrieve window handles.
- Identify process ownership.
- Retrieve window titles and executable identity.
- Inspect the current foreground window.
- Request target-window activation.

The actuator must not assume that foreground activation requests always succeed. Windows may restrict foreground changes depending on the current input and process state.

### B. Target Validation

Before every sensitive operation, validate:

- Target window handle.
- Target process identity.
- Target application.
- Current foreground ownership.
- Intended operation.
- Action authorization.
- Whether the target has changed since the proposal was created.

If the intended target cannot be verified, execution must be cancelled.

### C. Companion Application Focus Management

The PyQt6 companion should not remain an accidental input destination.

When desktop actuation is approved, the system should:

1. Preserve the companion's current interface state.
2. Resolve the target application.
3. Request target activation.
4. Verify actual foreground focus.
5. Execute the operation only after successful validation.
6. Restore or maintain the companion's interface according to the interaction design.

Hiding or minimizing the companion is not sufficient on its own. Foreground-window verification remains mandatory.

### D. Input Injection

Use a controlled Windows input backend, preferably Win32 SendInput for the Windows-specific execution path.

The existing actuator abstraction should be preserved where practical so the underlying input implementation can be changed without modifying the entire cognitive architecture.

### E. Execution Verification

Every significant operation should have an associated completion condition.

The actuator must verify whether the expected action occurred rather than treating successful input injection as proof of successful execution.

For example, entering text is not sufficient evidence that the intended document was modified.

The system should report completion, failure or uncertainty accurately.

# 6. Asynchronous Runtime and Computational Isolation

The PyQt6 interface must never perform expensive computational operations directly on its main event loop.

Introduce dedicated processing workers and clear ownership of each workload.

### Recommended runtime separation

**Main UI Runtime**

Responsible exclusively for interface rendering, visual feedback, user interaction and UI state updates.

**Observation Runtime**

Responsible for filesystem monitoring, AST updates, Git observation and telemetry aggregation.

**Voice Runtime**

Responsible for audio capture, audio preprocessing, Whisper transcription and TTS coordination.

**Inference Runtime**

Responsible for Ollama requests, contextual inference and action planning.

**Event Runtime**

Responsible for asynchronous message routing, event validation and task scheduling.

**Actuation Runtime**

Responsible for target-window management, authorized keyboard/mouse execution and result verification.

**Safety Runtime**

Responsible for emergency-stop monitoring, cancellation signalling and safety-state management.

### Worker implementation

Use QThread or dedicated Qt workers for appropriate background operations.

Use multiprocessing for computational workloads that could create significant CPU/GPU contention, particularly Whisper and LLM inference.

Use queued Qt signals or thread-safe message interfaces to communicate with the UI.

If asyncio is integrated with Qt through qasync, establish a single clearly defined event-loop ownership model. Avoid independently running competing event loops inside the same UI process.

### Queue management

Every computational queue should be bounded.

The system should:

- Reject or defer excessive workloads.
- Coalesce redundant filesystem events.
- Prioritize safety and execution-control messages.
- Prevent old telemetry from accumulating indefinitely.
- Apply timeouts to inference and action requests.
- Support cancellation of obsolete tasks.

A slow inference response must not prevent the developer from continuing to work.

# 7. Cursor Trajectory and Actuator Responsiveness

The existing cubic Bézier cursor movement implementation should remain, but its execution must be separated from computational inference and UI rendering.

Introduce a dedicated trajectory executor.

The executor should:

- Receive precomputed movement trajectories.
- Use monotonic timestamps.
- Schedule movement samples independently of LLM processing.
- Avoid blocking sleeps on the Qt main thread.
- Support trajectory cancellation.
- Validate target coordinates and screen boundaries.
- Stop immediately when the safety controller invalidates an operation.

The movement system should not attempt to compensate for scheduling delays by executing a burst of stale mouse events.

Instead, it should calculate the appropriate current trajectory position or cancel the movement when timing constraints cannot be met.

Trajectory smoothness should be evaluated through timing deviation, positional deviation and missed-deadline measurements.

# 8. Independent Emergency-Stop and Safety Architecture

The existing E-stop monitoring requirement must be treated as a separate safety responsibility rather than another ordinary background task.

The emergency-stop mechanism must not depend on:

- Whisper transcription.
- Ollama inference.
- The central event queue.
- PyQt6 rendering.
- Ordinary telemetry processing.
- A blocked actuator worker.

Introduce an independent safety controller that can invalidate outstanding actuation commands and prevent new operations from being accepted.

For a genuine strict sub-10-ms safety requirement, use a separately scheduled native controller or appropriate hardware-backed mechanism. A normal Python thread or Windows desktop application cannot provide a hard real-time guarantee.

### Required safety behaviour

1. E-stop activation must immediately transition the system into a safe state.
2. Outstanding actuator commands must be invalidated.
3. New physical operations must be rejected.
4. The actuator must check safety state immediately before execution and at appropriate checkpoints during execution.
5. The system must report the interruption.
6. Resuming operation must require deliberate reauthorization.
7. Safety events must be logged independently of ordinary application logs.

The safety controller must use a fail-closed approach: if authorization, target validation or safety state cannot be established, actuation must not proceed.

# 9. Recommended Technology Stack

The improvement should reuse the current technology wherever possible.

| Component | Recommended technology | Purpose |
|---|---|---|
| Desktop interface | PyQt6 | Existing companion application |
| Async integration | asyncio + qasync | Event-driven orchestration |
| Filesystem observation | Watchdog | Live file monitoring |
| AST analysis | Tree-sitter | Incremental structural changes |
| Git observation | GitPython / subprocess | Repository state |
| Event contracts | Pydantic | Structured validation |
| Background execution | QThread / multiprocessing | Workload isolation |
| Speech recognition | Existing Whisper implementation | STT |
| LLM inference | Existing Ollama implementation | Reasoning and intent interpretation |
| Voice output | Existing TTS implementation | Speech generation |
| Windows integration | pywin32 | Window management |
| Physical input | Win32 SendInput | Keyboard/mouse control |
| Logging | Python logging / structured logging | Operational diagnostics |

No additional distributed infrastructure is required for the initial correction.

Redis, Kafka or external brokers should only be considered if measured throughput, durability or multi-process communication requirements justify them.

# 10. Integration Strategy — Improving the Existing MIMIR

The implementation must preserve the existing project architecture and working components.

The objective is controlled refactoring rather than a complete rewrite.

### Existing components to retain

- Whisper audio capture and transcription.
- Ollama-based inference.
- Existing TTS implementation.
- PyQt6 companion interface.
- Existing DesktopActuator abstraction.
- Cubic Bézier cursor movement.
- Existing Inductive Inference Engine.
- Existing MIMIR security and permission architecture.

### Components requiring refactoring

- Snapshot-based Sensory Layer.
- Synchronous voice-to-action processing.
- Direct blocking calls between subsystems.
- Main-thread computational workloads.
- Desktop target and focus management.
- Actuator cancellation and safety integration.

### New components to introduce

- Background filesystem observation service.
- Incremental AST observer.
- Central asynchronous Event Runtime.
- Developer State Engine.
- Unified Action Registry.
- Target-window validation service.
- Independent Safety Controller.
- Actuator execution verification.

Before modifying existing modules, map their current responsibilities and identify existing interfaces.

Avoid duplicating working functionality. The new runtime should become the coordination layer around the existing components.

# 11. Implementation and Validation Sequence

Given the limited development time, implementation should prioritize the shared architectural foundation and the most consequential operational failures.

**Step 1 — Establish the Event Runtime**

Create the event contract, dispatcher and bounded asynchronous queues. Connect existing subsystems through this interface.

**Step 2 — Implement Continuous Observation**

Integrate Watchdog, Tree-sitter and Git observation. Generate normalized telemetry events and update the Developer State Engine.

**Step 3 — Unify Voice and Action Processing**

Connect Whisper transcription to the intent parser and Action Registry. Resolve spoken confirmations against specific pending actions.

**Step 4 — Repair Desktop Actuation**

Implement target-window discovery, foreground verification, companion-window handling and controlled input injection.

**Step 5 — Isolate Computational Workloads**

Move Whisper and LLM processing out of the UI execution path. Establish controlled worker communication and bounded queues.

**Step 6 — Integrate Independent Safety Controls**

Introduce emergency-stop signalling, actuator cancellation and fail-closed authorization checks.

**Step 7 — Perform Integrated Validation**

Run observation, voice, actuation and safety tests together under normal and computationally stressed conditions.

This sequence is intended to minimize unnecessary modifications and avoid parallel development of components that depend on an unfinished runtime foundation.

# 12. Testing and Acceptance Criteria

The improvement will only be considered complete when the integrated system demonstrates reliable operation.

## Observation Validation

- File modifications are detected without manual refresh.
- Duplicate filesystem notifications do not produce uncontrolled duplicate processing.
- AST changes are identified incrementally.
- Incomplete syntax trees do not crash the observer.
- Git changes are correlated with live filesystem activity.
- Cadence telemetry is generated without collecting raw keystrokes.

## Cognitive Validation

- Developer State updates when new observations arrive.
- Verified observations remain distinguishable from inferred conclusions.
- The inference engine receives current rather than stale context.
- Action proposals contain a valid target and supporting context.
- Low-confidence inferences do not trigger unauthorized operations.

## Voice Validation

- Whisper transcription does not freeze the companion interface.
- Voice intents are published through the central runtime.
- Spoken confirmations resolve against the correct pending action.
- Ambiguous instructions trigger clarification.
- Expired or already executed actions cannot be approved again.

## Desktop Validation

- The actuator identifies the intended application.
- MIMIR's own interface does not accidentally receive external input.
- Foreground focus is verified before execution.
- Failed target activation causes a safe abort.
- Executed actions are independently verified.

## Performance Validation

Measure:

- Filesystem event detection latency.
- AST update latency.
- Event queue waiting time.
- Developer State update latency.
- Voice intent resolution latency.
- UI responsiveness.
- Actuator trajectory timing deviation.
- Worker resource consumption.
- Emergency-stop response time.

Record p50, p95 and p99 latency wherever appropriate, alongside maximum observed latency.

The emergency-stop requirement must be independently tested under CPU/GPU saturation and other worst-case operating conditions.

## Safety Validation

- E-stop works independently of LLM and Whisper execution.
- Pending actuator commands are cancelled.
- No new operation starts while the system is in a stopped state.
- Failed authorization prevents physical execution.
- Target changes invalidate previously prepared operations.
- Recovery requires deliberate reauthorization.

# 13. Expected Outcome

After implementing the proposed improvements, MIMIR will no longer depend exclusively on explicit voice instructions to understand its working environment.

It will continuously observe relevant development activity, maintain an updated representation of the developer's current state and use that information to support contextual inference.

Voice interaction will become one of several input channels rather than the central dependency of the entire system.

The cognitive runtime will be able to generate contextual action proposals, while the authorization layer will determine whether those proposals can be executed.

The DesktopActuator will operate through verified target windows, and computational workloads will remain isolated from the interface and safety-critical execution paths.

Most importantly, MIMIR will maintain a clear distinction between observing, inferring, proposing and executing.

This distinction will make its autonomous behaviour more predictable, controllable and reliable.

# 14. Final Architectural Objective

The purpose of this improvement is not to make MIMIR a faster chatbot or a more sophisticated voice-controlled automation tool.

The objective is to establish an integrated digital cobot that understands the developer's ongoing work through continuous observation and can interact with the working environment through a controlled, permission-aware execution architecture.

Its operational model must evolve from:

**Voice Input → LLM Response → Desktop Action**

to:

**Continuous Observation → Context Reconstruction → Inductive Inference → Action Planning → Authorization → Physical Execution → Outcome Verification**

The existing MIMIR capabilities provide the foundation. The proposed Event-Driven Autonomous Runtime provides the missing coordination architecture.

**Final Design Principle: MIMIR must observe independently, understand context continuously, communicate naturally and execute precisely — without sacrificing developer control or operational safety.**