"""Brand-local Gemini calls with bounded acquisition, requests and retry waits."""

import math
import random
import time

from google.genai import types

from gemini_retry import call_gemini_with_retry


class GeminiOperationTimeout(RuntimeError):
    """The operation budget expired; another provider attempt must not start."""


def run_gemini(
    operation,
    generate,
    *,
    semaphore,
    request_timeout_seconds=20,
    operation_timeout_seconds=60,
    attempts=3,
    base_seconds=1,
    max_seconds=8,
    jitter_seconds=0.5,
    sleep=time.sleep,
    monotonic=time.monotonic,
    uniform=random.uniform,
):
    """Pass fresh HttpOptions to ``generate`` for every permitted attempt.

    The budget includes semaphore contention and backoff. Request timeouts are
    clipped to its remaining value; HTTPX applies them to individual network
    phases, so they cannot guarantee interruption at an absolute wall-clock
    deadline while a request is already in flight.
    """
    limits = (request_timeout_seconds, operation_timeout_seconds)
    if any(not math.isfinite(value) or value <= 0 for value in limits):
        raise ValueError("Gemini timeout limits must be finite and positive")
    deadline = monotonic() + operation_timeout_seconds

    def remaining_budget():
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise GeminiOperationTimeout("gemini_operation_budget_exhausted")
        return remaining

    def limited_call():
        if not semaphore.acquire(timeout=remaining_budget()):
            raise GeminiOperationTimeout("gemini_concurrency_wait_expired")
        try:
            request_seconds = min(request_timeout_seconds, remaining_budget())
            options = types.HttpOptions(
                timeout=max(1, int(request_seconds * 1000)),
                retry_options=types.HttpRetryOptions(attempts=1),
            )
            return generate(options)
        finally:
            semaphore.release()

    def bounded_sleep(delay):
        if delay >= remaining_budget():
            raise GeminiOperationTimeout("gemini_retry_wait_exceeds_budget")
        sleep(delay)

    try:
        return call_gemini_with_retry(
            operation, limited_call,
            attempts=attempts,
            base_seconds=base_seconds,
            max_seconds=max_seconds,
            jitter_seconds=jitter_seconds,
            sleep=bounded_sleep,
            uniform=uniform,
        )
    except GeminiOperationTimeout as error:
        print(f"[GEMINI BUDGET EXHAUSTED] operation={operation} reason={error}")
        raise
