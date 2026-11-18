"""Opt-in nonblocking metadata collector, wired only through the scoped pilot context.

One observation per actual attempt inside existing retries. Turn acceptance comes
separately from the existing sender. Disabled collectors do not start threads.
"""
import logging
import re
import time
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from queue import Full, Queue
from threading import Thread
from typing import Any, AsyncIterator, Callable, Iterator
from uuid import uuid4

logger = logging.getLogger(__name__)


def available(value: Any, key: str) -> Any:
    return value.get(key) if isinstance(value, dict) else getattr(value, key, None)


@dataclass
class AttemptObservation:
    enabled: bool
    usage: dict[str, int | None] = field(default_factory=dict)
    model_version: str | None = None

    def observe_response(self, response: Any) -> None:
        if not self.enabled:
            return
        try:
            metadata = available(response, "usage_metadata")
            for field_name, sdk_name in (
                ("input_tokens", "prompt_token_count"), ("output_tokens", "candidates_token_count"),
                ("total_tokens", "total_token_count"), ("cached_input_tokens", "cached_content_token_count"),
                ("thought_tokens", "thoughts_token_count"),
            ):
                value = available(metadata, sdk_name)
                self.usage[field_name] = value if type(value) is int and 0 <= value <= 9223372036854775807 else None
            version = available(response, "model_version")
            if isinstance(version, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}", version):
                self.model_version = version
        except Exception:
            # Failed metadata extraction must never turn a good reply into a failure.
            logger.warning("usage_metadata_unavailable")


class UsageCollector:
    def __init__(self, emit: Callable[[str, dict[str, Any]], None] | None = None, *, enabled: bool = False):
        self.enabled = bool(enabled and emit is not None)
        self._emit = emit
        self._queue: Queue[tuple[str, dict[str, Any]]] = Queue(maxsize=256)
        self.dropped_events = 0
        self.failed_events = 0
        self.accepted_events = 0
        if self.enabled:
            try:
                Thread(target=self._drain, name="chatty-usage", daemon=True).start()
            except RuntimeError:
                self.enabled = False
                logger.warning("usage_collector_not_started")

    def _drain(self) -> None:
        while True:
            kind, payload = self._queue.get()
            try:
                assert self._emit is not None
                self._emit(kind, payload)
                self.accepted_events += 1
            except Exception:
                # No response bodies, credentials, prompts, phone numbers or exception text.
                logger.warning("usage_sink_failed")
                self.failed_events += 1
            finally:
                self._queue.task_done()

    def _submit(self, kind: str, payload: dict[str, Any]) -> None:
        try:
            self._queue.put_nowait((kind, payload))
        except Full:
            self.dropped_events += 1
            logger.warning("usage_queue_full_event_not_recorded")

    def status(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "pending_events": self._queue.unfinished_tasks,
                "accepted_events": self.accepted_events, "failed_events": self.failed_events,
                "dropped_events": self.dropped_events, "durable_before_acceptance": False,
                "complete_accounting": False}

    @contextmanager
    def attempt(self, *, business_id: str, conversation_id: str, turn_id: str,
                attempt_index: int, purpose: str, model_name: str,
                attempt_id: str | None = None, model_version: str | None = None) -> Iterator[AttemptObservation]:
        observation = AttemptObservation(self.enabled)
        if not self.enabled:
            yield observation
            return
        started, observed_at = time.perf_counter(), datetime.now(timezone.utc)
        outcome, error_type = "success", None
        try:
            yield observation
        except BaseException as error:
            name = type(error).__name__
            error_type = name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", name) else "ProviderError"
            outcome = "cancelled" if name in {"CancelledError", "KeyboardInterrupt", "SystemExit"} else "timeout" if isinstance(error, TimeoutError) or "timeout" in name.lower() else "failed"
            raise
        finally:
            try:
                record = {
                    "business_id": business_id, "conversation_id": conversation_id, "turn_id": turn_id,
                    "attempt_id": attempt_id or str(uuid4()), "attempt_index": attempt_index,
                    "purpose": purpose, "model_name": model_name, "model_version": observation.model_version or model_version,
                    "latency_ms": max(0, int((time.perf_counter() - started) * 1000)),
                    "outcome": outcome, "error_type": error_type, "observed_at": observed_at.isoformat(),
                    **{key: observation.usage.get(key) for key in ("input_tokens", "output_tokens", "total_tokens", "cached_input_tokens", "thought_tokens")},
                }
                self._submit("attempt", record)
            except Exception:
                logger.warning("usage_attempt_not_recorded")

    @asynccontextmanager
    async def async_attempt(self, **correlation: Any) -> AsyncIterator[AttemptObservation]:
        with self.attempt(**correlation) as observation:
            yield observation

    def record_turn(self, *, business_id: str, conversation_id: str, turn_id: str,
                    outcome: str, started_at: datetime | None = None) -> None:
        if self.enabled:
            try:
                self._submit("turn", {"business_id": business_id, "conversation_id": conversation_id,
                    "turn_id": turn_id, "outcome": outcome,
                    "started_at": started_at.isoformat() if started_at else None})
            except Exception:
                logger.warning("usage_turn_not_recorded")
