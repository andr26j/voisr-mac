import contextlib
import json
import multiprocessing
import os
import stat
import threading
from pathlib import Path

import pytest

import voisr.history as history_module
from voisr.history import HistoryStore


def _add_many(path, prefix, count, start_event):
    start_event.wait()
    store = HistoryStore(path)
    for index in range(count):
        store.add(f"{prefix}-{index}", 1.0)


def _add_from_store(store, text):
    store.add(text, 1.0)


def _enter_nested_mutation_lock(path):
    store = HistoryStore(path)
    with store._mutation_lock():
        with store._mutation_lock():
            pass


def _exit_if_fd_is_not_file(fd, device, inode):
    try:
        descriptor_stat = os.fstat(fd)
    except OSError:
        raise SystemExit(1)
    if (descriptor_stat.st_dev, descriptor_stat.st_ino) != (device, inode):
        raise SystemExit(1)
    raise SystemExit(0)


def _exit_if_fd_is_file(fd, device, inode):
    try:
        descriptor_stat = os.fstat(fd)
    except OSError:
        raise SystemExit(0)
    if (descriptor_stat.st_dev, descriptor_stat.st_ino) == (device, inode):
        raise SystemExit(1)
    raise SystemExit(0)


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


def test_wrong_shape_legacy_values_are_ignored_and_valid_records_round_trip(tmp_path):
    path = tmp_path / "h.jsonl"
    oldest = {
        "id": "oldest-id",
        "ts": 1_700_000_000.0,
        "text": "oldest dictation",
        "words": 2,
        "duration_s": 1.5,
    }
    legacy = {
        "id": "legacy-id",
        "ts": 1_700_000_001.0,
        "text": "legacy dictation",
        "words": 2,
        "duration_s": 1.5,
    }
    invalid_values = [
        [],
        None,
        "not a record",
        {"id": "missing-fields"},
        {"id": "bad-ts", "ts": "now", "text": "text", "words": 1,
         "duration_s": 1.0},
        {"id": "bad-words", "ts": 1.0, "text": "text", "words": True,
         "duration_s": 1.0},
        {"id": "bad-duration", "ts": 1.0, "text": "text", "words": 1,
         "duration_s": -1.0},
    ]
    path.write_text(
        "".join(json.dumps(value) + "\n" for value in [oldest, *invalid_values, legacy]),
        encoding="utf-8",
    )
    store = HistoryStore(path)

    assert [item["id"] for item in store.list()] == ["legacy-id", "oldest-id"]
    assert store.stats()["total_words"] == 4
    store.delete("not-present")
    added = store.add("new dictation", 1.0)
    store.delete(added["id"])

    assert path.read_text(encoding="utf-8") == (
        json.dumps(oldest) + "\n" + json.dumps(legacy) + "\n"
    )


def test_replace_failure_preserves_history_bytes_and_removes_temp_file(tmp_path,
                                                                         monkeypatch):
    path = tmp_path / "h.jsonl"
    store = HistoryStore(path)
    store.add("committed", 1.0)
    before = path.read_bytes()

    def fail_replace(*args):
        raise OSError("replace failed")

    monkeypatch.setattr(history_module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        store.add("not committed", 1.0)

    assert path.read_bytes() == before
    assert list(tmp_path.glob(".h.jsonl.*.tmp")) == []


def test_pre_replace_fsync_failure_preserves_history_bytes_and_removes_temp_file(
        tmp_path, monkeypatch):
    path = tmp_path / "h.jsonl"
    store = HistoryStore(path)
    store.add("committed", 1.0)
    before = path.read_bytes()
    original_fsync = history_module.os.fsync
    calls = 0

    def fail_first_fsync(fd):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("temp sync failed")
        original_fsync(fd)

    monkeypatch.setattr(history_module.os, "fsync", fail_first_fsync)

    with pytest.raises(OSError, match="temp sync failed"):
        store.add("not committed", 1.0)

    assert path.read_bytes() == before
    assert list(tmp_path.glob(".h.jsonl.*.tmp")) == []


def test_post_replace_directory_fsync_error_is_visible_to_caller(tmp_path,
                                                                   monkeypatch):
    path = tmp_path / "h.jsonl"
    store = HistoryStore(path)
    original_fsync = history_module.os.fsync
    calls = 0

    def fail_directory_fsync(fd):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("directory sync failed")
        original_fsync(fd)

    monkeypatch.setattr(history_module.os, "fsync", fail_directory_fsync)

    with pytest.raises(OSError, match="directory sync failed"):
        store.add("replaced but not durable", 1.0)

    assert [item["text"] for item in store.list()] == ["replaced but not durable"]


def test_store_creates_private_parent_lock_and_history_files(tmp_path):
    path = tmp_path / "owned" / "h.jsonl"

    store = HistoryStore(path)

    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(store.lock_path.stat().st_mode) == 0o600
    store.add("private", 1.0)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_store_keeps_existing_parent_mode_while_tightening_private_files(tmp_path):
    parent = tmp_path / "existing"
    parent.mkdir()
    parent.chmod(0o755)
    path = parent / "h.jsonl"
    path.write_text("", encoding="utf-8")
    path.chmod(0o644)
    lock_path = parent / "h.jsonl.lock"
    lock_path.write_text("", encoding="utf-8")
    lock_path.chmod(0o644)

    HistoryStore(path)

    assert stat.S_IMODE(parent.stat().st_mode) == 0o755
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600


def test_store_creates_each_missing_parent_with_private_mode(tmp_path):
    path = tmp_path / "missing" / "nested" / "parent" / "h.jsonl"
    old_umask = os.umask(0o022)
    try:
        HistoryStore(path)
    finally:
        os.umask(old_umask)

    for directory in (tmp_path / "missing", tmp_path / "missing" / "nested",
                      path.parent):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700


def test_store_does_not_change_existing_symlink_parent_target_mode(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    target.chmod(0o755)
    alias = tmp_path / "alias"
    alias.symlink_to(target, target_is_directory=True)

    HistoryStore(alias / "new-parent" / "h.jsonl")

    assert stat.S_IMODE(target.stat().st_mode) == 0o755
    assert stat.S_IMODE((target / "new-parent").stat().st_mode) == 0o700


def test_huge_integer_timestamp_is_ignored_without_bricking_history_operations(
        tmp_path):
    path = tmp_path / "h.jsonl"
    valid = {
        "id": "valid-id",
        "ts": 1_700_000_000.0,
        "text": "valid record",
        "words": 2,
        "duration_s": 1.0,
    }
    huge_timestamp = {**valid, "id": "huge-id", "ts": 10 ** 1000}
    path.write_text(
        "".join(json.dumps(item) + "\n" for item in (valid, huge_timestamp)),
        encoding="utf-8",
    )
    store = HistoryStore(path)

    assert [item["id"] for item in store.list()] == ["valid-id"]
    assert store.stats()["dictations"] == 1
    store.delete("valid-id")
    added = store.add("after huge timestamp", 1.0)
    store.delete(added["id"])

    assert store.list() == []


def test_unrepresentable_timestamp_stays_in_history_but_not_streak_days(tmp_path):
    path = tmp_path / "h.jsonl"
    valid = {
        "id": "valid-id",
        "ts": 1_700_000_000.0,
        "text": "valid record",
        "words": 2,
        "duration_s": 1.0,
    }
    distant = {**valid, "id": "distant-id", "ts": 1e308,
               "text": "distant record", "words": 3}
    path.write_text(
        "".join(json.dumps(item) + "\n" for item in (valid, distant)),
        encoding="utf-8",
    )
    store = HistoryStore(path)

    assert [item["id"] for item in store.list()] == ["distant-id", "valid-id"]
    assert store.stats() == {
        "total_words": 5,
        "dictations": 2,
        "avg_wpm": 150,
        "streak_days": 0,
    }
    store.delete("valid-id")
    added = store.add("after distant timestamp", 1.0)
    store.delete(added["id"])

    assert [item["id"] for item in store.list()] == ["distant-id"]


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
    try:
        for process in processes:
            process.start()
        start.set()
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=2)
            assert not process.is_alive()

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


def test_writing_through_file_symlink_updates_target_without_replacing_link(tmp_path):
    target = tmp_path / "target.jsonl"
    alias = tmp_path / "alias.jsonl"
    HistoryStore(target).add("target", 1.0)
    alias.symlink_to(target)

    HistoryStore(alias).add("alias", 1.0)

    assert alias.is_symlink()
    assert {item["text"] for item in HistoryStore(target).list()} == {
        "target", "alias"
    }


def test_broken_file_symlink_writes_to_its_target_without_replacing_link(tmp_path):
    target = tmp_path / "future.jsonl"
    alias = tmp_path / "alias.jsonl"
    alias.symlink_to(target)

    HistoryStore(alias).add("first", 1.0)

    assert alias.is_symlink()
    assert [item["text"] for item in HistoryStore(target).list()] == ["first"]


def test_real_and_symlink_paths_share_concurrent_mutation_path(tmp_path):
    target = tmp_path / "target.jsonl"
    alias = tmp_path / "alias.jsonl"
    alias.symlink_to(target)
    start = threading.Event()
    errors = []

    def add(path, text):
        try:
            start.wait()
            HistoryStore(path).add(text, 1.0)
        except BaseException as error:
            errors.append(error)

    threads = [
        threading.Thread(target=add, args=(path, text))
        for path, text in ((target, "real"), (alias, "alias"))
    ]
    for thread in threads:
        thread.start()
    start.set()
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()

    assert not errors
    assert alias.is_symlink()
    assert {item["text"] for item in HistoryStore(target).list()} == {
        "real", "alias"
    }


@pytest.mark.filterwarnings(
    "ignore:This process .* is multi-threaded.*:DeprecationWarning"
)
def test_fork_child_does_not_inherit_another_threads_path_lock(tmp_path):
    path = tmp_path / "h.jsonl"
    store = HistoryStore(path)
    held = threading.Event()
    release = threading.Event()

    def hold_path_lock():
        with history_module._thread_lock_for(store.path):
            held.set()
            assert release.wait(timeout=2)

    holder = threading.Thread(target=hold_path_lock)
    holder.start()
    assert held.wait(timeout=2)
    context = multiprocessing.get_context("fork")
    process = context.Process(target=_add_from_store, args=(store, "child"))
    try:
        process.start()
        release.set()
        process.join(timeout=2)
        assert not process.is_alive()
        assert process.exitcode == 0
    finally:
        release.set()
        holder.join(timeout=2)
        if process.is_alive():
            process.terminate()
            process.join(timeout=2)
        assert not holder.is_alive()
        assert not process.is_alive()

    assert [item["text"] for item in store.list()] == ["child"]


def test_nested_mutation_lock_completes_within_timeout(tmp_path):
    context = multiprocessing.get_context("fork")
    process = context.Process(target=_enter_nested_mutation_lock,
                              args=(tmp_path / "h.jsonl",))
    try:
        process.start()
        process.join(timeout=2)
        assert not process.is_alive()
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=2)
        assert not process.is_alive()


def test_nested_mutation_lock_reuses_one_sidecar_fd(tmp_path, monkeypatch):
    store = HistoryStore(tmp_path / "h.jsonl")
    opened = []
    original_open = history_module.os.open

    def count_open(*args, **kwargs):
        fd = original_open(*args, **kwargs)
        opened.append(fd)
        return fd

    monkeypatch.setattr(history_module.os, "open", count_open)
    monkeypatch.setattr(history_module.fcntl, "flock", lambda *args: None)

    with store._mutation_lock():
        with store._mutation_lock():
            pass

    assert len(opened) == 1


def test_mutation_lock_closes_fd_when_unlock_fails(tmp_path, monkeypatch):
    store = HistoryStore(tmp_path / "h.jsonl")
    closed = []
    original_close = history_module.os.close
    original_flock = history_module.fcntl.flock

    def track_close(fd):
        closed.append(fd)
        original_close(fd)

    def fail_unlock(fd, operation):
        if operation == history_module.fcntl.LOCK_UN:
            raise OSError("unlock failed")
        return original_flock(fd, operation)

    monkeypatch.setattr(history_module.os, "close", track_close)
    monkeypatch.setattr(history_module.fcntl, "flock", fail_unlock)

    with pytest.raises(OSError, match="unlock failed"):
        with store._mutation_lock():
            pass

    assert len(closed) == 1


def test_store_path_stays_relative_while_writes_use_symlink_target(tmp_path,
                                                                    monkeypatch):
    target = tmp_path / "target.jsonl"
    alias = tmp_path / "alias.jsonl"
    alias.symlink_to(target)
    monkeypatch.chdir(tmp_path)
    supplied_path = Path("alias.jsonl")

    store = HistoryStore(supplied_path)
    store.add("through alias", 1.0)

    assert store.path == supplied_path
    assert store.lock_path == target.with_name("target.jsonl.lock")
    assert alias.is_symlink()
    assert [item["text"] for item in HistoryStore(target).list()] == [
        "through alias"
    ]


@pytest.mark.filterwarnings(
    "ignore:This process .* is multi-threaded.*:DeprecationWarning"
)
def test_fork_does_not_close_reused_fd_after_sidecar_close(tmp_path, monkeypatch):
    store = HistoryStore(tmp_path / "h.jsonl")
    sidecar_fds = []
    reopened_fds = []
    sidecar_closed = threading.Event()
    allow_close = threading.Event()
    fork_entered = threading.Event()
    allow_fork = threading.Event()
    worker_done = threading.Event()
    errors = []
    original_track = history_module._track_lock_fd
    original_close = history_module.os.close
    original_open = history_module.os.open
    original_fork = history_module.os.fork
    parent_pid = os.getpid()

    def track_sidecar(fd):
        sidecar_fds.append(fd)
        original_track(fd)

    def pause_after_close(fd):
        if os.getpid() != parent_pid or fd not in sidecar_fds:
            return original_close(fd)
        original_close(fd)
        reopened_fd = original_open(os.devnull, os.O_RDONLY)
        reopened_fds.append(reopened_fd)
        assert reopened_fd == fd
        sidecar_closed.set()
        assert allow_close.wait(timeout=2)

    def pause_before_fork():
        fork_entered.set()
        assert allow_fork.wait(timeout=2)
        return original_fork()

    monkeypatch.setattr(history_module, "_track_lock_fd", track_sidecar)
    monkeypatch.setattr(history_module.os, "close", pause_after_close)
    monkeypatch.setattr(history_module.os, "fork", pause_before_fork)

    def release_lock():
        try:
            with store._mutation_lock():
                pass
        except BaseException as error:
            errors.append(error)
        finally:
            worker_done.set()

    worker = threading.Thread(target=release_lock)
    context = multiprocessing.get_context("fork")
    process = None
    starter_done = threading.Event()

    def start_process():
        nonlocal process
        try:
            assert reopened_fds
            descriptor_stat = os.fstat(reopened_fds[0])
            process = context.Process(target=_exit_if_fd_is_not_file,
                                      args=(reopened_fds[0], descriptor_stat.st_dev,
                                            descriptor_stat.st_ino))
            process.start()
        except BaseException as error:
            errors.append(error)
        finally:
            starter_done.set()

    worker.start()
    assert sidecar_closed.wait(timeout=2)
    starter = threading.Thread(target=start_process)
    starter.start()
    assert fork_entered.wait(timeout=2)
    try:
        allow_fork.set()
        allow_close.set()
        assert starter_done.wait(timeout=2)
        assert worker_done.wait(timeout=2)
        assert process is not None
        process.join(timeout=2)
        assert process.exitcode == 0
        assert not errors
    finally:
        allow_fork.set()
        allow_close.set()
        starter.join(timeout=2)
        worker.join(timeout=2)
        if process is not None and process.is_alive():
            process.terminate()
            process.join(timeout=2)
        if reopened_fds:
            original_close(reopened_fds[0])
        assert not starter.is_alive()
        assert not worker.is_alive()
        assert process is None or not process.is_alive()


@pytest.mark.filterwarnings(
    "ignore:This process .* is multi-threaded.*:DeprecationWarning"
)
def test_fork_does_not_inherit_sidecar_fd_before_tracking(tmp_path, monkeypatch):
    store = HistoryStore(tmp_path / "h.jsonl")
    opened_fds = []
    opened = threading.Event()
    allow_tracking = threading.Event()
    fork_entered = threading.Event()
    allow_fork = threading.Event()
    worker_done = threading.Event()
    errors = []
    original_track = history_module._track_lock_fd
    original_fork = history_module.os.fork

    def pause_before_tracking(fd):
        opened_fds.append(fd)
        opened.set()
        assert allow_tracking.wait(timeout=2)
        original_track(fd)

    def pause_before_fork():
        fork_entered.set()
        assert allow_fork.wait(timeout=2)
        return original_fork()

    monkeypatch.setattr(history_module, "_track_lock_fd", pause_before_tracking)
    monkeypatch.setattr(history_module.os, "fork", pause_before_fork)

    def add_item():
        try:
            store.add("item", 1.0)
        except BaseException as error:
            errors.append(error)
        finally:
            worker_done.set()

    worker = threading.Thread(target=add_item)
    context = multiprocessing.get_context("fork")
    process = None
    starter_done = threading.Event()

    def start_process():
        nonlocal process
        try:
            assert opened_fds
            descriptor_stat = os.fstat(opened_fds[0])
            process = context.Process(target=_exit_if_fd_is_file,
                                      args=(opened_fds[0], descriptor_stat.st_dev,
                                            descriptor_stat.st_ino))
            process.start()
        except BaseException as error:
            errors.append(error)
        finally:
            starter_done.set()

    worker.start()
    assert opened.wait(timeout=2)
    starter = threading.Thread(target=start_process)
    starter.start()
    assert fork_entered.wait(timeout=2)
    try:
        allow_fork.set()
        allow_tracking.set()
        assert starter_done.wait(timeout=2)
        assert worker_done.wait(timeout=2)
        assert process is not None
        process.join(timeout=2)
        assert process.exitcode == 0
        assert not errors
    finally:
        allow_fork.set()
        allow_tracking.set()
        starter.join(timeout=2)
        worker.join(timeout=2)
        if process is not None and process.is_alive():
            process.terminate()
            process.join(timeout=2)
        assert not starter.is_alive()
        assert not worker.is_alive()
        assert process is None or not process.is_alive()


@pytest.mark.filterwarnings(
    "ignore:This process .* is multi-threaded.*:DeprecationWarning"
)
def test_real_and_symlink_process_writers_share_target_sidecar(tmp_path):
    target = tmp_path / "target.jsonl"
    alias = tmp_path / "alias.jsonl"
    alias.symlink_to(target)
    context = multiprocessing.get_context("fork")
    start = context.Event()
    processes = [
        context.Process(target=_add_many, args=(path, prefix, 10, start))
        for path, prefix in ((target, "real"), (alias, "alias"))
    ]
    try:
        for process in processes:
            process.start()
        start.set()
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=2)
            assert not process.is_alive()

    items = HistoryStore(target).list()
    assert alias.is_symlink()
    assert len(items) == 20
    assert {item["text"] for item in items} == {
        *(f"real-{index}" for index in range(10)),
        *(f"alias-{index}" for index in range(10)),
    }
    assert target.with_name("target.jsonl.lock").exists()
    assert not alias.with_name("alias.jsonl.lock").exists()
