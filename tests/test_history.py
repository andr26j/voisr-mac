import contextlib
import gc
import json
import multiprocessing
import os
import stat
import threading
import time
import weakref
from datetime import date, timedelta
from fractions import Fraction
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


def _construct_store_after_start(path, start_event, result_queue):
    start_event.wait()
    try:
        HistoryStore(path).close()
    except BaseException as error:
        result_queue.put(repr(error))
    else:
        result_queue.put(None)


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

    def fail_replace(*args, **kwargs):
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
        mode = stat.S_IMODE(directory.stat().st_mode)
        assert mode & 0o077 == 0
        assert mode == 0o700


def test_store_recovers_owner_restrictive_umask_with_usable_private_parent(
        tmp_path):
    path = tmp_path / "owner-restrictive" / "h.jsonl"
    old_umask = os.umask(0o700)
    try:
        with pytest.raises(PermissionError):
            HistoryStore(path)
    finally:
        os.umask(old_umask)
        if path.parent.exists():
            path.parent.chmod(0o700)


def test_store_does_not_change_existing_symlink_parent_target_mode(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    target.chmod(0o755)
    alias = tmp_path / "alias"
    alias.symlink_to(target, target_is_directory=True)

    HistoryStore(alias / "new-parent" / "h.jsonl")

    assert stat.S_IMODE(target.stat().st_mode) == 0o755
    assert stat.S_IMODE((target / "new-parent").stat().st_mode) == 0o700


def test_store_does_not_chmod_victim_after_created_parent_is_replaced(
        tmp_path, monkeypatch):
    victim = tmp_path / "unrelated"
    victim.mkdir()
    victim.chmod(0o755)
    parent = tmp_path / "race"
    path = parent / "h.jsonl"
    original_mkdir = history_module.os.mkdir

    def create_then_replace_parent(directory, *args, **kwargs):
        result = original_mkdir(directory, *args, **kwargs)
        if directory == parent.name:
            parent.rmdir()
            victim.rename(parent)
        return result

    monkeypatch.setattr(history_module.os, "mkdir", create_then_replace_parent)

    with pytest.raises(OSError):
        HistoryStore(path)

    assert stat.S_IMODE(parent.stat().st_mode) == 0o755
    assert not (parent / "h.jsonl").exists()
    assert not (parent / "h.jsonl.lock").exists()


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
    stats = store.stats()
    assert stats["total_words"] == 5
    assert stats["dictations"] == 2
    assert stats["avg_wpm"] == 150
    assert stats["streak_days"] == 0
    assert "total_seconds" in stats
    assert "time_saved_seconds" in stats
    assert "daily" in stats
    store.delete("valid-id")
    added = store.add("after distant timestamp", 1.0)
    store.delete(added["id"])

    assert [item["id"] for item in store.list()] == ["distant-id"]


def test_stats_empty(tmp_path):
    s = HistoryStore(tmp_path / "h.jsonl").stats()
    assert s["total_words"] == 0
    assert s["dictations"] == 0
    assert s["avg_wpm"] == 0
    assert s["streak_days"] == 0
    assert s["total_seconds"] == 0
    assert s["time_saved_seconds"] == 0
    assert len(s["daily"]) == 14


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


# These fork on purpose: fork safety is what they test, so Python 3.14's
# "multi-threaded process, use of fork()" warning is expected (L10).
@pytest.mark.filterwarnings(
    "ignore:This process .* is multi-threaded, use of fork:DeprecationWarning")
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


# These fork on purpose: fork safety is what they test, so Python 3.14's
# "multi-threaded process, use of fork()" warning is expected (L10).
@pytest.mark.filterwarnings(
    "ignore:This process .* is multi-threaded, use of fork:DeprecationWarning")
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

    assert len(closed) == 2  # sidecar plus its duplicated parent-dir lease


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


@pytest.fixture
def no_collection_during_test():
    """A collection on the main thread runs the __del__ of stores left by
    earlier tests, which waits on the fork guard. If this test's paused worker
    holds that guard, the main thread can't release it until the wait times out."""
    gc.collect()
    gc.disable()
    yield
    gc.enable()


@pytest.mark.filterwarnings(
    "ignore:This process .* is multi-threaded.*:DeprecationWarning"
)
@pytest.mark.usefixtures("no_collection_during_test")
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


def test_lock_creation_retries_only_the_create_then_open_race(tmp_path,
                                                               monkeypatch):
    store = HistoryStore(tmp_path / "h.jsonl")
    store.lock_path.unlink()
    original_open = history_module.os.open
    attempts = 0

    def create_then_disappear(name, flags, *args, **kwargs):
        nonlocal attempts
        if name == store.lock_path.name and flags & os.O_CREAT:
            attempts += 1
            if attempts == 1 and flags & os.O_EXCL:
                raise FileExistsError()
            if attempts == 1:
                raise FileNotFoundError()
        return original_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(history_module.os, "open", create_then_disappear)

    store._create_or_tighten_lock_file()

    assert attempts == 2
    assert store.lock_path.exists()


@pytest.mark.filterwarnings(
    "ignore:This process .* is multi-threaded.*:DeprecationWarning"
)
def test_repeated_concurrent_constructors_create_one_safe_sidecar(tmp_path):
    path = tmp_path / "h.jsonl"
    context = multiprocessing.get_context("fork")

    for _ in range(4):
        start = context.Event()
        results = context.Queue()
        processes = [
            context.Process(target=_construct_store_after_start,
                            args=(path, start, results))
            for _ in range(12)
        ]
        try:
            for process in processes:
                process.start()
            start.set()
            for process in processes:
                process.join(timeout=5)
                assert process.exitcode == 0
            assert [results.get(timeout=1) for _ in processes] == [None] * len(processes)
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                process.join(timeout=1)
        path.with_name(path.name + ".lock").unlink()


def test_close_does_not_redirect_an_active_operation_to_a_reused_fd(tmp_path,
                                                                     monkeypatch):
    path = tmp_path / "h.jsonl"
    store = HistoryStore(path)
    store.add("old", 1.0)
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    entered = threading.Event()
    release = threading.Event()
    original_open_regular = history_module._open_regular_at
    paused = False

    def pause_history_open(parent_fd, name, *args, **kwargs):
        nonlocal paused
        if name == store._name and not paused:
            paused = True
            entered.set()
            assert release.wait(timeout=2)
        return original_open_regular(parent_fd, name, *args, **kwargs)

    monkeypatch.setattr(history_module, "_open_regular_at", pause_history_open)
    thread = threading.Thread(target=store.add, args=("new", 1.0))
    thread.start()
    assert entered.wait(timeout=2)
    base_fd = store._parent_fd
    store.close()
    reused_fd = os.open(replacement, os.O_RDONLY | os.O_DIRECTORY)
    assert reused_fd == base_fd
    try:
        release.set()
        thread.join(timeout=2)
        assert not thread.is_alive()
    finally:
        release.set()
        os.close(reused_fd)

    assert {item["text"] for item in HistoryStore(path).list()} == {"old", "new"}
    assert not (replacement / "h.jsonl").exists()


def test_spawned_store_does_not_serialize_parent_fd_or_lock(tmp_path):
    path = tmp_path / "h.jsonl"
    store = HistoryStore(path)
    context = multiprocessing.get_context("spawn")
    process = context.Process(target=_add_from_store, args=(store, "spawned"))
    try:
        process.start()
        process.join(timeout=10)
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=2)

    assert [item["text"] for item in store.list()] == ["spawned"]


@pytest.mark.parametrize("kind", ["directory", "fifo"])
def test_special_history_path_is_rejected_without_chmod_or_blocking(tmp_path, kind):
    path = tmp_path / "h.jsonl"
    if kind == "directory":
        path.mkdir(mode=0o755)
    else:
        os.mkfifo(path, 0o644)
    before_mode = stat.S_IMODE(path.lstat().st_mode)

    with pytest.raises(OSError):
        HistoryStore(path)

    assert stat.S_IMODE(path.lstat().st_mode) == before_mode


def test_preexisting_lock_symlink_is_rejected_without_touching_target(tmp_path):
    path = tmp_path / "h.jsonl"
    target = tmp_path / "lock-target"
    target.write_text("do not chmod", encoding="utf-8")
    target.chmod(0o644)
    lock_path = path.with_name(path.name + ".lock")
    lock_path.symlink_to(target)

    with pytest.raises(OSError):
        HistoryStore(path)

    assert lock_path.is_symlink()
    assert stat.S_IMODE(target.stat().st_mode) == 0o644


def test_replaced_lock_symlink_is_rejected_without_touching_target(tmp_path):
    path = tmp_path / "h.jsonl"
    store = HistoryStore(path)
    target = tmp_path / "lock-target"
    target.write_text("do not chmod", encoding="utf-8")
    target.chmod(0o644)
    store.lock_path.unlink()
    store.lock_path.symlink_to(target)

    with pytest.raises(OSError):
        store.add("not written", 1.0)

    assert store.lock_path.is_symlink()
    assert stat.S_IMODE(target.stat().st_mode) == 0o644


def test_store_uses_its_opened_parent_after_parent_name_is_replaced(tmp_path):
    parent = tmp_path / "history-parent"
    parent.mkdir()
    path = parent / "h.jsonl"
    store = HistoryStore(path)
    moved_parent = tmp_path / "history-parent-moved"
    parent.rename(moved_parent)
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    parent.symlink_to(replacement, target_is_directory=True)

    store.add("kept in opened parent", 1.0)

    assert [item["text"] for item in HistoryStore(moved_parent / "h.jsonl").list()] == [
        "kept in opened parent"
    ]
    assert not (replacement / "h.jsonl").exists()


@pytest.mark.parametrize("duration_s", [-1, True, float("nan"), float("inf")])
def test_invalid_add_duration_raises_without_changing_history_bytes(tmp_path,
                                                                     duration_s):
    path = tmp_path / "h.jsonl"
    store = HistoryStore(path)
    store.add("committed", 1.0)
    before = path.read_bytes()

    with pytest.raises(ValueError):
        store.add("not committed", duration_s)

    assert path.read_bytes() == before


def test_stats_handles_huge_words_and_subnormal_durations_exactly(tmp_path):
    words = 10 ** 4_000
    duration_s = float.fromhex("0x0.0000000000001p-1022")
    path = tmp_path / "h.jsonl"
    path.write_text(json.dumps({
        "id": "huge",
        "ts": 1.0,
        "text": "x",
        "words": words,
        "duration_s": duration_s,
    }) + "\n", encoding="utf-8")

    stats = HistoryStore(path).stats()

    assert stats["total_words"] == words
    assert stats["avg_wpm"] == round(Fraction(words * 60, 1) / Fraction(duration_s))


def test_path_lock_registry_releases_unused_locks(tmp_path):
    path = tmp_path / "h.jsonl"
    key = str(path.resolve())
    lock = history_module._thread_lock_for(path)
    reference = weakref.ref(lock)

    del lock
    gc.collect()

    assert reference() is None
    assert key not in history_module._PATH_LOCKS


def test_literal_tilde_path_is_not_expanded(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    supplied_path = Path("~") / "h.jsonl"

    store = HistoryStore(supplied_path)
    store.add("literal tilde", 1.0)

    assert store.path == supplied_path
    assert (tmp_path / "~" / "h.jsonl").exists()


def test_stats_reports_time_saved_against_forty_wpm(tmp_path):
    store = HistoryStore(tmp_path / "h.jsonl")
    # 80 words spoken in 30s. Typing 80 words at 40wpm would take 120s.
    store.add("word " * 80, duration_s=30.0)
    stats = store.stats()
    assert stats["total_seconds"] == 30
    assert stats["time_saved_seconds"] == 90


def test_stats_clamps_time_saved_at_zero(tmp_path):
    store = HistoryStore(tmp_path / "h.jsonl")
    # 4 words in 60s is far slower than typing: must report 0, never negative.
    store.add("one two three four", duration_s=60.0)
    assert store.stats()["time_saved_seconds"] == 0


def test_stats_empty_history_has_all_keys(tmp_path):
    stats = HistoryStore(tmp_path / "h.jsonl").stats()
    assert stats["total_seconds"] == 0
    assert stats["time_saved_seconds"] == 0
    assert len(stats["daily"]) == 14
    assert all(d["words"] == 0 for d in stats["daily"])


def test_stats_daily_is_fourteen_days_zero_filled_ending_today(tmp_path):
    store = HistoryStore(tmp_path / "h.jsonl")
    store.add("alpha beta", duration_s=1.0)
    daily = store.stats()["daily"]
    assert len(daily) == 14
    assert daily[-1]["date"] == date.today().isoformat()
    assert daily[-1]["words"] == 2
    assert daily[0]["date"] == (date.today() - timedelta(days=13)).isoformat()
    assert daily[0]["words"] == 0


def test_stats_daily_ignores_entries_older_than_the_window(tmp_path):
    store = HistoryStore(tmp_path / "h.jsonl")
    item = store.add("old words here", duration_s=1.0)

    def age(items):
        items[0]["ts"] = time.time() - 40 * 86400

    store._mutate(age)
    stats = store.stats()
    assert sum(d["words"] for d in stats["daily"]) == 0
    assert stats["total_words"] == 3  # totals still count it


from voisr.app import DEFAULT_CONFIG


def test_default_config_has_the_new_keys():
    assert DEFAULT_CONFIG["appearance"] == "system"
    assert DEFAULT_CONFIG["launch_at_login"] is False
    assert DEFAULT_CONFIG["model"] is None


def test_restore_puts_a_deleted_item_back_with_its_identity(tmp_path):
    store = HistoryStore(tmp_path / "h.jsonl")
    item = store.add("bring me back", duration_s=2.0)
    store.delete(item["id"])
    assert store.list() == []
    store.restore(item)
    back = store.list()
    assert len(back) == 1
    assert back[0]["id"] == item["id"]
    assert back[0]["ts"] == item["ts"]
    assert back[0]["text"] == "bring me back"


def test_restore_returns_the_record_to_its_original_position(tmp_path):
    """Verify that restore() returns a deleted record to its original position,
    not to the top of the list. With three records, the restored middle record
    must land in the middle again, not at either end."""
    store = HistoryStore(tmp_path / "h.jsonl")

    # Add three records with distinct, increasing timestamps
    oldest_ts = 1_000_000.0
    middle_ts = 1_000_001.0
    newest_ts = 1_000_002.0

    # Add oldest
    oldest_id = store.add("oldest text", duration_s=1.0)["id"]
    def set_oldest_ts(items):
        for item in items:
            if item["id"] == oldest_id:
                item["ts"] = oldest_ts
    store._mutate(set_oldest_ts)

    # Add middle
    middle_id = store.add("middle text", duration_s=1.0)["id"]
    def set_middle_ts(items):
        for item in items:
            if item["id"] == middle_id:
                item["ts"] = middle_ts
    store._mutate(set_middle_ts)

    # Add newest
    newest_id = store.add("newest text", duration_s=1.0)["id"]
    def set_newest_ts(items):
        for item in items:
            if item["id"] == newest_id:
                item["ts"] = newest_ts
    store._mutate(set_newest_ts)

    # Verify all three are present, sorted by ts descending (newest first)
    before_delete = store.list()
    assert len(before_delete) == 3
    assert [item["id"] for item in before_delete] == [newest_id, middle_id, oldest_id]
    assert [item["ts"] for item in before_delete] == [newest_ts, middle_ts, oldest_ts]

    # Capture the middle item before deletion
    middle_to_restore = next(item for item in before_delete if item["id"] == middle_id)

    # Delete the middle record
    store.delete(middle_id)
    assert len(store.list()) == 2
    assert [item["id"] for item in store.list()] == [newest_id, oldest_id]

    # Restore it
    store.restore(middle_to_restore)

    # Verify it's back in the middle position with original timestamp
    after_restore = store.list()
    assert len(after_restore) == 3
    assert [item["id"] for item in after_restore] == [newest_id, middle_id, oldest_id]
    assert [item["ts"] for item in after_restore] == [newest_ts, middle_ts, oldest_ts]

    # Verify the restored record has the original timestamp, not a fresh one
    restored_middle = next(item for item in after_restore if item["id"] == middle_id)
    assert restored_middle["ts"] == middle_ts


def test_restore_is_a_no_op_when_the_id_is_already_present(tmp_path):
    store = HistoryStore(tmp_path / "h.jsonl")
    item = store.add("only once", duration_s=1.0)
    store.restore(item)
    assert len(store.list()) == 1


def test_clear_removes_everything_in_one_write_and_reports_the_count(tmp_path):
    store = HistoryStore(tmp_path / "h.jsonl")
    for n in range(3):
        store.add(f"entry {n}", duration_s=1.0)
    assert store.clear() == 3
    assert store.list() == []
    assert store.stats()["total_words"] == 0


def test_clear_on_empty_history_returns_zero(tmp_path):
    assert HistoryStore(tmp_path / "h.jsonl").clear() == 0


def test_undecodable_line_is_skipped_and_history_stays_usable(tmp_path):
    """M1: one bad byte must not make the whole history unreadable forever."""
    h = HistoryStore(tmp_path / "h.jsonl")
    h.add("good", 1.0)
    with open(h.path, "ab") as f:
        f.write(b'{"id": "x", "ts": 1, "text": "\xff\xfe", "words": 1, "duration_s": 1}\n')
    assert [i["text"] for i in h.list()] == ["good"]
    h.add("after", 1.0)
    assert [i["text"] for i in h.list()] == ["after", "good"]
    assert h.stats()["dictations"] == 2


# ---- M9 (2026-10-12 audit) ----

def _shift_days(store, *days_ago):
    """Move the stored records back by the given whole days, in order."""
    def shift(items):
        for item, n in zip(items, days_ago):
            item["ts"] = time.time() - n * 86400
    store._mutate(shift)


def test_a_streak_survives_until_the_day_ends(tmp_path):
    # Dictated yesterday and the day before, not yet today: the streak is
    # still alive this morning, not zero above a row of lit dots.
    store = HistoryStore(tmp_path / "h.jsonl")
    store.add("one", duration_s=1.0)
    store.add("two", duration_s=1.0)
    _shift_days(store, 1, 2)
    assert store.stats()["streak_days"] == 2


def test_a_streak_with_a_gap_before_yesterday_is_broken(tmp_path):
    store = HistoryStore(tmp_path / "h.jsonl")
    store.add("one", duration_s=1.0)
    _shift_days(store, 2)
    assert store.stats()["streak_days"] == 0


def test_dictating_today_extends_the_streak(tmp_path):
    store = HistoryStore(tmp_path / "h.jsonl")
    store.add("one", duration_s=1.0)
    store.add("two", duration_s=1.0)
    store.add("three", duration_s=1.0)
    _shift_days(store, 0, 1, 2)
    assert store.stats()["streak_days"] == 3


def test_daily_carries_time_saved_per_day(tmp_path):
    # Time saved has its own trend: 40 wpm typing minus speaking time, per
    # day, never below zero. It must not be the word series relabelled.
    store = HistoryStore(tmp_path / "h.jsonl")
    store.add(" ".join(["w"] * 40), duration_s=20.0)   # 60 s typed - 20 s = 40
    store.add("slow", duration_s=30.0)                 # 1.5 s typed - 30 s -> 0
    _shift_days(store, 0, 1)
    daily = store.stats()["daily"]
    assert daily[-1]["saved_seconds"] == 40
    assert daily[-2]["saved_seconds"] == 0
    assert all("saved_seconds" in d for d in daily)


def test_old_lines_without_model_or_timings_stay_valid(tmp_path):
    path = tmp_path / "h.jsonl"
    path.write_text(json.dumps({"id": "old", "ts": 1.0, "text": "hi there",
                                "words": 2, "duration_s": 1.5}) + "\n")
    store = HistoryStore(path)
    assert [i["id"] for i in store.list()] == ["old"]
    assert store.stats()["dictations"] == 1
    assert store.merge_timings("old", {"total": 12.4}) is True
    assert store.list()[0]["timings_ms"] == {"total": 12}


def test_add_records_model_and_whole_ms_timings(tmp_path):
    store = HistoryStore(tmp_path / "h.jsonl")
    item = store.add("hello", 1.0, model="small.en",
                     timings_ms={"transcribe": 812.6, "cleanup": 0.4,
                                 "bad": float("nan"), "neg": -1})
    assert item["model"] == "small.en"
    assert item["timings_ms"] == {"transcribe": 813, "cleanup": 0}
    assert store.list()[0]["timings_ms"] == item["timings_ms"]
    assert "model" not in store.add("x", 1.0) and "timings_ms" not in store.add("y", 1.0)


def test_merge_timings_keeps_earlier_keys_and_does_not_resurrect(tmp_path):
    store = HistoryStore(tmp_path / "h.jsonl")
    item = store.add("hello", 1.0, timings_ms={"transcribe": 100})
    assert store.merge_timings(item["id"], {"paste": 20, "total": 130})
    assert store.list()[0]["timings_ms"] == {"transcribe": 100, "paste": 20, "total": 130}
    store.delete(item["id"])
    assert store.merge_timings(item["id"], {"total": 1}) is False
    assert store.list() == []


def test_merge_fields_adds_app_stages_and_verdict(tmp_path):
    store = HistoryStore(tmp_path / "h.jsonl")
    item = store.add("hello", 1.0, timings_ms={"transcribe": 100})
    assert store.merge_fields(
        item["id"], timings_ms={"ax_context": 12, "verify": 30},
        fields={"app": "com.apple.TextEdit", "delivery": "verified"})
    saved = store.list()[0]
    assert saved["app"] == "com.apple.TextEdit"
    assert saved["delivery"] == "verified"
    assert saved["timings_ms"] == {"transcribe": 100, "ax_context": 12, "verify": 30}


def test_merge_fields_takes_only_known_fields_of_the_right_type(tmp_path):
    # the one way into History after the record is written: other apps' text
    # must have no route through it
    store = HistoryStore(tmp_path / "h.jsonl")
    item = store.add("hello", 1.0)
    store.merge_fields(item["id"], fields={
        "before_text": "secret on screen", "text": "overwritten",
        "app": 7, "words_corrected": "two", "delivery": "x" * 500})
    saved = store.list()[0]
    assert "before_text" not in saved
    assert saved["text"] == "hello"
    assert "app" not in saved and "words_corrected" not in saved
    assert len(saved["delivery"]) == 200


def test_merge_fields_does_not_bring_back_a_deleted_record(tmp_path):
    store = HistoryStore(tmp_path / "h.jsonl")
    item = store.add("hello", 1.0)
    store.delete(item["id"])
    assert store.merge_fields(item["id"], fields={"delivery": "verified"}) is False
    assert store.list() == []


def test_old_lines_without_the_new_fields_stay_valid(tmp_path):
    path = tmp_path / "h.jsonl"
    path.write_text(json.dumps(
        {"id": "a", "ts": 1.0, "text": "old", "words": 1, "duration_s": 1.0}) + "\n")
    assert HistoryStore(path).list()[0]["text"] == "old"
