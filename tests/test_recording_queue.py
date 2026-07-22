import threading
from concurrent.futures import Future

import pytest

from voisr.recording_queue import RecordingQueue, RecordingQueueError, SessionResult


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


def test_start_waits_for_capture_handoff_but_not_previous_processing(tmp_path):
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
        max_in_flight=3,
    )
    try:
        queue.start()
        first_future = queue.release(True, {})
        assert stop_started.wait(2)

        reservation_made = threading.Event()

        class ObservedStartEvent(threading.Event):
            def clear(self):
                super().clear()
                reservation_made.set()

        queue._start_done = ObservedStartEvent()
        queue._start_done.set()

        second_session = []

        def start_second():
            second_session.append(queue.start())
            second_started.set()

        start_thread = threading.Thread(target=start_second)
        start_thread.start()
        assert reservation_made.wait(2)
        assert second_started.is_set() is False
        assert len(recorders) == 1
        assert queue._in_flight == 2
        with pytest.raises(RecordingQueueError) as error:
            queue.start()
        assert error.value.stage == "start"
        assert "already recording" in str(error.value)
        assert stop_finished.is_set() is False
        assert first_processing.is_set() is False

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


def test_completion_callback_can_start_at_released_capacity(tmp_path):
    recorders = []
    callback_started = threading.Event()
    callback_error = []

    def recorder_factory():
        recorder = FakeRecorder()
        recorders.append(recorder)
        return recorder

    queue = RecordingQueue(
        recorder_factory=recorder_factory,
        fallback_recorder_factory=None,
        processor=lambda session: SessionResult.success(session.sequence),
        temp_path_factory=lambda: tmp_path / f"callback-{len(recorders) + 1}.wav",
        max_in_flight=1,
    )
    try:
        queue.start()
        first = queue.release(False, {})

        def start_again(_future):
            try:
                queue.start()
            except Exception as error:
                callback_error.append(error)
            finally:
                callback_started.set()

        first.add_done_callback(start_again)
        assert callback_started.wait(2)
        assert callback_error == []
        assert len(recorders) == 2
        second = queue.release(False, {})
        assert second.result(timeout=2).sequence == 2
    finally:
        queue.close()


def test_capacity_is_validated(tmp_path):
    for capacity in (0, -1, True, "2"):
        with pytest.raises(ValueError):
            RecordingQueue(
                recorder_factory=FakeRecorder,
                fallback_recorder_factory=None,
                processor=lambda session: SessionResult.success(session.sequence),
                temp_path_factory=lambda: tmp_path / "invalid.wav",
                max_in_flight=capacity,
            )


def test_capacity_counts_active_pending_and_worker_owned_sessions(tmp_path):
    processing = threading.Event()
    allow_processing = threading.Event()
    recorders = []

    def recorder_factory():
        recorder = FakeRecorder()
        recorders.append(recorder)
        return recorder

    def processor(session):
        if session.sequence == 1:
            processing.set()
            assert allow_processing.wait(2)
        return SessionResult.success(session.sequence, text=str(session.sequence))

    queue = RecordingQueue(
        recorder_factory=recorder_factory,
        fallback_recorder_factory=None,
        processor=processor,
        temp_path_factory=lambda: tmp_path / f"capacity-{len(recorders) + 1}.wav",
        max_in_flight=2,
    )
    try:
        queue.start()
        first = queue.release(False, {})
        assert processing.wait(2)

        queue.start()
        second = queue.release(False, {})
        with pytest.raises(RecordingQueueError) as error:
            queue.start()
        assert error.value.stage == "start"
        assert "busy" in str(error.value)

        allow_processing.set()
        assert first.result(timeout=2).sequence == 1
        assert second.result(timeout=2).sequence == 2
    finally:
        allow_processing.set()
        queue.close()


def test_capacity_recovers_after_worker_completion(tmp_path):
    processing = threading.Event()
    allow_processing = threading.Event()
    queue = RecordingQueue(
        recorder_factory=FakeRecorder,
        fallback_recorder_factory=None,
        processor=lambda session: (
            processing.set(),
            allow_processing.wait(2),
            SessionResult.success(session.sequence),
        )[-1],
        temp_path_factory=lambda: tmp_path / "recovery.wav",
        max_in_flight=1,
    )
    try:
        queue.start()
        first = queue.release(False, {})
        assert processing.wait(2)
        with pytest.raises(RecordingQueueError):
            queue.start()

        allow_processing.set()
        first.result(timeout=2)
        queue.start()
        second = queue.release(False, {})
        assert second.result(timeout=2).sequence == 2
    finally:
        allow_processing.set()
        queue.close()


def test_failed_start_releases_its_capacity_reservation(tmp_path):
    attempts = 0

    def recorder_factory():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return ExplodingStartRecorder()
        return FakeRecorder()

    queue = RecordingQueue(
        recorder_factory=recorder_factory,
        fallback_recorder_factory=None,
        processor=lambda session: SessionResult.success(session.sequence),
        temp_path_factory=lambda: tmp_path / "failed-admission.wav",
        max_in_flight=1,
    )
    try:
        with pytest.raises(RecordingQueueError):
            queue.start()
        assert queue.start().sequence == 1
    finally:
        queue.close()


def test_start_exception_after_recorder_creation_releases_reservation(tmp_path):
    recorder = FakeRecorder()
    wav_path = tmp_path / "clock-failure.wav"

    def temp_path():
        wav_path.touch()
        return wav_path

    queue = RecordingQueue(
        recorder_factory=lambda: recorder,
        fallback_recorder_factory=None,
        processor=lambda session: SessionResult.success(session.sequence),
        clock=lambda: (_ for _ in ()).throw(RuntimeError("clock failed")),
        temp_path_factory=temp_path,
        max_in_flight=1,
    )
    try:
        with pytest.raises(RecordingQueueError) as error:
            queue.start()
        assert error.value.stage == "start"
        assert recorder.cancelled is True
        assert wav_path.exists() is False
        assert queue._in_flight == 0
        queue._clock = lambda: 1.0
        assert queue.start().sequence == 1
    finally:
        queue.close()


def test_close_releases_active_capacity_once(tmp_path):
    recorder = FakeRecorder()
    queue = RecordingQueue(
        recorder_factory=lambda: recorder,
        fallback_recorder_factory=None,
        processor=lambda session: SessionResult.success(session.sequence),
        temp_path_factory=lambda: tmp_path / "active-close.wav",
        max_in_flight=1,
    )
    queue.start()
    queue.close()
    queue.close()

    assert recorder.cancelled is True
    assert queue._in_flight == 0


def test_completion_and_close_do_not_release_capacity_twice(tmp_path):
    processing = threading.Event()
    allow_processing = threading.Event()

    def processor(session):
        processing.set()
        assert allow_processing.wait(2)
        return SessionResult.success(session.sequence)

    queue = RecordingQueue(
        recorder_factory=FakeRecorder,
        fallback_recorder_factory=None,
        processor=processor,
        temp_path_factory=lambda: tmp_path / "once.wav",
        max_in_flight=1,
    )
    try:
        queue.start()
        completion = queue.release(False, {})
        assert processing.wait(2)
        allow_processing.set()
        completion.result(timeout=2)
        assert queue._in_flight == 0
    finally:
        allow_processing.set()
        queue.close()
    assert queue._in_flight == 0


def test_finalizer_start_failure_cancels_and_cleans_then_next_capture_works(
    tmp_path, monkeypatch
):
    recorders = []
    original_start = threading.Thread.start
    fail_finalizer = True

    def recorder_factory():
        recorder = FakeRecorder()
        recorders.append(recorder)
        return recorder

    def start_thread(thread):
        if fail_finalizer and thread.name.startswith("voisr-capture-"):
            raise RuntimeError("thread unavailable")
        return original_start(thread)

    def temp_path():
        path = tmp_path / f"thread-{len(recorders) + 1}.wav"
        path.touch()
        return path

    monkeypatch.setattr(threading.Thread, "start", start_thread)
    queue = RecordingQueue(
        recorder_factory=recorder_factory,
        fallback_recorder_factory=None,
        processor=lambda session: SessionResult.success(session.sequence),
        temp_path_factory=temp_path,
        max_in_flight=1,
    )
    try:
        first_session = queue.start()
        failed = queue.release(False, {})
        failed_result = failed.result(timeout=2)
        assert failed_result.stage == "stop"
        assert recorders[0].cancelled is True
        assert first_session.wav_path.exists() is False
        assert queue._in_flight == 0

        fail_finalizer = False
        queue.start()
        assert queue.release(False, {}).result(timeout=2).stage is None
    finally:
        queue.close()


def test_stop_failure_cancels_cleans_and_allows_following_capture(tmp_path):
    recorders = [ExplodingStopRecorder(), FakeRecorder()]

    def temp_path():
        path = tmp_path / f"stop-{len(recorders) + 1}.wav"
        path.touch()
        return path

    queue = RecordingQueue(
        recorder_factory=lambda: recorders.pop(0),
        fallback_recorder_factory=None,
        processor=lambda session: SessionResult.success(session.sequence),
        temp_path_factory=temp_path,
        max_in_flight=1,
    )
    try:
        first_session = queue.start()
        failed = queue.release(False, {})
        assert failed.result(timeout=2).stage == "stop"
        assert first_session.recorder.cancelled is True
        assert first_session.wav_path.exists() is False
        assert queue._in_flight == 0

        queue.start()
        assert queue.release(False, {}).result(timeout=2).stage is None
    finally:
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


def test_start_failure_cancels_both_recorders_and_removes_wav(tmp_path):
    primary = ExplodingStartRecorder()
    fallback = ExplodingStartRecorder()
    wav_path = tmp_path / "failed-start.wav"

    def allocate_wav():
        wav_path.touch()
        return wav_path

    queue = RecordingQueue(
        recorder_factory=lambda: primary,
        fallback_recorder_factory=lambda: fallback,
        processor=lambda session: SessionResult.success(session.sequence),
        temp_path_factory=allocate_wav,
    )
    try:
        try:
            queue.start()
        except RecordingQueueError as error:
            assert error.stage == "start"
        else:
            raise AssertionError("start should report recorder failure")

        assert primary.cancelled is True
        assert fallback.cancelled is True
        assert wav_path.exists() is False
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


def test_concurrent_close_waits_for_owner_and_releases_both_callers(tmp_path):
    process_started = threading.Event()
    allow_process = threading.Event()
    first_close_waiting = threading.Event()
    second_close_finished = threading.Event()
    errors = []

    def processor(session):
        process_started.set()
        assert allow_process.wait(2)
        return SessionResult.success(session.sequence, text="done")

    queue = RecordingQueue(
        recorder_factory=FakeRecorder,
        fallback_recorder_factory=None,
        processor=processor,
        temp_path_factory=lambda: tmp_path / "concurrent-close.wav",
    )
    released = queue.start()
    completion = queue.release(False, {})
    assert process_started.wait(2)

    pending_join = queue._pending.join

    def wait_for_pending():
        first_close_waiting.set()
        return pending_join()

    queue._pending.join = wait_for_pending

    def close_queue(finished=None):
        try:
            queue.close()
        except Exception as error:
            errors.append(error)
        finally:
            if finished is not None:
                finished.set()

    first_closer = threading.Thread(target=close_queue)
    first_closer.start()
    assert first_close_waiting.wait(2)

    second_closer = threading.Thread(target=close_queue, args=(second_close_finished,))
    second_closer.start()

    try:
        assert second_close_finished.wait(0.1) is False
    finally:
        allow_process.set()
        first_closer.join(2)
        second_closer.join(2)

    assert completion.result(timeout=2).to_api_dict()["text"] == "done"
    assert errors == []
    assert first_closer.is_alive() is False
    assert second_closer.is_alive() is False
    assert second_close_finished.is_set()
    assert queue._finalizers == set()
    assert queue._worker.is_alive() is False
    assert released.wav_path.exists() is False
    assert queue._in_flight == 0


def test_temp_wav_failure_is_a_structured_start_error(tmp_path):
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
