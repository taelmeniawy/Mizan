"""Run long backup/restore work off the caller's thread.

The service API is synchronous and safe to call from any thread.  The UI starts
one of these jobs, keeps painting, renders ``stage`` updates and can cancel
before publication.  A cancelled or failed job never leaves a partial backup:
the service already stages and verifies before publishing.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace


class JobCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class JobState:
    status: str = "pending"  # pending | running | succeeded | failed | cancelled
    stage: str | None = None
    stages: tuple[str, ...] = ()
    result: object = None
    error: BaseException | None = None


class BackgroundJob:
    """Threaded wrapper with progress reporting and cooperative cancellation."""

    def __init__(self, work, name: str = "mizan-background"):
        if not callable(work):
            raise TypeError("work must be callable")
        self.work = work
        self.name = name
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._state = JobState()

    # ------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------
    def start(self) -> "BackgroundJob":
        if self._thread is not None:
            raise RuntimeError("job has already been started")
        self._update(status="running")
        self._thread = threading.Thread(target=self._run, name=self.name, daemon=True)
        self._thread.start()
        return self

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def wait(self, timeout: float | None = None) -> JobState:
        if self._thread is not None:
            self._thread.join(timeout)
        return self.state

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def state(self) -> JobState:
        with self._lock:
            return replace(self._state)

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------
    def progress(self, stage: str) -> None:
        """Call from the work function; raises when the job was cancelled."""
        with self._lock:
            self._state = replace(self._state, stage=stage,
                                  stages=(*self._state.stages, stage))
        if self._cancel.is_set():
            raise JobCancelled("job was cancelled")

    def _update(self, **changes) -> None:
        with self._lock:
            self._state = replace(self._state, **changes)

    def _run(self) -> None:
        try:
            result = self.work(self.progress, self._cancel)
        except JobCancelled as exc:
            self._update(status="cancelled", error=exc)
        except BaseException as exc:  # Recorded for the caller; never raised in the thread.
            self._update(status="failed", error=exc)
        else:
            if self._cancel.is_set():
                self._update(status="cancelled")
            else:
                self._update(status="succeeded", result=result)
