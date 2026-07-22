import os
import queue as queue_module
import tempfile
import threading
import time
from copy import deepcopy
from concurrent.futures import Future, InvalidStateError
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class SessionResult:
    sequence: int | None
    data: dict[str, Any]
    stage: str | None = None

    @classmethod
    def success(cls, sequence: int, **data) -> "SessionResult":
        return cls(sequence=sequence, data=data)

    @classmethod
    def failure(
        cls, sequence: int | None, stage: str, message: str, **partial
    ) -> "SessionResult":
        return cls(
            sequence=sequence,
            data={**partial, "error": message},
            stage=stage,
        )

    def to_api_dict(self) -> dict[str, Any]:
        return dict(self.data)


@dataclass
class RecordingSession:
    sequence: int
    wav_path: Path
    started_at: float
    recorder: Any
    released_at: float | None = None
    paste_requested: bool = False
    context: dict[str, Any] = field(default_factory=dict)
    capture_stderr: str = ""
    capture_error: SessionResult | None = None
    capture_ready: threading.Event = field(default_factory=threading.Event)
    completion: Future = field(default_factory=Future)


class RecordingQueueError(RuntimeError):
    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage


def make_temp_wav() -> Path:
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    return Path(path)


_STOP = object()


class RecordingQueue:
    def __init__(
        self,
        recorder_factory: Callable[[], Any],
        fallback_recorder_factory: Callable[[], Any] | None,
        processor: Callable[[RecordingSession], SessionResult],
        clock: Callable[[], float] = time.monotonic,
        temp_path_factory: Callable[[], Path] = make_temp_wav,
    ):
        self._recorder_factory = recorder_factory
        self._fallback_recorder_factory = fallback_recorder_factory
        self._processor = processor
        self._clock = clock
        self._temp_path_factory = temp_path_factory
        self._lock = threading.Lock()
        self._active = None
        self._capture_finalizing = None
        self._next_sequence = 1
        self._pending = queue_module.Queue()
        self._finalizers = set()
        self._closing = False
        self._closed = False
        self._closed_event = threading.Event()
        self._worker = threading.Thread(
            target=self._worker_loop, name="voisr-completion", daemon=True
        )
        self._worker.start()

    @property
    def has_active_capture(self) -> bool:
        with self._lock:
            return self._active is not None

    @staticmethod
    def _safe_cancel(recorder) -> None:
        if recorder is None:
            return
        for method_name in ("cancel", "close"):
            method = getattr(recorder, method_name, None)
            if method is None:
                continue
            try:
                method()
                return
            except Exception:
                continue

    @staticmethod
    def _remove_wav(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def start(self) -> RecordingSession:
        while True:
            with self._lock:
                if self._closing:
                    raise RecordingQueueError("start", "app is shutting down")
                if self._active is not None:
                    raise RecordingQueueError("start", "already recording")
                finalizing = self._capture_finalizing
                if finalizing is None:
                    try:
                        wav_path = self._temp_path_factory()
                    except Exception as error:
                        raise RecordingQueueError(
                            "start", f"recorder failed: {error}"[:200]
                        )

                    recorder = None
                    try:
                        recorder = self._recorder_factory()
                        recorder.start(wav_path)
                    except Exception:
                        self._safe_cancel(recorder)
                        recorder = None
                        try:
                            if self._fallback_recorder_factory is None:
                                raise RuntimeError("no fallback recorder available")
                            recorder = self._fallback_recorder_factory()
                            recorder.start(wav_path)
                        except Exception as fallback_error:
                            self._safe_cancel(recorder)
                            self._remove_wav(wav_path)
                            raise RecordingQueueError(
                                "start", f"recorder failed: {fallback_error}"[:200]
                            )

                    session = RecordingSession(
                        sequence=self._next_sequence,
                        wav_path=wav_path,
                        started_at=self._clock(),
                        recorder=recorder,
                    )
                    self._next_sequence += 1
                    self._active = session
                    return session
            finalizing.capture_ready.wait()

    @staticmethod
    def _resolved(result: SessionResult) -> Future:
        future = Future()
        future.set_result(result)
        return future

    def release(self, paste_requested: bool, context: dict[str, Any]) -> Future:
        with self._lock:
            session = self._active
            if session is None:
                return self._resolved(SessionResult.failure(None, "stop", "not recording"))
            self._active = None
            session.released_at = self._clock()
            session.paste_requested = bool(paste_requested)
            session.context = deepcopy(context)
            self._capture_finalizing = session
            self._pending.put(session)
            finalizer = threading.Thread(
                target=self._finalize_capture,
                args=(session,),
                name=f"voisr-capture-{session.sequence}",
                daemon=True,
            )
            self._finalizers.add(finalizer)
            try:
                finalizer.start()
            except Exception as error:
                self._finalizers.discard(finalizer)
                session.capture_error = SessionResult.failure(
                    session.sequence, "stop", f"recorder failed: {error}"[:200]
                )
                session.capture_ready.set()
                self._capture_finalizing = None
            return session.completion

    def _finalize_capture(self, session: RecordingSession) -> None:
        try:
            session.capture_stderr = session.recorder.stop()
        except Exception as error:
            session.capture_error = SessionResult.failure(
                session.sequence, "stop", f"recorder failed: {error}"[:200]
            )
        finally:
            session.capture_ready.set()
            with self._lock:
                self._finalizers.discard(threading.current_thread())
                if self._capture_finalizing is session:
                    self._capture_finalizing = None

    def _worker_loop(self) -> None:
        while True:
            item = self._pending.get()
            try:
                if item is _STOP:
                    return
                session = item
                session.capture_ready.wait()
                result = session.capture_error
                if result is None:
                    try:
                        result = self._processor(session)
                        if not isinstance(result, SessionResult):
                            raise TypeError("session processor returned no SessionResult")
                    except Exception as error:
                        result = SessionResult.failure(
                            session.sequence,
                            "transcription",
                            f"processing failed: {error}"[:250],
                        )
                self._remove_wav(session.wav_path)
                if not session.completion.done():
                    try:
                        session.completion.set_result(result)
                    except InvalidStateError:
                        pass
            finally:
                self._pending.task_done()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            if self._closing:
                wait_for_owner = True
                active = None
            else:
                wait_for_owner = False
                self._closing = True
                active, self._active = self._active, None

        if wait_for_owner:
            self._closed_event.wait()
            return

        if active is not None:
            self._safe_cancel(active.recorder)
            self._remove_wav(active.wav_path)

        self._pending.join()
        while True:
            with self._lock:
                finalizers = list(self._finalizers)
            if not finalizers:
                break
            for finalizer in finalizers:
                finalizer.join()

        self._pending.put(_STOP)
        self._worker.join()
        with self._lock:
            self._closed = True
        self._closed_event.set()
