"""Slow external push transport must not stall source synchronization."""

import threading
import time
from types import SimpleNamespace

from crm_service import worker


def test_slow_push_runs_once_while_next_sync_cycle_proceeds(monkeypatch):
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    sync_runs, push_runs = [], []
    clock = [0.0]

    class Stop:
        cycles = 0

        def set(self):
            self.cycles = 2

        def is_set(self):
            return self.cycles >= 2

        def wait(self, _seconds):
            assert started.wait(1)
            assert not finished.is_set()
            self.cycles += 1
            clock[0] += 5
            if self.cycles == 2:
                release.set()

    def slow_push(*_args):
        push_runs.append(True)
        started.set()
        try:
            assert release.wait(3)
            return 0
        finally:
            finished.set()

    config = SimpleNamespace(businesses={"synthetic": object()}, sync_interval=5)
    monkeypatch.setattr(worker, "Settings", SimpleNamespace(from_env=lambda **_: config))
    monkeypatch.setattr(worker, "database", lambda _: (SimpleNamespace(dispose=lambda: None), object()))
    monkeypatch.setattr(worker, "threading", SimpleNamespace(Event=Stop))
    monkeypatch.setattr(worker.signal, "signal", lambda *_: None)
    monkeypatch.setattr(worker, "Synchronizer", lambda *_: SimpleNamespace(run=sync_runs.append))
    monkeypatch.setattr(worker, "consume_one", lambda *_: None)
    monkeypatch.setattr(worker, "dispatch", slow_push)
    monkeypatch.setattr(worker.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr("sys.argv", ["crm-worker"])
    worker.main()
    assert sync_runs == ["synthetic", "synthetic"]
    assert push_runs == [True] and finished.is_set()


def test_one_slow_business_does_not_hold_the_next_read_of_another(monkeypatch):
    slow_started, release, fast_finished = threading.Event(), threading.Event(), threading.Event()
    calls, clock = [], [0.0]

    class Stop:
        cycles = 0

        def set(self):
            self.cycles = 3
            release.set()

        def is_set(self):
            return self.cycles >= 3

        def wait(self, _seconds):
            assert slow_started.wait(1) and fast_finished.wait(1)
            # Real worker waits yield to its pool. The fake clock must also
            # allow a completed function to publish its Future result.
            time.sleep(0.01)
            self.cycles += 1
            clock[0] += 5
            if self.cycles == 3:
                assert calls.count("fast") >= 2 and calls.count("slow") == 1
                release.set()

    def synchronize(business):
        calls.append(business)
        if business == "slow":
            slow_started.set()
            assert release.wait(3)
        else:
            fast_finished.set()
        return {business: "ok"}

    settings = SimpleNamespace(businesses={"slow": object(), "fast": object()}, sync_interval=5)
    monkeypatch.setattr(worker, "Settings", SimpleNamespace(from_env=lambda **_: settings))
    monkeypatch.setattr(worker, "database", lambda _: (SimpleNamespace(dispose=lambda: None), object()))
    monkeypatch.setattr(worker, "threading", SimpleNamespace(Event=Stop))
    monkeypatch.setattr(worker.signal, "signal", lambda *_: None)
    monkeypatch.setattr(worker, "Synchronizer", lambda *_: SimpleNamespace(run=synchronize))
    monkeypatch.setattr(worker, "consume_one", lambda *_: None)
    monkeypatch.setattr(worker, "dispatch", lambda *_: 0)
    monkeypatch.setattr(worker.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr("sys.argv", ["crm-worker"])
    worker.main()
