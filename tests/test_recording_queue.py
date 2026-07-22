import threading

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
