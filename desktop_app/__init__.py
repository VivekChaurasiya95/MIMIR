"""MIMIR desktop app: serene PyQt6 shell over the cognitive core."""

from .prefs import DesktopPrefs
from .state import AppState
from .theme import PORCELAIN, TAB_DEFS

__version__ = "0.1.0"

__all__ = ["AppState", "DesktopPrefs", "PORCELAIN", "TAB_DEFS", "__version__"]
