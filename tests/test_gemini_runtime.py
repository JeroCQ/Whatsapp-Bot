"""Budget and real-SDK transport checks with synthetic data only."""

import threading

import httpx
import pytest
from google import genai
from google.genai import types

from gemini_runtime import GeminiOperationTimeout, run_gemini


class Clock:
    def __init__(self):
        self.elapsed = 0.0
        self.sleeps = []

    def now(self):
        return self.elapsed

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.elapsed += seconds


def run(generate, *, clock=None, semaphore=None, **kwargs):
    clock = clock or Clock()
    return run_gemini(
        "message_logic", generate,
        semaphore=semaphore or threading.BoundedSemaphore(1),
        monotonic=clock.now, sleep=clock.sleep, uniform=lambda *_: 0,
        **kwargs,
    )


def test_real_sdk_receives_millisecond_timeout_without_nested_retries():
    observed = []

    def respond(request):
        observed.append(request)
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": "synthetic response"}], "role": "model"}}],
        })

    client = genai.Client(api_key="synthetic-key", http_options=types.HttpOptions(
        base_url="https://gemini.test", client_args={"transport": httpx.MockTransport(respond)},
    ))
    try:
        result = run(lambda options: client.models.generate_content(
            model="gemini-flash-latest", contents="synthetic input",
            config=types.GenerateContentConfig(http_options=options),
        ))
    finally:
        client._api_client._httpx_client.close()

    assert result.text == "synthetic response"
    assert len(observed) == 1
    assert observed[0].extensions["timeout"] == {"connect": 20.0, "read": 20.0, "write": 20.0, "pool": 20.0}


def test_options_clip_to_budget_remaining_after_semaphore_wait():
    clock = Clock()

    class ContendedSemaphore:
        released = 0

        def acquire(self, *, timeout):
            assert timeout == 60
            clock.elapsed += 55
            return True

        def release(self):
            self.released += 1

    semaphore = ContendedSemaphore()
    options = run(lambda value: value, clock=clock, semaphore=semaphore)
    assert options.timeout == 5000
    assert options.retry_options.attempts == 1
    assert semaphore.released == 1


def test_real_sdk_overload_is_retried_by_application_once_without_hidden_attempts():
    clock = Clock()
    observed, generated = [], []

    def respond(request):
        observed.append(request)
        if len(observed) == 1:
            return httpx.Response(503, json={"error": {"code": 503, "status": "UNAVAILABLE", "message": "synthetic overload"}})
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": "recovered"}], "role": "model"}}],
        })

    client = genai.Client(api_key="synthetic-key", http_options=types.HttpOptions(
        base_url="https://gemini.test", client_args={"transport": httpx.MockTransport(respond)},
    ))

    def generate(options):
        generated.append(options)
        return client.models.generate_content(
            model="gemini-flash-latest", contents="synthetic input",
            config=types.GenerateContentConfig(http_options=options),
        )

    try:
        assert run(generate, clock=clock).text == "recovered"
    finally:
        client._api_client._httpx_client.close()
    assert len(observed) == len(generated) == 2
    assert clock.sleeps == [1]
    assert all(options.retry_options.attempts == 1 for options in generated)


def test_expired_semaphore_wait_never_calls_provider():
    class BusySemaphore:
        def acquire(self, *, timeout):
            assert timeout == 60
            return False

        def release(self):
            pytest.fail("unacquired semaphore cannot be released")

    with pytest.raises(GeminiOperationTimeout, match="gemini_concurrency_wait_expired"):
        run(lambda options: pytest.fail("must not contact provider"), semaphore=BusySemaphore())


def test_deadline_expiring_during_acquisition_still_releases_slot():
    clock = Clock()

    class SlowSemaphore:
        released = 0

        def acquire(self, *, timeout):
            clock.elapsed = timeout
            return True

        def release(self):
            self.released += 1

    semaphore = SlowSemaphore()
    with pytest.raises(GeminiOperationTimeout, match="gemini_operation_budget_exhausted"):
        run(lambda options: pytest.fail("must not contact provider"), clock=clock, semaphore=semaphore)
    assert semaphore.released == 1


def test_retry_releases_slot_before_wait_and_clips_second_request_timeout():
    clock = Clock()
    semaphore = threading.BoundedSemaphore(1)
    observed = []

    def generate(options):
        observed.append(options)
        if len(observed) == 1:
            clock.elapsed = 55
            raise httpx.ReadTimeout("synthetic transport timeout")
        return "recovered"

    def sleep(seconds):
        assert semaphore.acquire(blocking=False)
        semaphore.release()
        clock.sleep(seconds)

    assert run_gemini(
        "message_logic", generate, semaphore=semaphore,
        sleep=sleep, monotonic=clock.now, uniform=lambda *_: 0,
    ) == "recovered"
    assert [options.timeout for options in observed] == [20000, 4000]
    assert all(options.retry_options.attempts == 1 for options in observed)
    assert clock.sleeps == [1]
    assert semaphore.acquire(blocking=False)


def test_no_retry_sleep_or_provider_call_after_budget_is_spent():
    clock = Clock()
    calls = []

    def generate(options):
        calls.append(options)
        clock.elapsed = 60
        raise httpx.ReadTimeout("synthetic timeout")

    with pytest.raises(GeminiOperationTimeout, match="gemini_operation_budget_exhausted"):
        run(generate, clock=clock)
    assert len(calls) == 1
    assert clock.sleeps == []


def test_retry_wait_cannot_consume_remaining_budget():
    clock = Clock()

    def generate(options):
        clock.elapsed = 59.5
        raise httpx.ReadTimeout("synthetic timeout")

    with pytest.raises(GeminiOperationTimeout, match="gemini_retry_wait_exceeds_budget"):
        run(generate, clock=clock)
    assert clock.sleeps == []


def test_permanent_provider_exception_releases_slot_without_retry():
    semaphore = threading.BoundedSemaphore(1)
    calls = []

    def generate(options):
        calls.append(options)
        raise ValueError("synthetic permanent error")

    with pytest.raises(ValueError):
        run(generate, semaphore=semaphore)
    assert len(calls) == 1
    assert semaphore.acquire(blocking=False)


@pytest.mark.parametrize("limits", [
    {"request_timeout_seconds": 0}, {"request_timeout_seconds": float("nan")},
    {"operation_timeout_seconds": -1}, {"operation_timeout_seconds": float("inf")},
])
def test_invalid_limits_fail_without_contacting_provider(limits):
    with pytest.raises(ValueError, match="finite and positive"):
        run(lambda options: pytest.fail("invalid limits cannot contact provider"), **limits)
