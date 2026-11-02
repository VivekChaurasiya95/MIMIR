"""MIMIR desktop presence widgets (Days 8-9)."""

from .visor import (
    PHASE5_LABELS,
    PHASE5_STATES,
    PHASE5_TO_VISOR,
    VISOR_COLORS,
    VISOR_LABELS,
    VISOR_TO_PHASE5,
    VisorController,
    VisorError,
    VisorState,
    create_visor_widget,
    phase5_of,
    run_visor,
    set_phase5_state,
)

__all__ = [
    "PHASE5_LABELS",
    "PHASE5_STATES",
    "PHASE5_TO_VISOR",
    "VISOR_COLORS",
    "VISOR_LABELS",
    "VISOR_TO_PHASE5",
    "VisorController",
    "VisorError",
    "VisorState",
    "create_visor_widget",
    "phase5_of",
    "run_visor",
    "set_phase5_state",
]
