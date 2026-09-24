import pytest

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
