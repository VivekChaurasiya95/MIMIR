"""Computational isolation runtime (plan sections 6 + 9, step 5).

The PyQt6 interface must never perform expensive computation on its main
event loop. This module is the Qt-free half of that guarantee; the Qt
half lives in ``desktop_app/observe_worker.py``,
``desktop_app/replicate_worker.py``, ``desktop_app/voice_worker.py``,
``desktop_app/actuate_worker.py``, and ``desktop_app/watch.py``.

Runtime separation (plan section 6)::

    Main UI      interface rendering + interaction only (Qt main thread)
    Observation  filesystem / AST / git / cadence   (SensoryService threads,
                                                     ObserveWorker QThread)
    Voice        capture / Whisper / TTS             (ListenWorker/SpeakWorker
                                                     QThreads, TranscriptionWorker,
                                                     ProcessOffload for Whisper)
    Inference    Ollama / LLM / action planning      (ReplicateWorker QThread
                                                     with per-thread store,
                                                     ProcessOffload for LLM)
    Event        message routing / scheduling        (EventBus bounded queues
                                                     + loop-free pump, LoopOwner)
    Actuation    window mgmt / input / verification  (DriveWorker QThread,
                                                     TrajectoryExecutor)
    Safety       E-stop / cancellation               (failsafe listener thread,
                                                     dedicated SAFETY queue)

Queue management (plan section 6): every :class:`BoundedJobQueue` is
bounded, coalesces redundant work by key, prioritizes safety over
telemetry, expires stale jobs, enforces cooperative timeouts, and
supports cancellation. A slow inference response therefore never
prevents the developer from working, and safety work never waits
behind ordinary telemetry.

Loop ownership: :class:`LoopOwner` enforces a single clearly defined
asyncio/qasync event loop per process. Hosts that need a loop acquire
it by name; a second competing loop is rejected. Qt hosts keep using
the loop-free :meth:`EventBus.pump` bridge; when qasync is desired::

    loop = LoopOwner.acquire_qasync("desktop-app")

No broker is required (plan section 9): threads, ``multiprocessing``,
and in-process queues only. No Redis/Kafka.
"""

from __future__ import annotations

import asyncio
import heapq
import inspect
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

__all__ = [
    "DEFAULT_RUNTIME_QUEUE_SIZES",
    "BoundedJobQueue",
    "IsolationBackpressure",
    "IsolationError",
    "IsolationWorker",
    "JobCancelledError",
    "JobExpiredError",
    "JobHandle",
    "JobPriority",
    "LoopOwner",
    "ProcessOffload",
    "RuntimeIsolation",
    "RuntimeKind",
    "get_runtime_isolation",
    "reset_runtime_isolation",
    "set_runtime_isolation",
]


class IsolationError(Exception):
    """Base error for computational-isolation failures."""


class IsolationBackpressure(IsolationError):
    """Raised when a bounded queue cannot admit a job without evicting
    higher-or-equal priority work (fail-fast backpressure)."""


class JobCancelledError(IsolationError):
    """Raised by cooperative jobs that observe their cancel event."""


class JobExpiredError(IsolationError):
    """Delivered when a job's TTL elapses before it starts."""


class RuntimeKind(str, Enum):
    """The plan section 6 worker runtimes (UI excluded: render-only)."""

    OBSERVATION = "observation"
    VOICE = "voice"
    INFERENCE = "inference"
    EVENT = "event"
    ACTUATION = "actuation"
    SAFETY = "safety"


class JobPriority(int, Enum):
    """Queue priority; lower rank is served first (mirrors EventPriority)."""

    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3


#: Default bounded-queue capacities per runtime. Inference is deliberately
#: small (LLM work is slow; excess must shed fast), while the event queue
#: absorbs telemetry bursts. Safety is never starved: it owns a worker.
DEFAULT_RUNTIME_QUEUE_SIZES: dict[RuntimeKind, int] = {
    RuntimeKind.OBSERVATION: 256,
    RuntimeKind.VOICE: 64,
    RuntimeKind.INFERENCE: 16,
    RuntimeKind.EVENT: 512,
    RuntimeKind.ACTUATION: 32,
    RuntimeKind.SAFETY: 64,
}


def _positive_or_none(value: Any, *, field: str) -> Optional[float]:
    if value is None:
        return None
    if (
        type(value) is bool
        or not isinstance(value, (int, float))
        or not (value == value and value not in (float("inf"), float("-inf")))
        or value <= 0
    ):
        raise ValueError(f"{field}: expected a positive number of seconds")
    return float(value)


@dataclass
class JobHandle:
    """Caller-facing handle for one submitted job."""

    job_id: str
    runtime: RuntimeKind
    priority: JobPriority
    future: Future = field(repr=False)
    cancel_event: threading.Event = field(repr=False)
    _cancel_fn: Any = field(default=None, repr=False)

    def cancel(self) -> bool:
        """Request cancellation (pending jobs drop; running jobs are
        asked cooperatively via :attr:`cancel_event`)."""
        if self._cancel_fn is not None:
            try:
                return bool(self._cancel_fn())
            except Exception:
                return False
        self.cancel_event.set()
        return True

    def done(self) -> bool:
        return self.future.done()

    def result(self, timeout: Optional[float] = None) -> Any:
        return self.future.result(timeout=timeout)


@dataclass
class _Job:
    job_id: str
    runtime: RuntimeKind
    priority: JobPriority
    seq: int
    fn: Callable[..., Any]
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    coalesce_key: str
    timeout_s: Optional[float]
    created_mono: float
    expires_mono: Optional[float]
    cancel_event: threading.Event
    future: Future
    started: bool = False
    settled: bool = False


class BoundedJobQueue:
    """One bounded, prioritized, coalescing job queue.

    - Bounded: ``maxsize`` pending jobs; a full queue evicts the
      lowest-priority stalest job only when the newcomer is strictly
      more urgent, otherwise raises :class:`IsolationBackpressure`.
    - Coalesce: submissions sharing ``coalesce_key`` with a pending job
      replace its payload instead of queueing duplicates (filesystem
      bursts collapse to one refresh).
    - Prioritize: :meth:`pop` always serves the highest priority first,
      so safety/control work preempts telemetry.
    - Expire: jobs carrying ``ttl_s`` that wait too long are dropped
      with :class:`JobExpiredError` instead of running stale.
    """

    def __init__(
        self,
        maxsize: int,
        *,
        runtime: RuntimeKind = RuntimeKind.EVENT,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if type(maxsize) is bool or not isinstance(maxsize, int) \
                or maxsize < 1:
            raise ValueError("maxsize: expected a positive integer")
        if not isinstance(runtime, RuntimeKind):
            raise ValueError(
                f"runtime: expected RuntimeKind, got {type(runtime).__name__}"
            )
        self._maxsize = maxsize
        self._runtime = runtime
        self._clock = clock
        self._cond = threading.Condition()
        self._heap: list[tuple[int, int, str]] = []
        self._jobs: dict[str, _Job] = {}
        self._by_coalesce: dict[str, str] = {}
        self._seq = 0
        self._id_seq = 0
        self._stats = {
            "submitted": 0,
            "coalesced": 0,
            "dropped": 0,
            "rejected": 0,
            "cancelled": 0,
            "expired": 0,
            "completed": 0,
            "failed": 0,
            "timed_out": 0,
        }

    @property
    def maxsize(self) -> int:
        return self._maxsize

    @property
    def runtime(self) -> RuntimeKind:
        return self._runtime

    def pending(self) -> int:
        """Number of jobs waiting (not started, not settled)."""
        with self._cond:
            return sum(
                1 for job in self._jobs.values()
                if not job.started and not job.settled
            )

    def stats(self) -> dict[str, int]:
        """Snapshot of lifecycle counters plus current pending depth."""
        with self._cond:
            snap = dict(self._stats)
            snap["pending"] = sum(
                1 for job in self._jobs.values()
                if not job.started and not job.settled
            )
            snap["maxsize"] = self._maxsize
            return snap

    def submit(
        self,
        fn: Callable[..., Any],
        args: tuple[Any, ...] = (),
        kwargs: Optional[dict[str, Any]] = None,
        *,
        priority: JobPriority = JobPriority.NORMAL,
        coalesce_key: str = "",
        timeout_s: Optional[float] = None,
        ttl_s: Optional[float] = None,
    ) -> JobHandle:
        """Enqueue one callable (never blocks the caller)."""
        if not callable(fn):
            raise ValueError(
                f"fn: expected a callable, got {type(fn).__name__}"
            )
        if not isinstance(args, tuple):
            raise ValueError("args: expected a tuple")
        if kwargs is None:
            kwargs = {}
        if not isinstance(kwargs, dict):
            raise ValueError("kwargs: expected a dict")
        if not isinstance(priority, JobPriority):
            raise ValueError("priority: expected JobPriority")
        if not isinstance(coalesce_key, str):
            raise ValueError("coalesce_key: expected a string")
        timeout = _positive_or_none(timeout_s, field="timeout_s")
        ttl = _positive_or_none(ttl_s, field="ttl_s")
        now = self._clock()
        with self._cond:
            if coalesce_key:
                existing_id = self._by_coalesce.get(coalesce_key)
                if existing_id is not None:
                    existing = self._jobs.get(existing_id)
                    if (existing is not None and not existing.started
                            and not existing.settled):
                        existing.fn = fn
                        existing.args = tuple(args)
                        existing.kwargs = dict(kwargs)
                        existing.timeout_s = timeout
                        existing.priority = priority
                        existing.created_mono = now
                        existing.expires_mono = (
                            now + ttl if ttl is not None else None
                        )
                        self._seq += 1
                        existing.seq = self._seq
                        heapq.heappush(
                            self._heap,
                            (priority.value, existing.seq, existing.job_id),
                        )
                        self._stats["coalesced"] += 1
                        self._cond.notify()
                        return self._handle(existing)
            pending = [
                job for job in self._jobs.values()
                if not job.started and not job.settled
            ]
            if len(pending) >= self._maxsize:
                victim: Optional[_Job] = None
                for job in pending:
                    if victim is None or (
                        job.priority.value, -job.seq
                    ) > (
                        victim.priority.value, -victim.seq
                    ):
                        victim = job
                assert victim is not None  # pending is non-empty here
                if victim.priority.value > priority.value:
                    self._settle_locked(
                        victim, dropped=True, reason="evicted by higher priority job",
                    )
                    self._stats["dropped"] += 1
                else:
                    self._stats["rejected"] += 1
                    raise IsolationBackpressure(
                        f"{self._runtime.value} queue full "
                        f"({self._maxsize}); job rejected"
                    )
            self._id_seq += 1
            self._seq += 1
            job = _Job(
                job_id=f"job_{self._runtime.value}_{self._id_seq}",
                runtime=self._runtime,
                priority=priority,
                seq=self._seq,
                fn=fn,
                args=tuple(args),
                kwargs=dict(kwargs),
                coalesce_key=coalesce_key,
                timeout_s=timeout,
                created_mono=now,
                expires_mono=now + ttl if ttl is not None else None,
                cancel_event=threading.Event(),
                future=Future(),
            )
            self._jobs[job.job_id] = job
            if coalesce_key:
                self._by_coalesce[coalesce_key] = job.job_id
            heapq.heappush(
                self._heap, (priority.value, job.seq, job.job_id)
            )
            self._stats["submitted"] += 1
            self._cond.notify()
            return self._handle(job)

    def _handle(self, job: _Job) -> JobHandle:
        return JobHandle(
            job_id=job.job_id,
            runtime=job.runtime,
            priority=job.priority,
            future=job.future,
            cancel_event=job.cancel_event,
            _cancel_fn=lambda: self.cancel(job.job_id),
        )

    def _settle_locked(
        self, job: _Job, *, dropped: bool = False, reason: str = ""
    ) -> None:
        job.settled = True
        if job.coalesce_key and self._by_coalesce.get(
            job.coalesce_key
        ) == job.job_id:
            del self._by_coalesce[job.coalesce_key]
        if not job.future.done():
            if dropped:
                job.future.set_exception(
                    IsolationBackpressure(reason or "job dropped")
                )
            else:
                job.future.cancel()

    def cancel(self, job_id: str) -> bool:
        """Cancel one job: pending jobs drop immediately; running jobs
        get their cancel event set (cooperative)."""
        with self._cond:
            job = self._jobs.get(job_id)
            if job is None or job.settled:
                return False
            if job.started:
                job.cancel_event.set()
                return True
            self._settle_locked(job)
            self._stats["cancelled"] += 1
            # A cancelled TTL/coalesce entry must not linger.
            self._jobs.pop(job.job_id, None)
            return True

    def pop(self, timeout: Optional[float] = None) -> Optional[_Job]:
        """Take the highest-priority live job (workers only).

        Skips cancelled, dropped, and TTL-expired entries. Returns None
        on timeout so worker loops can observe their stop flag.
        """
        deadline = None if timeout is None else self._clock() + max(
            0.0, timeout
        )
        with self._cond:
            while True:
                while self._heap:
                    _, seq, job_id = heapq.heappop(self._heap)
                    job = self._jobs.get(job_id)
                    if job is None or job.settled or job.started:
                        continue
                    if job.seq != seq:
                        continue  # stale heap entry (coalesce refresh)
                    now = self._clock()
                    if (job.expires_mono is not None
                            and now >= job.expires_mono):
                        job.settled = True
                        if job.coalesce_key and self._by_coalesce.get(
                            job.coalesce_key
                        ) == job.job_id:
                            del self._by_coalesce[job.coalesce_key]
                        self._stats["expired"] += 1
                        if not job.future.done():
                            job.future.set_exception(
                                JobExpiredError(
                                    f"{job.job_id}: TTL elapsed before start"
                                )
                            )
                        self._jobs.pop(job.job_id, None)
                        continue
                    if job.cancel_event.is_set():
                        self._settle_locked(job)
                        self._stats["cancelled"] += 1
                        self._jobs.pop(job.job_id, None)
                        continue
                    job.started = True
                    if job.coalesce_key and self._by_coalesce.get(
                        job.coalesce_key
                    ) == job.job_id:
                        del self._by_coalesce[job.coalesce_key]
                    return job
                if deadline is not None and self._clock() >= deadline:
                    return None
                remaining = None if deadline is None else max(
                    0.0, deadline - self._clock()
                )
                if remaining is not None and remaining <= 0:
                    return None
                self._cond.wait(timeout=remaining if remaining else None)
                if deadline is not None and self._clock() >= deadline \
                        and not self._heap:
                    return None

    def _finish(self, job: _Job, outcome: str) -> None:
        with self._cond:
            job.settled = True
            self._jobs.pop(job.job_id, None)
            if outcome in self._stats:
                self._stats[outcome] += 1


def _wants_cancel(fn: Callable[..., Any]) -> bool:
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return "cancel_event" in params


class IsolationWorker:
    """One daemon thread draining a :class:`BoundedJobQueue`.

    Guarantees: jobs run off the caller's thread; one failing job never
    breaks the worker or its siblings (failure isolation); timeouts arm
    the job's cancel event and are recorded even when the callable
    ignores them (hard preemption needs :class:`ProcessOffload`).
    """

    def __init__(
        self,
        runtime: RuntimeKind = RuntimeKind.EVENT,
        *,
        maxsize: Optional[int] = None,
        clock: Callable[[], float] = time.monotonic,
        thread_name: Optional[str] = None,
    ) -> None:
        if not isinstance(runtime, RuntimeKind):
            raise ValueError("runtime: expected RuntimeKind")
        size = DEFAULT_RUNTIME_QUEUE_SIZES[runtime] if maxsize is None \
            else maxsize
        self.queue = BoundedJobQueue(size, runtime=runtime, clock=clock)
        self._clock = clock
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._serve,
            name=thread_name or f"mimir-{runtime.value}",
            daemon=True,
        )
        self._started = False
        self._run_ms: list[float] = []
        self._wait_ms: list[float] = []
        self._metrics_lock = threading.Lock()

    @property
    def runtime(self) -> RuntimeKind:
        return self.queue.runtime

    @property
    def running(self) -> bool:
        return self._started and self._thread.is_alive()

    def start(self) -> None:
        if not self._started:
            self._started = True
            self._thread.start()

    def stop(self, timeout_s: float = 5.0) -> None:
        self._stop.set()
        if self._started:
            self._thread.join(timeout=max(0.0, timeout_s))

    def submit(
        self,
        fn: Callable[..., Any],
        args: tuple[Any, ...] = (),
        kwargs: Optional[dict[str, Any]] = None,
        **options: Any,
    ) -> JobHandle:
        self.start()
        return self.queue.submit(fn, args, kwargs, **options)

    def cancel(self, job_id: str) -> bool:
        return self.queue.cancel(job_id)

    def metrics(self) -> dict[str, Any]:
        snap = self.queue.stats()
        with self._metrics_lock:
            waits = sorted(self._wait_ms)
            runs = sorted(self._run_ms)
        snap["queue_wait_ms_p50"] = _percentile(waits, 50)
        snap["queue_wait_ms_p95"] = _percentile(waits, 95)
        snap["run_ms_p50"] = _percentile(runs, 50)
        snap["run_ms_p95"] = _percentile(runs, 95)
        return snap

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                job = self.queue.pop(timeout=0.05)
            except Exception:
                continue
            if job is None:
                continue
            self._execute(job)

    def _execute(self, job: _Job) -> None:
        queued_ms = (self._clock() - job.created_mono) * 1000.0
        with self._metrics_lock:
            self._wait_ms.append(queued_ms)
            if len(self._wait_ms) > 512:
                del self._wait_ms[:256]
        call_kwargs = dict(job.kwargs)
        if _wants_cancel(job.fn):
            call_kwargs["cancel_event"] = job.cancel_event
        timer: Optional[threading.Timer] = None
        if job.timeout_s is not None:
            timer = threading.Timer(job.timeout_s, job.cancel_event.set)
            timer.daemon = True
            timer.start()
        started = self._clock()
        try:
            result = job.fn(*job.args, **call_kwargs)
        except JobCancelledError as exc:
            self._expire_timer(timer)
            self.queue._finish(job, "cancelled")
            if not job.future.done():
                job.future.set_exception(exc)
            return
        except Exception as exc:  # failure isolation: worker survives
            self._expire_timer(timer)
            self.queue._finish(job, "failed")
            if not job.future.done():
                job.future.set_exception(exc)
            return
        elapsed = self._clock() - started
        self._expire_timer(timer)
        with self._metrics_lock:
            self._run_ms.append(elapsed * 1000.0)
            if len(self._run_ms) > 512:
                del self._run_ms[:256]
        if job.cancel_event.is_set() and job.timeout_s is not None \
                and elapsed >= job.timeout_s:
            # The callable ignored its timeout: record the miss but
            # still deliver the late value (it did finish).
            self.queue._finish(job, "timed_out")
        else:
            self.queue._finish(job, "completed")
        if not job.future.done():
            job.future.set_result(result)

    @staticmethod
    def _expire_timer(timer: Optional[threading.Timer]) -> None:
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return round(sorted_vals[0], 3)
    rank = (pct / 100.0) * (len(sorted_vals) - 1)
    low = int(rank)
    high = min(len(sorted_vals) - 1, low + 1)
    frac = rank - low
    return round(
        sorted_vals[low] * (1.0 - frac) + sorted_vals[high] * frac, 3
    )


class RuntimeIsolation:
    """Owns one bounded queue + worker per plan section 6 runtime.

    Separate workers are the isolation: a saturated inference queue
    cannot delay the safety worker, and UI rendering never shares a
    thread with transcription, inference, or actuation.
    """

    def __init__(
        self,
        sizes: Optional[dict[RuntimeKind, int]] = None,
        *,
        autostart: bool = True,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._workers: dict[RuntimeKind, IsolationWorker] = {}
        for runtime in RuntimeKind:
            size = DEFAULT_RUNTIME_QUEUE_SIZES[runtime]
            if sizes is not None and runtime in sizes:
                size = sizes[runtime]
            worker = IsolationWorker(runtime, maxsize=size, clock=clock)
            self._workers[runtime] = worker
            if autostart:
                worker.start()

    def worker(self, runtime: RuntimeKind) -> IsolationWorker:
        if not isinstance(runtime, RuntimeKind):
            raise ValueError("runtime: expected RuntimeKind")
        return self._workers[runtime]

    def submit(
        self,
        runtime: Any,
        fn: Callable[..., Any],
        args: tuple[Any, ...] = (),
        kwargs: Optional[dict[str, Any]] = None,
        **options: Any,
    ) -> JobHandle:
        if isinstance(runtime, str):
            try:
                runtime = RuntimeKind(runtime.strip().lower())
            except ValueError:
                raise ValueError(
                    f"runtime: unknown runtime {runtime!r}"
                ) from None
        return self.worker(runtime).submit(fn, args, kwargs, **options)

    def cancel(self, job_id: str) -> bool:
        for worker in self._workers.values():
            try:
                if worker.cancel(job_id):
                    return True
            except Exception:
                continue
        return False

    def stats(self) -> dict[str, dict[str, Any]]:
        return {
            runtime.value: worker.metrics()
            for runtime, worker in self._workers.items()
        }

    def shutdown(self, timeout_s: float = 5.0) -> None:
        for worker in self._workers.values():
            try:
                worker.stop(timeout_s=timeout_s)
            except Exception:
                continue


_global_lock = threading.Lock()
_global_runtime: Optional[RuntimeIsolation] = None


def get_runtime_isolation() -> RuntimeIsolation:
    """Process-global runtime isolation (created lazily, autostarted)."""
    global _global_runtime
    with _global_lock:
        if _global_runtime is None:
            _global_runtime = RuntimeIsolation(autostart=True)
        return _global_runtime


def set_runtime_isolation(runtime: RuntimeIsolation) -> None:
    """Replace the process-global runtime (tests/hosts only)."""
    global _global_runtime
    if not isinstance(runtime, RuntimeIsolation):
        raise ValueError("runtime: expected RuntimeIsolation")
    with _global_lock:
        _global_runtime = runtime


def reset_runtime_isolation() -> None:
    """Shut down and drop the process-global runtime (tests only)."""
    global _global_runtime
    with _global_lock:
        old, _global_runtime = _global_runtime, None
    if old is not None:
        try:
            old.shutdown(timeout_s=2.0)
        except Exception:
            pass


def _probe_add(left: int, right: int) -> int:
    """Module-level picklable probe for :class:`ProcessOffload` tests."""
    return int(left) + int(right)


class ProcessOffload:
    """Real process isolation for CPU/GPU-heavy work (Whisper, LLM).

    Plan section 6 reserves ``multiprocessing`` for workloads that could
    starve the UI or safety paths. The pool is created lazily (never at
    import, spawn-safe on Windows) and only runs module-level picklable
    callables. Timeouts are observed by the waiter via
    :meth:`result`; a still-running child keeps its slot until the pool
    is shut down (documented: processes cannot be safely killed
    mid-inference without losing the worker).
    """

    def __init__(
        self, *, max_workers: int = 1, mp_context: str = "spawn"
    ) -> None:
        if type(max_workers) is bool or not isinstance(max_workers, int) \
                or max_workers < 1:
            raise ValueError("max_workers: expected a positive integer")
        if mp_context not in ("spawn", "fork", "forkserver"):
            raise ValueError("mp_context: expected spawn, fork, or forkserver")
        self._max_workers = max_workers
        self._mp_context = mp_context
        self._lock = threading.Lock()
        self._executor: Any = None

    @property
    def available(self) -> bool:
        try:
            import multiprocessing as mp

            mp.get_context(self._mp_context)
            return True
        except Exception:
            return False

    def submit(
        self,
        fn: Callable[..., Any],
        args: tuple[Any, ...] = (),
        kwargs: Optional[dict[str, Any]] = None,
    ) -> Future:
        """Run ``fn(*args, **kwargs)`` in a worker process (lazy pool)."""
        if not callable(fn):
            raise ValueError("fn: expected a callable")
        from concurrent.futures import ProcessPoolExecutor

        with self._lock:
            if self._executor is None:
                try:
                    import multiprocessing as mp

                    ctx = mp.get_context(self._mp_context)
                    self._executor = ProcessPoolExecutor(
                        max_workers=self._max_workers, mp_context=ctx
                    )
                except Exception as exc:
                    raise IsolationError(
                        "process pool unavailable in this environment: "
                        f"{exc}"
                    ) from exc
            executor = self._executor
        try:
            return executor.submit(fn, *(args or ()), **(kwargs or {}))
        except Exception as exc:
            raise IsolationError(f"process submit failed: {exc}") from exc

    def result(
        self, future: Future, timeout_s: Optional[float] = None
    ) -> Any:
        """Wait for a process future (raises TimeoutError on expiry)."""
        timeout = _positive_or_none(timeout_s, field="timeout_s")
        try:
            return future.result(timeout=timeout)
        except TimeoutError:
            raise
        except Exception as exc:
            raise IsolationError(
                f"process job failed: {exc}"
            ) from exc

    def shutdown(
        self, wait: bool = True, cancel_futures: bool = True
    ) -> None:
        with self._lock:
            executor, self._executor = self._executor, None
        if executor is not None:
            try:
                executor.shutdown(
                    wait=wait, cancel_futures=cancel_futures
                )
            except Exception:
                pass


class LoopOwner:
    """Single asyncio/qasync event-loop ownership for the process.

    Exactly one loop may be owned at a time; a second ``acquire`` under
    a different name is rejected so competing loops can never run side
    by side in the UI process (plan section 6). Ownership is explicit::

        loop = LoopOwner.acquire("event-runtime")
        ...
        LoopOwner.release("event-runtime")

    Qt hosts that need qasync call :meth:`acquire_qasync` (lazy import;
    the Qt application keeps pumping the loop). Hosts without a loop
    keep using the loop-free :meth:`EventBus.pump` bridge.
    """

    _lock = threading.Lock()
    _owner: Optional[str] = None
    _loop: Any = None
    _thread: Optional[threading.Thread] = None
    _stop_thread = threading.Event()
    _managed_thread: bool = False

    @classmethod
    def owned(cls) -> bool:
        with cls._lock:
            return cls._owner is not None

    @classmethod
    def owner(cls) -> Optional[str]:
        with cls._lock:
            return cls._owner

    @classmethod
    def loop(cls) -> Any:
        with cls._lock:
            return cls._loop

    @classmethod
    def acquire(
        cls,
        owner: str,
        *,
        loop_factory: Optional[Callable[[], Any]] = None,
        manage_thread: bool = True,
    ) -> Any:
        """Acquire (or re-acquire) the process loop for ``owner``.

        ``loop_factory`` injects a host-managed loop (used by tests and
        qasync hosts); otherwise a fresh asyncio loop is created on a
        dedicated daemon thread. Rejected when another owner holds it.
        """
        if not isinstance(owner, str) or not owner.strip():
            raise ValueError("owner: expected a non-empty name")
        name = owner.strip()
        with cls._lock:
            if cls._owner is not None:
                if cls._owner == name:
                    return cls._loop
                raise IsolationError(
                    "event loop already owned by "
                    f"{cls._owner!r}; {name!r} must share it "
                    "(single loop ownership)"
                )
            if loop_factory is not None:
                try:
                    loop = loop_factory()
                except Exception as exc:
                    raise IsolationError(
                        f"loop_factory failed for {name!r}: {exc}"
                    ) from exc
                cls._owner = name
                cls._loop = loop
                cls._thread = None
                cls._managed_thread = False
                return loop
            try:
                loop = asyncio.new_event_loop()
            except Exception as exc:
                raise IsolationError(
                    "cannot create an event loop in this environment "
                    f"({exc}); use the loop-free EventBus.pump bridge"
                ) from exc
            thread: Optional[threading.Thread] = None
            if manage_thread:
                cls._stop_thread = threading.Event()
                stop = cls._stop_thread

                def _pump() -> None:
                    asyncio.set_event_loop(loop)
                    try:
                        loop.run_forever()
                    finally:
                        stop.set()

                thread = threading.Thread(
                    target=_pump,
                    name=f"mimir-loop-{name}",
                    daemon=True,
                )
                thread.start()
            cls._owner = name
            cls._loop = loop
            cls._thread = thread
            cls._managed_thread = manage_thread
            return loop

    @classmethod
    def acquire_qasync(cls, owner: str, app: Any = None) -> Any:
        """Acquire a qasync loop bound to the Qt application (lazy).

        The Qt host keeps pumping (no management thread is started).
        Raises :class:`IsolationError` with an install hint when qasync
        is absent, or when no QApplication exists to bind to.
        """
        try:
            import qasync  # type: ignore[import-not-found]
        except ImportError:
            raise IsolationError(
                "qasync is required for Qt-integrated loops "
                "(pip install 'mimir[cobot]')"
            ) from None
        target = app
        if target is None:
            try:
                from PyQt6.QtWidgets import QApplication

                target = QApplication.instance()
            except ImportError:
                try:
                    from PySide6.QtWidgets import QApplication  # type: ignore[import-not-found]

                    target = QApplication.instance()
                except ImportError:
                    target = None
        if target is None:
            raise IsolationError(
                "acquire_qasync needs a running QApplication"
            )
        return cls.acquire(
            owner,
            loop_factory=lambda: qasync.QEventLoop(target),
            manage_thread=False,
        )

    @classmethod
    def run(cls, owner: str, coro: Any,
            timeout_s: Optional[float] = None) -> Any:
        """Drive one coroutine on the owned loop from any thread."""
        timeout = _positive_or_none(timeout_s, field="timeout_s")
        with cls._lock:
            if cls._owner != owner.strip():
                raise IsolationError(
                    f"loop not owned by {owner!r} (owner: {cls._owner!r})"
                )
            loop = cls._loop
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=timeout)

    @classmethod
    def release(cls, owner: str) -> bool:
        """Release ownership (owner only); stops a managed loop thread."""
        if not isinstance(owner, str) or not owner.strip():
            raise ValueError("owner: expected a non-empty name")
        name = owner.strip()
        with cls._lock:
            if cls._owner is None:
                return False
            if cls._owner != name:
                raise IsolationError(
                    f"loop owned by {cls._owner!r}; {name!r} cannot release it"
                )
            loop = cls._loop
            thread = cls._thread
            managed = cls._managed_thread
            cls._owner = None
            cls._loop = None
            cls._thread = None
            cls._managed_thread = False
        if managed and thread is not None and loop is not None:
            try:
                loop.call_soon_threadsafe(loop.stop)
            except Exception:
                pass
            try:
                thread.join(timeout=5.0)
            except Exception:
                pass
            try:
                loop.close()
            except Exception:
                pass
        return True
