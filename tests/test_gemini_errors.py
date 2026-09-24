from gemini_errors import (
    gemini_error_status_code,
    is_depleted_prepaid_credits,
    is_transient_gemini_error,
)


class FakeGeminiError(Exception):
    def __init__(self, status_code, response_json):
        self.status_code = status_code
        self.response_json = response_json
        super().__init__(response_json.get("error", {}).get("message", ""))


def test_recognizes_depleted_prepaid_credits():
    error = FakeGeminiError(
        429,
        {
            "error": {
                "code": 429,
                "message": "Your prepayment credits are depleted. Please manage billing.",
                "status": "RESOURCE_EXHAUSTED",
            }
        },
    )

    assert is_depleted_prepaid_credits(error)


def test_does_not_misclassify_a_transient_rate_limit():
    error = FakeGeminiError(
        429,
        {
            "error": {
                "code": 429,
                "message": "Rate limit exceeded. Retry later.",
                "status": "RESOURCE_EXHAUSTED",
            }
        },
    )

    assert not is_depleted_prepaid_credits(error)


def test_does_not_classify_an_unstructured_exception():
    assert not is_depleted_prepaid_credits(RuntimeError("network unavailable"))


def test_recognizes_high_demand_503_as_transient():
    error = FakeGeminiError(
        503,
        {
            "error": {
                "code": 503,
                "message": "This model is currently experiencing high demand.",
                "status": "UNAVAILABLE",
            }
        },
    )

    assert gemini_error_status_code(error) == 503
    assert is_transient_gemini_error(error)


def test_recognizes_retryable_provider_statuses():
    for status_code in (429, 500, 502, 503, 504):
        error = FakeGeminiError(status_code, {"error": {"code": status_code}})
        assert is_transient_gemini_error(error)


def test_does_not_retry_permanent_or_depleted_credit_errors():
    invalid = FakeGeminiError(400, {"error": {"code": 400, "status": "INVALID_ARGUMENT"}})
    depleted = FakeGeminiError(
        429,
        {
            "error": {
                "code": 429,
                "message": "Your prepayment credits are depleted.",
                "status": "RESOURCE_EXHAUSTED",
            }
        },
    )

    assert not is_transient_gemini_error(invalid)
    assert not is_transient_gemini_error(depleted)


def test_recognizes_transport_timeout_as_transient():
    assert is_transient_gemini_error(TimeoutError("timed out"))
