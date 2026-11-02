"""Vector-embedded Principle Bank (MIMIR 3.0 Pillar 1 memory).

A Gen 2.5 facade over the durable :class:`~mimir.store.PrincipleStore`:
mined invariants persist as :class:`~mimir.principles.BehavioralPrinciple`
rows with float32 vector blobs, toggle state lives in a side table, and
every applied/rolled-back actuation is appended to an audit ledger — all
inside the single ``mimir_memory.db`` SQLite file.

Embeddings prefer Ollama's local ``/api/embeddings`` endpoint and fall
back to the deterministic offline :class:`HashEmbeddingProvider`, so the
bank works with zero network and zero downloads.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from ._common import utc_now_iso
from .principles import (
    BehavioralPrinciple,
    ContextTrigger,
    EpistemicStatus,
)
from .store import (
    EmbeddingError,
    EmbeddingProvider,
    HashEmbeddingProvider,
    PrincipleStore,
)

__all__ = [
    "AuditRecord",
    "OllamaEmbeddingProvider",
    "PrincipleBank",
    "resolve_embedding_provider",
]

_DEFAULT_ENDPOINT = "http://127.0.0.1:11434"
_DEFAULT_EMBED_MODEL = "nomic-embed-text"

_LOOPBACK_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

_RULE_STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS rule_state (
    rule_id TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1
)
"""
_AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'ok'
)
"""


class OllamaEmbeddingProvider:
    """Dense embeddings from the loopback Ollama daemon.

    Implements the :class:`EmbeddingProvider` protocol (``dimension`` +
    ``embed_texts``). Dimension is discovered with one probe call at
    construction; any failure raises :class:`EmbeddingError` so callers
    can fall back to hashing.
    """

    def __init__(
        self,
        model: str = _DEFAULT_EMBED_MODEL,
        endpoint: str = _DEFAULT_ENDPOINT,
        timeout_s: float = 10.0,
    ) -> None:
        self._model = model
        self._endpoint = endpoint.rstrip("/")
        self._timeout_s = timeout_s
        probe = self._embed_batch(["mimir dimension probe"])
        self._dimension = len(probe[0])

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model(self) -> str:
        return self._model

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        if not isinstance(texts, (list, tuple)):
            raise ValueError("texts: expected a list of strings")
        return self._embed_batch(list(texts))

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            payload = {"model": self._model, "prompt": text}
            request = urllib.request.Request(
                self._endpoint + "/api/embeddings",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with _LOOPBACK_OPENER.open(
                    request, timeout=self._timeout_s
                ) as response:
                    body = json.loads(
                        response.read().decode("utf-8", errors="replace")
                    )
                vector = body.get("embedding", [])
                if not vector or not all(
                    isinstance(v, (int, float)) for v in vector
                ):
                    raise EmbeddingError(
                        "Ollama embeddings: malformed vector in response"
                    )
                vectors.append([float(v) for v in vector])
            except EmbeddingError:
                raise
            except Exception as exc:
                raise EmbeddingError(
                    f"Ollama embeddings unavailable ({exc})"
                ) from exc
        return vectors


def resolve_embedding_provider(
    endpoint: str = _DEFAULT_ENDPOINT,
    model: str = _DEFAULT_EMBED_MODEL,
    fallback_dim: int = 256,
) -> tuple[EmbeddingProvider, str]:
    """Return ``(provider, source)`` preferring Ollama, else hash fallback.

    ``source`` is ``"ollama:<model>"`` or ``"hash-<dim>d"`` for honest UI
    badges. Never raises for backend reasons.
    """
    try:
        provider = OllamaEmbeddingProvider(
            model=model, endpoint=endpoint, timeout_s=5.0
        )
        return (provider, f"ollama:{model}")
    except Exception:
        return (HashEmbeddingProvider(dim=fallback_dim), f"hash-{fallback_dim}d")


@dataclass(frozen=True)
class AuditRecord:
    """One audit ledger row with attribute access for dashboard views."""

    id: str
    timestamp: str
    actor: str
    action_type: str
    target_file: str
    details: str
    status: str


class PrincipleBank:
    """Steerable rule library + audit ledger over one SQLite file.

    Opens the shared :class:`PrincipleStore` for principle rows/vectors
    and a second connection for toggle state and the audit ledger, so no
    private store internals are touched.
    """

    def __init__(
        self,
        db_path: str = "mimir_memory.db",
        *,
        embedder: Optional[EmbeddingProvider] = None,
        embedding_source: str = "",
    ) -> None:
        if not isinstance(db_path, str) or not db_path.strip():
            raise ValueError("db_path: expected a non-empty path string")
        self._db_path = db_path
        self._store = PrincipleStore(db_path, embedder=embedder)
        if embedder is None:
            self._embedding_source = "hash-256d"
        else:
            self._embedding_source = embedding_source or "custom"
        self._meta = sqlite3.connect(db_path)
        try:
            self._meta.execute(_RULE_STATE_SCHEMA)
            self._meta.execute(_AUDIT_SCHEMA)
            self._meta.commit()
        except Exception:
            self._meta.close()
            self._store.close()
            raise

    @property
    def db_path(self) -> str:
        return self._db_path

    @property
    def embedding_source(self) -> str:
        return self._embedding_source

    def close(self) -> None:
        try:
            self._meta.close()
        finally:
            self._store.close()

    def __enter__(self) -> "PrincipleBank":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- rules ------------------------------------------------------
    def add_rule(
        self,
        invariant: str,
        anti_pattern: str = "",
        context_trigger: str = "general",
        category: str = "Style",
        confidence: float = 0.7,
        name: str = "",
    ) -> BehavioralPrinciple:
        """Persist one mined invariant as a steerable, enabled rule."""
        if not isinstance(invariant, str) or not invariant.strip():
            raise ValueError("invariant: expected a non-empty string")
        digest = hashlib.sha1(invariant.strip().encode("utf-8")).hexdigest()[:10]
        stamp = utc_now_iso().replace(":", "").replace("-", "")
        principle = BehavioralPrinciple(
            id=f"bp_mined_{stamp}_{digest}",
            name=name.strip() or invariant.strip()[:80],
            category=category.strip() or "Style",
            context_trigger=ContextTrigger(
                scope=context_trigger.strip() or "general",
                language_agnostic=True,
            ),
            latent_invariant=invariant.strip(),
            anti_patterns=[anti_pattern.strip()] if anti_pattern.strip() else [],
            epistemic_status=EpistemicStatus.new(
                min(1.0, max(0.0, float(confidence))), supporting=1
            ),
            inspectable_traces=["gen2.5-miner"],
        )
        self._store.save(principle)
        self._meta.execute(
            "INSERT OR IGNORE INTO rule_state (rule_id, enabled) VALUES (?, 1)",
            (principle.id,),
        )
        self._meta.commit()
        return principle

    def list_rules(
        self, *, enabled_only: bool = False
    ) -> list[tuple[BehavioralPrinciple, bool]]:
        """Return ``(principle, enabled)`` pairs, newest toggle state applied."""
        principles = self._store.list_all()
        states = {
            row[0]: bool(row[1])
            for row in self._meta.execute("SELECT rule_id, enabled FROM rule_state")
        }
        result = [
            (principle, states.get(principle.id, True)) for principle in principles
        ]
        if enabled_only:
            result = [(p, e) for (p, e) in result if e]
        return result

    def set_enabled(self, rule_id: str, enabled: bool) -> None:
        """Flip a rule switch; takes effect on the next generation."""
        self._meta.execute(
            "INSERT INTO rule_state (rule_id, enabled) VALUES (?, ?) "
            "ON CONFLICT(rule_id) DO UPDATE SET enabled=excluded.enabled",
            (rule_id, 1 if enabled else 0),
        )
        self._meta.commit()

    def search(
        self,
        task: str,
        *,
        top_k: int = 5,
        min_confidence: float = 0.0,
        enabled_only: bool = True,
    ) -> list[BehavioralPrinciple]:
        """Cosine k-NN over the bank, honoring rule toggle state."""
        hits = self._store.query_relevant(
            task, top_k=top_k, min_confidence=min_confidence
        )
        if not enabled_only:
            return hits
        states = {
            row[0]: bool(row[1])
            for row in self._meta.execute("SELECT rule_id, enabled FROM rule_state")
        }
        return [p for p in hits if states.get(p.id, True)]

    # -- audit ledger -----------------------------------------------
    def log_action(
        self,
        actor: str,
        action: str,
        target: str,
        detail: str = "",
        status: str = "ok",
    ) -> int:
        """Append one tamper-evident ledger row; returns its row id."""
        cursor = self._meta.execute(
            "INSERT INTO audit_log (ts, actor, action, target, detail, status)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                utc_now_iso(),
                actor[:120],
                action[:120],
                target[:400],
                detail[:4000],
                status[:40],
            ),
        )
        self._meta.commit()
        return int(cursor.lastrowid or 0)

    def upsert_principles(self, rules: Sequence[dict[str, Any]]) -> int:
        """Insert mined rule dicts, skipping empties and known invariants.

        Returns the count of newly added rules.
        """
        known = {p.latent_invariant for p, _ in self.list_rules()}
        added = 0
        for item in rules:
            if not isinstance(item, dict):
                continue
            invariant = str(item.get("invariant", "")).strip()
            if not invariant or invariant in known:
                continue
            try:
                confidence = float(item.get("confidence", 0.7))
            except (TypeError, ValueError):
                confidence = 0.7
            self.add_rule(
                invariant,
                str(item.get("anti_pattern", "")),
                str(item.get("context_trigger", "general")),
                str(item.get("category", "Style")),
                confidence,
            )
            known.add(invariant)
            added += 1
        return added

    def list_actions(self, *, limit: int = 100) -> list[AuditRecord]:
        """Newest-first audit records for the ledger UI."""
        rows = self._meta.execute(
            "SELECT id, ts, actor, action, target, detail, status"
            " FROM audit_log ORDER BY id DESC LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()
        return [
            AuditRecord(
                id=str(row[0]),
                timestamp=row[1],
                actor=row[2],
                action_type=row[3],
                target_file=row[4],
                details=row[5],
                status=row[6],
            )
            for row in rows
        ]
