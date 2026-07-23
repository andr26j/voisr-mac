import contextlib
import json
import multiprocessing
import threading

from voisr.history import HistoryStore


def _add_many(path, prefix, count, start_event):
    start_event.wait()
    store = HistoryStore(path)
    for index in range(count):
        store.add(f"{prefix}-{index}", 1.0)


def test_add_and_list(tmp_path):
    h = HistoryStore(tmp_path / "h.jsonl")
    h.add("Hello world today", duration_s=2.0)
    items = h.list()
    assert len(items) == 1
    assert items[0]["text"] == "Hello world today"
    assert items[0]["words"] == 3
    assert "id" in items[0] and "ts" in items[0]


def test_list_newest_first(tmp_path):
    h = HistoryStore(tmp_path / "h.jsonl")
    h.add("first", 1.0)
    h.add("second", 1.0)
    assert h.list()[0]["text"] == "second"


def test_delete(tmp_path):
    h = HistoryStore(tmp_path / "h.jsonl")
    h.add("keep", 1.0)
    victim = h.add("remove", 1.0)["id"]
    h.delete(victim)
    assert [i["text"] for i in h.list()] == ["keep"]


def test_stats(tmp_path):
    h = HistoryStore(tmp_path / "h.jsonl")
    h.add("one two three four five six", duration_s=3.0)  # 6 words in 3s = 120 wpm
    s = h.stats()
    assert s["total_words"] == 6
    assert s["dictations"] == 1
    assert s["avg_wpm"] == 120
    assert s["streak_days"] == 1


def test_corrupt_line_is_skipped(tmp_path):
    h = HistoryStore(tmp_path / "h.jsonl")
    h.add("good", 1.0)
    with open(h.path, "a") as f:
        f.write('{"torn line\n')  # crash mid-write
    h.add("after", 1.0)
    assert [i["text"] for i in h.list()] == ["after", "good"]


def test_stats_empty(tmp_path):
    s = HistoryStore(tmp_path / "h.jsonl").stats()
    assert s == {"total_words": 0, "dictations": 0, "avg_wpm": 0, "streak_days": 0}


def test_two_instances_in_threads_do_not_lose_additions(tmp_path):
    path = tmp_path / "h.jsonl"
    start = threading.Event()

    def add(text):
        start.wait()
        HistoryStore(path).add(text, 1.0)

    threads = [
        threading.Thread(target=add, args=(text,))
        for text in ("first", "second")
    ]
    for thread in threads:
        thread.start()
    start.set()
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()

    assert {item["text"] for item in HistoryStore(path).list()} == {
        "first", "second"
    }


def test_independent_process_writers_keep_every_record(tmp_path):
    path = tmp_path / "h.jsonl"
    context = multiprocessing.get_context("fork")
    start = context.Event()
    processes = [
        context.Process(target=_add_many, args=(path, f"p{index}", 10, start))
        for index in range(8)
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    items = HistoryStore(path).list()
    assert len(items) == 80
    assert len({item["text"] for item in items}) == 80
    assert all(isinstance(json.loads(line), dict)
               for line in path.read_text().splitlines())


def test_delete_waiting_on_another_instance_keeps_both_mutations(tmp_path, monkeypatch):
    path = tmp_path / "h.jsonl"
    store = HistoryStore(path)
    store.add("keep", 1.0)
    victim_id = store.add("remove", 1.0)["id"]
    adder = HistoryStore(path)
    deleter = HistoryStore(path)
    write_entered = threading.Event()
    release_write = threading.Event()
    delete_attempted = threading.Event()
    add_done = threading.Event()
    delete_done = threading.Event()
    errors = []
    original_write = adder._write_atomic
    original_lock = deleter._mutation_lock

    def paused_write(items):
        write_entered.set()
        assert release_write.wait(timeout=2)
        original_write(items)

    @contextlib.contextmanager
    def signaling_lock():
        delete_attempted.set()
        with original_lock():
            yield

    monkeypatch.setattr(adder, "_write_atomic", paused_write)
    monkeypatch.setattr(deleter, "_mutation_lock", signaling_lock)

    def add_new():
        try:
            adder.add("new", 1.0)
        except BaseException as error:
            errors.append(error)
        finally:
            add_done.set()

    def delete_victim():
        try:
            deleter.delete(victim_id)
        except BaseException as error:
            errors.append(error)
        finally:
            delete_done.set()

    add_thread = threading.Thread(target=add_new)
    delete_thread = threading.Thread(target=delete_victim)
    add_thread.start()
    assert write_entered.wait(timeout=2)
    delete_thread.start()
    assert delete_attempted.wait(timeout=2)
    try:
        assert not delete_done.is_set()
    finally:
        release_write.set()

    for thread, done in ((add_thread, add_done), (delete_thread, delete_done)):
        assert done.wait(timeout=2)
        thread.join(timeout=2)
        assert not thread.is_alive()
    assert not errors
    assert {item["text"] for item in store.list()} == {"keep", "new"}


def test_reader_sees_complete_snapshot_while_atomic_write_is_paused(tmp_path,
                                                                    monkeypatch):
    path = tmp_path / "h.jsonl"
    store = HistoryStore(path)
    store.add("old", 1.0)
    writer = HistoryStore(path)
    write_entered = threading.Event()
    release_write = threading.Event()
    write_done = threading.Event()
    errors = []
    original_write = writer._write_atomic

    def paused_write(items):
        write_entered.set()
        assert release_write.wait(timeout=2)
        original_write(items)

    monkeypatch.setattr(writer, "_write_atomic", paused_write)

    def add_new():
        try:
            writer.add("new", 1.0)
        except BaseException as error:
            errors.append(error)
        finally:
            write_done.set()

    thread = threading.Thread(target=add_new)
    thread.start()
    assert write_entered.wait(timeout=2)
    try:
        before_replace = [item["text"] for item in store.list()]
    finally:
        release_write.set()

    assert write_done.wait(timeout=2)
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert not errors
    after_replace = [item["text"] for item in store.list()]
    assert before_replace == ["old"]
    assert after_replace == ["new", "old"]
