"""Portable lock contracts and simultaneous independent journal initialization."""

import multiprocessing
import os
from types import SimpleNamespace

import pytest

from contextopus.core import locking


def fake_windows_api(calls, error=None):
    def lock(fd, mode, length):
        calls.append((fd, mode, length, os.lseek(fd, 0, os.SEEK_CUR)))
        if error:
            raise error

    return SimpleNamespace(LK_LOCK=1, LK_NBLCK=2, LK_UNLCK=3, locking=lock)













def record_simultaneously(root, barrier, worker):
    from workspace.analytics import EventStore
    import time

    barrier.wait(timeout=20)
    store = EventStore(root)
    for number in range(3):
        started = time.monotonic()
        event = store.begin("p", "read", {"worker": worker, "number": number})
        store.end(event, "p", started, {}, {}, result={"number": number})


def test_simultaneous_processes_bootstrap_wal_without_lost_events(tmp_path):
    from workspace.analytics import EventStore

    # Installation initializes schema once before independent writers start.
    store = EventStore(tmp_path)
    store.storage_apply(store.storage_prepare()['revision'])
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(4)
    processes = [
        context.Process(target=record_simultaneously, args=(tmp_path, barrier, worker))
        for worker in range(4)
    ]
    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=40)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
    report = EventStore(tmp_path).query()
    assert report["totals"]["calls"] == 12
    assert report["totals"]["unknown_outcomes"] == 0
    assert report["totals"]["errors"] == 0


def native_windows_contender(path, connection):
    """Separate CRT handle/process; never emulate the native lock call."""
    locked = False
    with open(path, "a+b") as stream:
        stream.seek(11)
        connection.send("ready")
        try:
            while (command := connection.recv()) != "stop":
                if command == "acquire":
                    try:
                        locking.flock(stream, locking.LOCK_EX | locking.LOCK_NB)
                    except OSError:
                        connection.send(("busy", stream.tell()))
                    else:
                        locked = True
                        connection.send(("acquired", stream.tell()))
                elif command == "release":
                    locking.flock(stream, locking.LOCK_UN)
                    locked = False
                    connection.send(("released", stream.tell()))
        finally:
            if locked:
                locking.flock(stream, locking.LOCK_UN)
            connection.close()


