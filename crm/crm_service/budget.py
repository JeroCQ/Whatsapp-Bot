"""Per-business execution budgets, including actual source requests and retries."""

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any


class BudgetExhausted(Exception):
    pass


class ReadBudget:
    def __init__(self, settings: Any, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.started = clock()
        self.max_seconds = settings.sync_max_seconds
        self.max_pages = settings.max_pages
        self.max_requests = settings.sync_max_requests
        self.max_connections = settings.sync_max_connections
        self.pages = self.requests = self.rows = self.connections_peak = 0
        self.read_seconds = 0.0

    def check(self) -> None:
        if self.clock() - self.started >= self.max_seconds:
            raise BudgetExhausted("duration_budget")

    def before_page(self) -> None:
        self.check()
        if self.pages >= self.max_pages:
            raise BudgetExhausted("page_budget")
        self.pages += 1

    def before_request(self) -> float:
        self.check()
        if self.requests >= self.max_requests:
            raise BudgetExhausted("request_budget")
        self.requests += 1
        return self.clock()

    def after_request(self, started: float, rows: int = 0) -> None:
        self.read_seconds += max(0.0, self.clock() - started)
        self.rows += rows
        self.check()

    @contextmanager
    def request(self) -> Iterator[None]:
        started = self.before_request()
        try:
            yield
        except BaseException:
            self.read_seconds += max(0.0, self.clock() - started)
            raise
        else:
            self.after_request(started)

    def record_rows(self, count: int) -> None:
        self.rows += count

    def timeout_seconds(self) -> float:
        self.check()
        return max(0.001, self.max_seconds - (self.clock() - self.started))

    def metrics(self) -> dict[str, int | float]:
        return {
            "pages": self.pages,
            "requests": self.requests,
            "rows": self.rows,
            "duration_seconds": round(self.clock() - self.started, 6),
            "read_seconds": round(self.read_seconds, 6),
            "connections_peak": self.connections_peak,
        }
