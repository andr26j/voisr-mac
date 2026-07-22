import threading
from concurrent.futures import Future

from voisr.recording_queue import RecordingQueue, SessionResult


class FakeRecorder:
    def __init__(self, stopped=None):
        self.path = None
        self.stopped = stopped or threading.Event()
        self.cancelled = False
        self.cancelled_event = threading.Event()

    def start(self, path):
        self.path = path

    def stop(self):
        self.path.write_bytes(b"audio" * 400)
        self.stopped.set()
        return ""

    def cancel(self):
        self.cancelled = True
        self.cancelled_event.set()


def test_second_capture_finalizes_while_first_processes_but_completes_fifo(tmp_path):
    recorders = []
    first_processing = threading.Event()
    unblock_first = threading.Event()
    processing_order = []
    active_processors = 0
    max_active_processors = 0
    state_lock = threading.Lock()

    def recorder_factory():
        recorder = FakeRecorder()
        recorders.append(recorder)
        return recorder

    def processor(session):
        nonlocal active_processors, max_active_processors
        with state_lock:
            active_processors += 1
            max_active_processors = max(max_active_processors, active_processors)
        try:
            processing_order.append(session.sequence)
            if session.sequence == 1:
                first_processing.set()
                assert unblock_first.wait(2)
            return SessionResult.success(session.sequence, text=str(session.sequence))
        finally:
            with state_lock:
                active_processors -= 1

    queue = RecordingQueue(
        recorder_factory=recorder_factory,
        fallback_recorder_factory=None,
        processor=processor,
        clock=iter([1.0, 2.0, 3.0, 4.0]).__next__,
        temp_path_factory=lambda: tmp_path / f"capture-{len(recorders) + 1}.wav",
    )
    try:
        first = queue.start()
        first_future = queue.release(True, {"paste": True})
        completions = []
        first_future.add_done_callback(lambda future: completions.append(future.result()))
        assert first_processing.wait(2)

        second = queue.start()
        second_future = queue.release(True, {"paste": True})

        assert first.recorder is not second.recorder
        assert recorders[1].stopped.wait(2)
        assert second_future.done() is False

        unblock_first.set()
        assert first_future.result(timeout=2).to_api_dict()["text"] == "1"
        assert second_future.result(timeout=2).to_api_dict()["text"] == "2"
        assert processing_order == [1, 2]
        assert max_active_processors == 1
        assert len(completions) == 1
        assert first.wav_path.exists() is False
        assert second.wav_path.exists() is False
    finally:
        unblock_first.set()
        queue.close()


def test_start_waits_for_previous_stop_but_not_previous_processing(tmp_path):
    stop_started = threading.Event()
    allow_stop = threading.Event()
    stop_finished = threading.Event()
    second_started = threading.Event()
    first_processing = threading.Event()
    unblock_processing = threading.Event()
    recorders = []

    class BlockingStopRecorder(FakeRecorder):
        def stop(self):
            stop_started.set()
            assert allow_stop.wait(2)
            result = super().stop()
            stop_finished.set()
            return result

    def recorder_factory():
        recorder = BlockingStopRecorder() if not recorders else FakeRecorder()
        recorders.append(recorder)
        return recorder

    def processor(session):
        if session.sequence == 1:
            first_processing.set()
            assert unblock_processing.wait(2)
        return SessionResult.success(session.sequence, text=str(session.sequence))

    queue = RecordingQueue(
        recorder_factory=recorder_factory,
        fallback_recorder_factory=None,
        processor=processor,
        temp_path_factory=lambda: tmp_path / f"capture-{len(recorders) + 1}.wav",
    )
    try:
        queue.start()
        first_future = queue.release(True, {})
        assert stop_started.wait(2)

        second_session = []

        def start_second():
            second_session.append(queue.start())
            second_started.set()

        start_thread = threading.Thread(target=start_second)
        start_thread.start()
        assert second_started.wait(0.1) is False

        allow_stop.set()
        assert stop_finished.wait(2)
        assert first_processing.wait(2)
        assert second_started.wait(2)
        assert first_future.done() is False

        second_future = queue.release(True, {})
        unblock_processing.set()
        assert first_future.result(timeout=2).to_api_dict()["text"] == "1"
        assert second_future.result(timeout=2).to_api_dict()["text"] == "2"
        assert second_session[0].recorder is recorders[1]
    finally:
        allow_stop.set()
        unblock_processing.set()
        start_thread.join(timeout=2)
        queue.close()


def test_release_snapshots_nested_context_before_async_processing(tmp_path):
    stop_started = threading.Event()
    allow_stop = threading.Event()
    received_contexts = []

    class BlockingStopRecorder(FakeRecorder):
        def stop(self):
            stop_started.set()
            assert allow_stop.wait(2)
            return super().stop()

    def processor(session):
        received_contexts.append(session.context)
        return SessionResult.success(session.sequence, text="done")

    queue = RecordingQueue(
        recorder_factory=BlockingStopRecorder,
        fallback_recorder_factory=None,
        processor=processor,
        temp_path_factory=lambda: tmp_path / "capture.wav",
    )
    try:
        queue.start()
        context = {"metadata": {"language": "en"}, "items": ["initial"]}
        future = queue.release(True, context)
        assert stop_started.wait(2)

        context["metadata"]["language"] = "fr"
        context["items"].append("later")
        allow_stop.set()

        assert future.result(timeout=2).to_api_dict()["text"] == "done"
        assert received_contexts == [
            {"metadata": {"language": "en"}, "items": ["initial"]}
        ]
    finally:
        allow_stop.set()
        queue.close()


def test_cancelled_completion_does_not_stop_later_sessions(tmp_path):
    class CancelBetweenDoneAndSetResultFuture(Future):
        def __init__(self):
            super().__init__()
            self.done_checked = threading.Event()
            self.allow_done_to_return = threading.Event()

        def done(self):
            self.done_checked.set()
            assert self.allow_done_to_return.wait(2)
            return False

    recorders = []

    def recorder_factory():
        recorder = FakeRecorder()
        recorders.append(recorder)
        return recorder

    def processor(session):
        return SessionResult.success(session.sequence, text=str(session.sequence))

    queue = RecordingQueue(
        recorder_factory=recorder_factory,
        fallback_recorder_factory=None,
        processor=processor,
        temp_path_factory=lambda: tmp_path / f"capture-{len(recorders) + 1}.wav",
    )
    second_future = None
    try:
        first = queue.start()
        first.completion = CancelBetweenDoneAndSetResultFuture()
        queue.release(True, {})
        assert first.completion.done_checked.wait(2)
        assert first.completion.cancel() is True
        first.completion.allow_done_to_return.set()

        queue.start()
        second_future = queue.release(True, {})
        assert second_future.result(timeout=1).to_api_dict()["text"] == "2"
    finally:
        first.completion.allow_done_to_return.set()
        if second_future is not None and second_future.done():
            queue.close()


class ExplodingStartRecorder(FakeRecorder):
    def start(self, path):
        self.path = path
        raise RuntimeError("primary unavailable")


class ExplodingStopRecorder(FakeRecorder):
    def stop(self):
        self.stopped.set()
        raise RuntimeError("stop exploded")


def test_start_fallback_is_owned_only_by_that_session(tmp_path):
    primary = ExplodingStartRecorder()
    fallback = FakeRecorder()
    queue = RecordingQueue(
        recorder_factory=lambda: primary,
        fallback_recorder_factory=lambda: fallback,
        processor=lambda session: SessionResult.success(session.sequence),
        temp_path_factory=lambda: tmp_path / "fallback.wav",
    )
    try:
        session = queue.start()
        assert session.recorder is fallback
        assert primary.cancelled is True
        assert queue.release(False, {}).result(timeout=2).stage is None
    finally:
        queue.close()


def test_stop_and_processor_failures_do_not_stall_later_sessions(tmp_path):
    available = iter([ExplodingStopRecorder(), FakeRecorder(), FakeRecorder()])
    recorders = []
    processor_calls = 0

    def recorder_factory():
        recorder = next(available)
        recorders.append(recorder)
        return recorder

    def processor(session):
        nonlocal processor_calls
        processor_calls += 1
        if processor_calls == 1:
            raise RuntimeError("unexpected processor failure")
        return SessionResult.success(session.sequence, text="recovered")

    queue = RecordingQueue(
        recorder_factory=recorder_factory,
        fallback_recorder_factory=None,
        processor=processor,
        temp_path_factory=lambda: tmp_path / f"failure-{len(recorders) + 1}.wav",
    )
    try:
        queue.start()
        stopped = queue.release(False, {})
        queue.start()
        processor_failed = queue.release(False, {})
        queue.start()
        recovered = queue.release(False, {})

        assert stopped.result(timeout=2).stage == "stop"
        assert processor_failed.result(timeout=2).stage == "transcription"
        assert recovered.result(timeout=2).to_api_dict()["text"] == "recovered"
    finally:
        queue.close()


def test_close_cancels_active_and_drains_released_session(tmp_path):
    process_started = threading.Event()
    allow_process = threading.Event()
    recorders = []

    def factory():
        recorder = FakeRecorder()
        recorders.append(recorder)
        return recorder

    def processor(session):
        process_started.set()
        assert allow_process.wait(2)
        return SessionResult.success(session.sequence, text="done")

    queue = RecordingQueue(
        recorder_factory=factory,
        fallback_recorder_factory=None,
        processor=processor,
        temp_path_factory=lambda: tmp_path / f"close-{len(recorders) + 1}.wav",
    )
    queue.start()
    released = queue.release(False, {})
    assert process_started.wait(2)
    active = queue.start()

    close_finished = threading.Event()

    def close_queue():
        try:
            queue.close()
        finally:
            close_finished.set()

    closer = threading.Thread(target=close_queue)
    closer.start()

    try:
        assert close_finished.wait(0.1) is False
        assert active.recorder.cancelled_event.wait(2)
    finally:
        allow_process.set()
        closer.join(2)

    assert released.result(timeout=2).to_api_dict()["text"] == "done"
    assert active.wav_path.exists() is False
    assert close_finished.is_set()
    assert queue._finalizers == set()
    assert queue._worker.is_alive() is False
    queue.close()


def test_temp_wav_failure_is_a_structured_start_error(tmp_path):
    from voisr.recording_queue import RecordingQueueError

    queue = RecordingQueue(
        recorder_factory=FakeRecorder,
        fallback_recorder_factory=None,
        processor=lambda session: SessionResult.success(session.sequence),
        temp_path_factory=lambda: (_ for _ in ()).throw(OSError("disk full")),
    )
    try:
        try:
            queue.start()
        except RecordingQueueError as error:
            assert error.stage == "start"
            assert "disk full" in str(error)
        else:
            raise AssertionError("start should report temporary-file failure")
    finally:
        queue.close()
