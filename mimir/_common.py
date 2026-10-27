"""Shared validation helpers for MIMIR data contracts (internal).

The public contracts live in :mod:`mimir.principles` and
:mod:`mimir.episodes`; this module holds the small strict-typing helpers they
both use so the contracts stay identical in behavior.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

__all__ = [
    "check_bool",
    "check_float",
    "check_int",
    "check_mapping",
    "check_str",
    "check_str_list",
    "normalize_timestamp",
    "utc_now_iso",
]

_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_MAX_TEXT_LEN = 1_000_000


def utc_now_iso() -> str:
    """Current UTC time in canonical MIMIR form (``YYYY-MM-DDTHH:MM:SSZ``)."""
    return datetime.now(timezone.utc).strftime(_TIMESTAMP_FORMAT)


def normalize_timestamp(value: Any, *, field: str) -> str:
    """Canonicalize an ISO-8601 timestamp to UTC ``...Z`` (seconds precision)."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError(
                f"{field}: naive datetime is ambiguous; use timezone-aware UTC"
            )
        return value.astimezone(timezone.utc).strftime(_TIMESTAMP_FORMAT)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"{field}: expected ISO-8601 timestamp string, "
            f"got {type(value).__name__}"
        )
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(f"{field}: invalid ISO-8601 timestamp: {value!r}") from None
    if parsed.tzinfo is None:
        raise ValueError(f"{field}: timestamp lacks timezone offset: {value!r}")
    return parsed.astimezone(timezone.utc).strftime(_TIMESTAMP_FORMAT)


def check_mapping(
    value: Any,
    *,
    cls: str,
    required: tuple[str, ...],
    optional: tuple[str, ...] = (),
) -> dict:
    """Validate a contract object: must be a dict with exact known keys."""
    if not isinstance(value, dict):
        raise ValueError(f"{cls}: expected an object, got {type(value).__name__}")
    unknown = [k for k in value if k not in required and k not in optional]
    if unknown:
        raise ValueError(f"{cls}: unknown keys: {unknown}")
    missing = [k for k in required if k not in value]
    if missing:
        raise ValueError(f"{cls}: missing required keys: {missing}")
    return value


def check_str(
    value: Any, *, field: str, allow_empty: bool = False, max_len: int = _MAX_TEXT_LEN
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field}: expected string, got {type(value).__name__}")
    if not allow_empty and not value.strip():
        raise ValueError(f"{field}: must be a non-empty string")
    if len(value) > max_len:
        raise ValueError(f"{field}: exceeds max length {max_len}")
    return value


def check_bool(value: Any, *, field: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{field}: expected bool, got {type(value).__name__}")
    return value


def check_int(value: Any, *, field: str, minimum: int = 0) -> int:
    if type(value) is not int:
        raise ValueError(f"{field}: expected int, got {type(value).__name__}")
    if value < minimum:
        raise ValueError(f"{field}: must be >= {minimum}, got {value}")
    return value


def check_float(
    value: Any, *, field: str, minimum: float = 0.0, maximum: float = 1.0
) -> float:
    if type(value) is bool or not isinstance(value, (int, float)):
        raise ValueError(f"{field}: expected number, got {type(value).__name__}")
    result = float(value)
    if not (minimum <= result <= maximum):
        raise ValueError(
            f"{field}: must be within [{minimum}, {maximum}], got {value}"
        )
    return result


def check_str_list(
    value: Any, *, field: str, allow_empty_items: bool = False
) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(
            f"{field}: expected list of strings, got {type(value).__name__}"
        )
    return tuple(
        check_str(item, field=f"{field}[{i}]", allow_empty=allow_empty_items)
        for i, item in enumerate(value)
    )
