"""Slow external push transport must not stall source synchronization."""

import threading
from types import SimpleNamespace

from crm_service import worker


def test_slow_push_runs_once_while_next_sync_cycle_proceeds(monkeypatch):
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    sync_runs, push_runs = [], []

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
    monkeypatch.setattr("sys.argv", ["crm-worker"])
    worker.main()
    assert sync_runs == ["synthetic", "synthetic"]
    assert push_runs == [True] and finished.is_set()
