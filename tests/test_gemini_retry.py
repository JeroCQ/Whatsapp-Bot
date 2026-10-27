import pytest
from google.genai.errors import ClientError, ServerError
from rq.timeouts import JobTimeoutException

from gemini_retry import call_gemini_with_retry


class FakeGeminiError(Exception):
    def __init__(self, status_code):
        self.status_code = status_code
        self.response_json = {"error": {"code": status_code, "status": "UNAVAILABLE"}}
        super().__init__(f"Gemini status {status_code}")


def retry(call, *, attempts=3, sleeps=None, jitter=0.25):
    sleeps = sleeps if sleeps is not None else []
    return call_gemini_with_retry(
        "message_logic",
        call,
        attempts=attempts,
        base_seconds=1,
        max_seconds=2,
        jitter_seconds=0.5,
        sleep=sleeps.append,
        uniform=lambda _low, _high: jitter,
    )


def test_transient_503_recovers_without_reaching_handoff_caller(capsys):
    calls = 0
    sleeps = []

    def provider_call():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise FakeGeminiError(503)
        return "response"

    assert retry(provider_call, sleeps=sleeps) == "response"
    assert calls == 3
    assert sleeps == [1.25, 2]
    output = capsys.readouterr().out
    assert "attempt=2/3 status=503" in output
    assert "attempt=3/3 status=503" in output
    assert "EXHAUSTED" not in output


def test_transient_error_is_reraised_after_bounded_attempts(capsys):
    calls = 0
    sleeps = []

    def provider_call():
        nonlocal calls
        calls += 1
        raise FakeGeminiError(503)

    with pytest.raises(FakeGeminiError):
        retry(provider_call, sleeps=sleeps)

    assert calls == 3
    assert sleeps == [1.25, 2]
    assert "[GEMINI RETRY EXHAUSTED]" in capsys.readouterr().out


def test_permanent_error_is_reraised_without_sleep_or_retry():
    calls = 0
    sleeps = []

    def provider_call():
        nonlocal calls
        calls += 1
        raise FakeGeminiError(400)

    with pytest.raises(FakeGeminiError):
        retry(provider_call, sleeps=sleeps)

    assert calls == 1
    assert sleeps == []


def test_attempt_count_is_never_less_than_one():
    calls = 0

    def provider_call():
        nonlocal calls
        calls += 1
        raise FakeGeminiError(503)

    with pytest.raises(FakeGeminiError):
        retry(provider_call, attempts=0)

    assert calls == 1


def test_real_sdk_503_retries_and_recovers_without_customer_side_effects(capsys):
    calls, sleeps = [], []

    def provider_call():
        calls.append("inference")
        if len(calls) < 3:
            raise ServerError(503, {"error": {"code": 503, "status": "UNAVAILABLE",
                                               "message": "PRIVATE_PROVIDER_PAYLOAD"}})
        return "synthetic response"

    assert retry(provider_call, sleeps=sleeps) == "synthetic response"
    assert calls == ["inference"] * 3
    assert sleeps == [1.25, 2]
    output = capsys.readouterr().out
    assert "status=503" in output and "PRIVATE_PROVIDER_PAYLOAD" not in output


@pytest.mark.parametrize("error", [
    ClientError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                                "message": "Your prepayment credits are depleted."}}),
    ClientError(400, {"error": {"code": 400, "status": "INVALID_ARGUMENT"}}),
    ClientError(401, {"error": {"code": 401, "status": "UNAUTHENTICATED"}}),
    JobTimeoutException("synthetic job execution deadline"),
])
def test_real_permanent_errors_and_worker_deadlines_are_reraised_immediately(error):
    calls, sleeps = [], []

    def provider_call():
        calls.append("inference")
        raise error

    with pytest.raises(type(error)) as caught:
        retry(provider_call, sleeps=sleeps)
    assert caught.value is error
    assert calls == ["inference"]
    assert sleeps == []
