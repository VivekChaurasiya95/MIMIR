"""Persistent behavioral memory: principle store with semantic retrieval.

Session A (MVP Days 4 & 5). Persists inferred
:class:`~mimir.principles.BehavioralPrinciple` objects in a local SQLite
database with dense embedding blobs, retrieves the most relevant principles
for a novel task via cosine k-NN, and applies Day 4 confidence updates
(reinforcement vs. contradiction) with evidence-appended traces.

Embeddings come from a pluggable :class:`EmbeddingProvider`. The default
:class:`HashEmbeddingProvider` is zero-dependency, deterministic, and
offline; any API-backed provider implementing the same two members can be
substituted without changing the store.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import struct
from pathlib import Path
from typing import Protocol, Sequence

from ._common import check_float, check_int, check_str, utc_now_iso
from .principles import BehavioralPrinciple

__all__ = [
    "EmbeddingError",
    "EmbeddingProvider",
    "HashEmbeddingProvider",
    "PrincipleNotFoundError",
    "PrincipleStore",
    "StoreError",
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS principles (
    id TEXT PRIMARY KEY,
    principle_json TEXT NOT NULL,
    embedding BLOB NOT NULL,
    confidence REAL NOT NULL,
    last_applied_at TEXT
)
"""
_CONFIDENCE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_principles_confidence
ON principles (confidence)
"""
_TOKEN_RE = re.compile(r"[a-z0-9]+")


class StoreError(Exception):
    """Base error for principle-store failures."""


class PrincipleNotFoundError(StoreError, KeyError):
    """Raised when a principle id is absent from the store."""


class EmbeddingError(StoreError, ValueError):
    """Raised when an embedding provider fails or misbehaves."""


class EmbeddingProvider(Protocol):
    """Structural contract for pluggable embedding backends."""

    @property
    def dimension(self) -> int:
        """Fixed vector width produced by :meth:`embed_texts`."""
        ...

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed each text; returns one ``dimension``-wide vector per text."""
        ...


def _check_provider(provider: EmbeddingProvider) -> EmbeddingProvider:
    dimension = getattr(provider, "dimension", None)
    if type(dimension) is not int or dimension < 1:
        raise EmbeddingError(
            "embedder: dimension must be a positive int, "
            f"got {dimension!r}"
        )
    if not callable(getattr(provider, "embed_texts", None)):
        raise EmbeddingError("embedder: missing callable embed_texts(texts)")
    return provider


class HashEmbeddingProvider:
    """Zero-dependency deterministic embeddings via signed feature hashing.

    Tokens hash into ``dim`` buckets with ±1 signs; the aggregate vector is
    L2-normalized. Shared vocabulary yields high cosine similarity while
    disjoint texts stay near-orthogonal (up to hash collisions). Identical
    inputs always produce identical vectors on every platform.
    """

    def __init__(self, dim: int = 256) -> None:
        self._dim = check_int(dim, field="dim", minimum=1)

    @property
    def dimension(self) -> int:
        return self._dim

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        if not isinstance(texts, (list, tuple)):
            raise ValueError("texts: expected a list of strings")
        return [self._embed_one(text, index) for index, text in enumerate(texts)]

    def _embed_one(self, text: str, index: int) -> list[float]:
        if not isinstance(text, str):
            raise ValueError(
                f"texts[{index}]: expected string, got {type(text).__name__}"
            )
        tokens = _TOKEN_RE.findall(text.lower()) or ["__empty__"]
        vector = [0.0] * self._dim
        for token in tokens:
            digest = hashlib.sha1(token.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "little") % self._dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[bucket] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:  # unreachable via public API, guarded for safety
            return vector
        return [value / norm for value in vector]


def _embed_checked(
    provider: EmbeddingProvider, texts: list[str]
) -> list[list[float]]:
    try:
        vectors = provider.embed_texts(texts)
    except EmbeddingError:
        raise
    except Exception as exc:
        raise EmbeddingError(f"embedding provider failed: {exc}") from exc
    if not isinstance(vectors, list) or len(vectors) != len(texts):
        raise EmbeddingError(
            "embedder: expected one vector per input text, "
            f"got {type(vectors).__name__}"
        )
    checked: list[list[float]] = []
    for index, vector in enumerate(vectors):
        if (
            not isinstance(vector, (list, tuple))
            or len(vector) != provider.dimension
            or any(
                type(value) is bool
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in vector
            )
        ):
            raise EmbeddingError(
                f"embedder: texts[{index}] must be "
                f"{provider.dimension} finite floats"
            )
        checked.append([float(value) for value in vector])
    return checked


def _pack_vector(vector: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def _unpack_vector(blob: bytes, *, expected_dim: int) -> list[float]:
    if len(blob) != 4 * expected_dim:
        raise StoreError(
            "embedding dimension mismatch: store holds "
            f"{len(blob) // 4}-dim vectors, provider yields {expected_dim}-dim; "
            "reopen with the original provider or rebuild the store"
        )
    return list(struct.unpack(f"<{expected_dim}f", bytes(blob)))


def _cosine(first: Sequence[float], second: Sequence[float]) -> float:
    dot = sum(a * b for a, b in zip(first, second))
    first_norm = math.sqrt(sum(a * a for a in first))
    second_norm = math.sqrt(sum(b * b for b in second))
    if first_norm == 0.0 or second_norm == 0.0:
        return 0.0
    return dot / (first_norm * second_norm)


def _principle_text(principle: BehavioralPrinciple) -> str:
    return (
        f"{principle.context_trigger.scope}\n{principle.latent_invariant}"
    )


class PrincipleStore:
    """Durable, queryable subconscious for behavioral principles.

    Backed by one SQLite file (or ``\":memory:\"``). Embeddings cover the
    principle's trigger scope plus latent invariant. Single-connection and
    not thread-safe (MVP scope); use as a context manager or call
    :meth:`close` to release file locks.
    """

    def __init__(
        self,
        path: str | Path = ":memory:",
        *,
        embedder: EmbeddingProvider | None = None,
    ) -> None:
        self._provider = _check_provider(
            embedder if embedder is not None else HashEmbeddingProvider()
        )
        self._path = str(path)
        try:
            self._conn = sqlite3.connect(self._path)
            self._conn.execute(_SCHEMA)
            self._conn.execute(_CONFIDENCE_INDEX)
            self._conn.commit()
        except sqlite3.Error as exc:
            raise StoreError(
                f"cannot open principle store at {path}: {exc}"
            ) from None

    @property
    def path(self) -> str:
        return self._path

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "PrincipleStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def save(self, principle: BehavioralPrinciple) -> None:
        """Insert or replace one principle (re-embedding scope+invariant).

        Overwriting preserves the existing ``last_applied_at`` stamp:
        re-saving refreshed knowledge must not erase application history.
        """
        if not isinstance(principle, BehavioralPrinciple):
            raise ValueError(
                "principle: expected BehavioralPrinciple, "
                f"got {type(principle).__name__}"
            )
        payload = json.dumps(principle.to_dict())
        blob = _pack_vector(
            _embed_checked(self._provider, [_principle_text(principle)])[0]
        )
        try:
            row = self._conn.execute(
                "SELECT embedding FROM principles LIMIT 1"
            ).fetchone()
            if row is not None and len(row[0]) != len(blob):
                raise StoreError(
                    "embedding dimension mismatch: store holds "
                    f"{len(row[0]) // 4}-dim vectors, provider yields "
                    f"{len(blob) // 4}-dim; reopen with the original "
                    "provider or rebuild the store"
                )
            self._conn.execute(
                "INSERT INTO principles "
                "(id, principle_json, embedding, confidence, last_applied_at)"
                " VALUES (?, ?, ?, ?, NULL)"
                " ON CONFLICT(id) DO UPDATE SET"
                " principle_json = excluded.principle_json,"
                " embedding = excluded.embedding,"
                " confidence = excluded.confidence",
                (
                    principle.id,
                    payload,
                    blob,
                    principle.epistemic_status.confidence_score,
                ),
            )
            self._conn.commit()
        except sqlite3.Error as exc:
            raise StoreError(f"save failed for {principle.id!r}: {exc}") from None

    def query_relevant(
        self,
        task_context: str,
        top_k: int = 3,
        min_confidence: float = 0.70,
    ) -> list[BehavioralPrinciple]:
        """Return up to ``top_k`` principles relevant to ``task_context``.

        Candidates below ``min_confidence`` are filtered before cosine
        ranking; ties break by principle id. Returned principles are
        stamped with the current UTC ``last_applied_at`` (retrieval is
        application), so this read performs a write.
        """
        task_context = check_str(task_context, field="task_context")
        top_k = check_int(top_k, field="top_k", minimum=1)
        min_confidence = check_float(min_confidence, field="min_confidence")
        query_vector = _embed_checked(self._provider, [task_context])[0]
        try:
            rows = self._conn.execute(
                "SELECT id, principle_json, embedding"
                " FROM principles WHERE confidence >= ?",
                (min_confidence,),
            ).fetchall()
        except sqlite3.Error as exc:
            raise StoreError(f"query failed: {exc}") from None
        scored = [
            (
                _cosine(
                    query_vector,
                    _unpack_vector(
                        row[2], expected_dim=self._provider.dimension
                    ),
                ),
                row[0],
                row[1],
            )
            for row in rows
        ]
        scored.sort(key=lambda item: (-item[0], item[1]))
        winners = scored[:top_k]
        if not winners:
            return []
        now = utc_now_iso()
        try:
            self._conn.execute(
                f"UPDATE principles SET last_applied_at = ? WHERE id IN "
                f"({','.join('?' for _ in winners)})",
                (now, *[item[1] for item in winners]),
            )
            self._conn.commit()
        except sqlite3.Error as exc:
            raise StoreError(f"query stamp failed: {exc}") from None
        return [
            BehavioralPrinciple.from_dict(json.loads(payload))
            for _, _, payload in winners
        ]

    def update_confidence(
        self, principle_id: str, delta: float, new_evidence_id: str
    ) -> None:
        """Apply Day 4 Bayesian-style evidence to one principle.

        Positive ``delta`` reinforces (``supporting_episodes_count + 1``),
        negative ``delta`` contradicts (``contradictions_count + 1``), and
        the confidence score clamps into [0, 1]. The evidence id appends to
        ``inspectable_traces`` (once) and ``last_validated`` refreshes.
        """
        principle_id = check_str(principle_id, field="principle_id")
        if (
            type(delta) is bool
            or not isinstance(delta, (int, float))
            or not math.isfinite(delta)
        ):
            raise ValueError(
                f"delta: expected a finite number, got {delta!r}"
            )
        new_evidence_id = check_str(new_evidence_id, field="new_evidence_id")
        try:
            row = self._conn.execute(
                "SELECT principle_json FROM principles WHERE id = ?",
                (principle_id,),
            ).fetchone()
        except sqlite3.Error as exc:
            raise StoreError(f"update failed for {principle_id!r}: {exc}") from None
        if row is None:
            raise PrincipleNotFoundError(
                f"unknown principle id: {principle_id!r}"
            )
        data = json.loads(row[0])
        status = data["epistemic_status"]
        status["confidence_score"] = min(
            max(status["confidence_score"] + float(delta), 0.0), 1.0
        )
        if delta > 0:
            status["supporting_episodes_count"] += 1
        elif delta < 0:
            status["contradictions_count"] += 1
        status["last_validated"] = utc_now_iso()
        if new_evidence_id not in data["inspectable_traces"]:
            data["inspectable_traces"].append(new_evidence_id)
        updated = BehavioralPrinciple.from_dict(data)  # revalidate contract
        try:
            self._conn.execute(
                "UPDATE principles SET principle_json = ?, confidence = ?"
                " WHERE id = ?",
                (
                    json.dumps(updated.to_dict()),
                    updated.epistemic_status.confidence_score,
                    principle_id,
                ),
            )
            self._conn.commit()
        except sqlite3.Error as exc:
            raise StoreError(f"update failed for {principle_id!r}: {exc}") from None

    def list_all(self) -> list[BehavioralPrinciple]:
        """Return every stored principle, ordered by id (deterministic)."""
        try:
            rows = self._conn.execute(
                "SELECT principle_json FROM principles ORDER BY id"
            ).fetchall()
        except sqlite3.Error as exc:
            raise StoreError(f"list failed: {exc}") from None
        return [BehavioralPrinciple.from_dict(json.loads(row[0])) for row in rows]

    def delete(self, principle_id: str) -> bool:
        """Remove one principle; True when a row existed, else False."""
        principle_id = check_str(principle_id, field="principle_id")
        try:
            cursor = self._conn.execute(
                "DELETE FROM principles WHERE id = ?", (principle_id,)
            )
            self._conn.commit()
        except sqlite3.Error as exc:
            raise StoreError(f"delete failed for {principle_id!r}: {exc}") from None
        return cursor.rowcount > 0

    def last_applied_at(self, principle_id: str) -> str | None:
        """UTC stamp of the last :meth:`query_relevant` hit, else None."""
        principle_id = check_str(principle_id, field="principle_id")
        try:
            row = self._conn.execute(
                "SELECT last_applied_at FROM principles WHERE id = ?",
                (principle_id,),
            ).fetchone()
        except sqlite3.Error as exc:
            raise StoreError(f"lookup failed for {principle_id!r}: {exc}") from None
        if row is None:
            raise PrincipleNotFoundError(
                f"unknown principle id: {principle_id!r}"
            )
        return row[0]
