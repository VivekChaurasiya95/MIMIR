"""One-click demo persona seeding (Qt-free).

Populates the Behavioral Memory with rich, authentic principles so a demo
on a fresh machine — without git history — still shows inspectable,
persona-aligned standards. Each persona bundles three principles; seeding
is idempotent (re-seeding overwrites the same deterministic ids).
"""

from __future__ import annotations

from typing import Any

from mimir.principles import (
    BehavioralPrinciple,
    ContextTrigger,
    EpistemicStatus,
)
from mimir.store import PrincipleStore

__all__ = [
    "PERSONA_IDS",
    "PERSONAS",
    "get_persona",
    "get_persona_principles",
    "list_personas",
    "seed_persona",
]

_PERSONA_DEFS: dict[str, dict[str, Any]] = {
    "clean_arch": {
        "title": "Senior Clean Architecture Dev",
        "description": (
            "Strict unit testing, dependency inversion, conventional commits."
        ),
        "principles": (
            {
                "name": "Strict unit testing",
                "category": "testing",
                "scope": "All code changes",
                "invariant": (
                    "Cover every change with unit tests in the same commit."
                ),
                "anti_patterns": (
                    "Merging code without accompanying tests",
                    "Deferring tests to a follow-up commit",
                ),
                "confidence": 0.85,
            },
            {
                "name": "Dependency inversion",
                "category": "architecture",
                "scope": "Module boundaries",
                "invariant": (
                    "Depend on abstractions, never on concrete "
                    "implementations."
                ),
                "anti_patterns": (
                    "Importing concrete infrastructure modules from domain code",
                    "Hard-coding service clients inside business logic",
                ),
                "confidence": 0.80,
            },
            {
                "name": "Conventional commits",
                "category": "workflow",
                "scope": "Git commit workflow",
                "invariant": (
                    "Write conventional commit subjects "
                    "(type: description)."
                ),
                "anti_patterns": (
                    "Committing with vague one-word subjects",
                ),
                "confidence": 0.82,
            },
        ),
    },
    "rust_systems": {
        "title": "Rust Systems Engineer",
        "description": (
            "Zero-unsafe invariant, comprehensive docstrings, explicit "
            "error propagation."
        ),
        "principles": (
            {
                "name": "Zero-unsafe invariant",
                "category": "architecture",
                "scope": "Rust code",
                "invariant": (
                    "Never use `unsafe` blocks; express invariants through "
                    "the type system."
                ),
                "anti_patterns": (
                    "Adding unsafe without a proof of soundness",
                    "Bypassing the borrow checker with raw pointers",
                ),
                "confidence": 0.90,
            },
            {
                "name": "Comprehensive docstrings",
                "category": "documentation",
                "scope": "Public APIs",
                "invariant": (
                    "Document every public item with its purpose, "
                    "arguments, and failure modes."
                ),
                "anti_patterns": (
                    "Shipping public APIs without documentation",
                ),
                "confidence": 0.78,
            },
            {
                "name": "Explicit error propagation",
                "category": "workflow",
                "scope": "Fallible operations",
                "invariant": (
                    "Propagate errors explicitly with context; never "
                    "swallow failures."
                ),
                "anti_patterns": (
                    "Calling unwrap() on production paths",
                    "Silently mapping errors to default values",
                ),
                "confidence": 0.84,
            },
        ),
    },
    "web_prototyper": {
        "title": "Fast Web Prototyper",
        "description": (
            "Accessible HTML semantics, Tailwind utility hierarchy, "
            "defensive prop typing."
        ),
        "principles": (
            {
                "name": "Accessible HTML semantics",
                "category": "style",
                "scope": "Web UI markup",
                "invariant": (
                    "Use semantic HTML landmarks and labels for every "
                    "interactive element."
                ),
                "anti_patterns": (
                    "Div-only click handlers without roles or labels",
                ),
                "confidence": 0.76,
            },
            {
                "name": "Tailwind utility hierarchy",
                "category": "style",
                "scope": "Web UI styling",
                "invariant": (
                    "Compose layout with Tailwind utilities before "
                    "reaching for custom CSS."
                ),
                "anti_patterns": (
                    "Writing bespoke CSS for spacing the utilities cover",
                ),
                "confidence": 0.74,
            },
            {
                "name": "Defensive prop typing",
                "category": "architecture",
                "scope": "Component interfaces",
                "invariant": (
                    "Type every component prop explicitly, with defaults "
                    "for optionals."
                ),
                "anti_patterns": (
                    "Leaving component props untyped",
                    "Required props that should be optional",
                ),
                "confidence": 0.72,
            },
        ),
    },
}

PERSONA_IDS: tuple[str, ...] = tuple(_PERSONA_DEFS)

PERSONAS: dict[str, str] = {
    pid: spec["title"] for pid, spec in _PERSONA_DEFS.items()
}


def _check_persona_id(persona_id: str) -> dict[str, Any]:
    if persona_id not in _PERSONA_DEFS:
        valid = ", ".join(sorted(_PERSONA_DEFS))
        raise ValueError(
            f"persona_id: expected one of ({valid}), got {persona_id!r}"
        )
    return _PERSONA_DEFS[persona_id]


def list_personas() -> list[dict[str, str]]:
    """Summaries (id, title, description) for every demo persona."""
    return [{"id": pid, "title": spec["title"],
             "description": spec["description"]}
            for pid, spec in _PERSONA_DEFS.items()]


def get_persona(persona_id: str) -> dict[str, str]:
    """Summary (id, title, description) for one persona."""
    spec = _check_persona_id(persona_id)
    return {"id": persona_id, "title": spec["title"],
            "description": spec["description"]}


def get_persona_principles(persona_id: str) -> list[BehavioralPrinciple]:
    """Build (without persisting) the persona's principles."""
    spec = _check_persona_id(persona_id)
    principles: list[BehavioralPrinciple] = []
    for index, item in enumerate(spec["principles"], 1):
        principles.append(BehavioralPrinciple(
            id=f"bp_persona_{persona_id}_{index:03d}",
            name=item["name"],
            category=item["category"],
            context_trigger=ContextTrigger(
                scope=item["scope"], language_agnostic=True),
            latent_invariant=item["invariant"],
            anti_patterns=tuple(item["anti_patterns"]),
            epistemic_status=EpistemicStatus.new(
                item["confidence"], supporting=3),
            inspectable_traces=(f"persona_seed:{persona_id}",),
        ))
    return principles


def seed_persona(store: PrincipleStore,
                 persona_id: str) -> list[BehavioralPrinciple]:
    """Persist the persona's principles into ``store`` (idempotent).

    Returns the seeded principles. Re-seeding overwrites the same
    deterministic ids, so the Memory table never accumulates duplicates.
    """
    if not isinstance(store, PrincipleStore):
        raise ValueError(
            "store: expected PrincipleStore, "
            f"got {type(store).__name__}"
        )
    principles = get_persona_principles(persona_id)
    for principle in principles:
        store.save(principle)
    return principles
