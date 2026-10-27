"""MIMIR: observation-driven behavioral intelligence.

MVP first boundary (observe, learn, infer):
- :mod:`mimir.episodes` -- BehavioralEpisode data contract (MVP Day 1)
- :mod:`mimir.principles` -- BehavioralPrinciple data contract (MVP Day 1)
- :mod:`mimir.telemetry` -- redacted git observation bridge (MVP Day 2)
- :mod:`mimir.detective` -- inductive inference engine (MVP Day 3)
- :mod:`mimir.store` -- persistent principle memory (Session A, Days 4-5)
- :mod:`mimir.actor` -- replication & steering engine (Session B, Day 6)
- :mod:`mimir.actuator` -- compliant desktop actuator (Day 7)
- :mod:`mimir.voice` -- voice coordination engine (Days 8-9)
- :mod:`mimir.ui.visor` -- expressive visor avatar (Days 8-9)
"""

from .actuator import (
    ActuatorError,
    CobotInterruptedError,
    CursorPoint,
    DesktopActuator,
    ExecutionPlan,
    plan_keystroke_delays,
    plan_trajectory,
    split_command_line,
)
from .actor import (
    ActorError,
    ReplicationActor,
    ReplicationRequest,
    ReplicationResult,
    build_baseline_prompt,
    check_compliance,
)
from .detective import (
    ClusteringResult,
    DetectiveError,
    EpisodeCluster,
    LLMError,
    LLMOutputError,
    build_inference_prompt,
    cluster_episodes,
    infer_from_cluster,
    infer_from_cluster_with_llm,
    infer_principles,
)
from .episodes import (
    BehavioralEpisode,
    EpisodeContext,
    EpisodeOutcome,
    FileTransition,
)
from .principles import (
    BehavioralPrinciple,
    ContextTrigger,
    EpistemicStatus,
)
from .store import (
    EmbeddingError,
    HashEmbeddingProvider,
    PrincipleNotFoundError,
    PrincipleStore,
    StoreError,
)
from .telemetry import (
    NotARepositoryError,
    TelemetryBoundaryError,
    TelemetryError,
    observe_git_repo,
    redact_text,
)
from .ui import (
    VISOR_COLORS,
    VisorController,
    VisorError,
    VisorState,
)
from .voice import VoiceCoordinator, VoiceError

__version__ = "0.1.0"

__all__ = [
    "ActorError",
    "ActuatorError",
    "BehavioralEpisode",
    "BehavioralPrinciple",
    "ClusteringResult",
    "CobotInterruptedError",
    "ContextTrigger",
    "CursorPoint",
    "DesktopActuator",
    "DetectiveError",
    "EmbeddingError",
    "EpistemicStatus",
    "EpisodeCluster",
    "EpisodeContext",
    "EpisodeOutcome",
    "ExecutionPlan",
    "FileTransition",
    "HashEmbeddingProvider",
    "LLMError",
    "LLMOutputError",
    "NotARepositoryError",
    "PrincipleNotFoundError",
    "PrincipleStore",
    "ReplicationActor",
    "ReplicationRequest",
    "ReplicationResult",
    "StoreError",
    "TelemetryBoundaryError",
    "TelemetryError",
    "VISOR_COLORS",
    "VisorController",
    "VisorError",
    "VisorState",
    "VoiceCoordinator",
    "VoiceError",
    "__version__",
    "build_baseline_prompt",
    "build_inference_prompt",
    "check_compliance",
    "cluster_episodes",
    "infer_from_cluster",
    "infer_from_cluster_with_llm",
    "infer_principles",
    "observe_git_repo",
    "plan_keystroke_delays",
    "plan_trajectory",
    "redact_text",
    "split_command_line",
]
