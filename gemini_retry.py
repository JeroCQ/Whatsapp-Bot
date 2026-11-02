"""Bounded retry policy shared by Gemini runtime operations."""

import random
import time
from collections.abc import Callable
from typing import TypeVar

from gemini_errors import gemini_error_status_code, is_transient_gemini_error


Result = TypeVar("Result")


def call_gemini_with_retry(
    operation: str,
    call: Callable[[], Result],
    *,
    attempts: int,
    base_seconds: float,
    max_seconds: float,
    jitter_seconds: float,
    sleep: Callable[[float], None] = time.sleep,
    uniform: Callable[[float, float], float] = random.uniform,
) -> Result:
    """Run a Gemini call with bounded exponential backoff for transient errors.

    Permanent errors are raised immediately. The last transient exception is
    also raised after the configured total number of attempts, preserving the
    existing handoff behavior in the caller.
    """
    total_attempts = max(1, attempts)
    for attempt in range(1, total_attempts + 1):
        try:
            return call()
        except Exception as exc:
            retryable = is_transient_gemini_error(exc)
            if not retryable or attempt == total_attempts:
                if retryable:
                    print(
                        f"[GEMINI RETRY EXHAUSTED] operation={operation} "
                        f"attempts={total_attempts} status={gemini_error_status_code(exc) or 'unknown'}"
                    )
                raise

            exponential_delay = base_seconds * (2 ** (attempt - 1))
            delay = min(max_seconds, exponential_delay + uniform(0, jitter_seconds))
            print(
                f"[GEMINI RETRY] operation={operation} attempt={attempt + 1}/{total_attempts} "
                f"status={gemini_error_status_code(exc) or 'network'} delay_ms={int(delay * 1000)}"
            )
            sleep(delay)

    raise RuntimeError("Gemini retry loop completed without a result")
