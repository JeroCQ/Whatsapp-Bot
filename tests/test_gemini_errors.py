import httpx
import pytest
from google.genai.errors import ClientError, ServerError
from rq.timeouts import JobTimeoutException

from gemini_errors import (
    GeminiResponseError,
    gemini_error_status_code,
    gemini_failure_category,
    gemini_handoff_reason,
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


def test_pinned_sdk_server_error_uses_real_code_and_details_contract():
    error = ServerError(503, {"error": {"code": 503, "status": "UNAVAILABLE", "message": "Synthetic overload"}})
    assert not hasattr(error, "status_code") and not hasattr(error, "response_json")
    assert gemini_error_status_code(error) == 503
    assert is_transient_gemini_error(error)


def test_pinned_sdk_prepaid_credit_error_is_not_retried():
    error = ClientError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                                        "message": "Your prepayment credits are depleted."}})
    assert is_depleted_prepaid_credits(error)
    assert not is_transient_gemini_error(error)


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_pinned_sdk_permanent_errors_are_not_transient(status):
    error = ClientError(status, {"error": {"code": status, "status": "INVALID_ARGUMENT"}})
    assert gemini_error_status_code(error) == status
    assert not is_transient_gemini_error(error)


@pytest.mark.parametrize("error", [httpx.ReadTimeout("synthetic"), httpx.ConnectTimeout("synthetic"),
                                    httpx.ConnectError("synthetic"), httpx.ReadError("synthetic")])
def test_actual_sdk_httpx_network_failures_are_transient(error):
    assert gemini_error_status_code(error) is None
    assert is_transient_gemini_error(error)


def test_worker_execution_deadline_is_not_a_provider_timeout():
    assert not is_transient_gemini_error(JobTimeoutException("synthetic job deadline"))


def test_response_status_is_supported_without_reading_the_response_body():
    error = RuntimeError("synthetic")
    error.response = httpx.Response(503)
    assert gemini_error_status_code(error) == 503
    assert is_transient_gemini_error(error)


@pytest.mark.parametrize("error, expected", [
    (ServerError(503, {"error": {"code": 503, "message": "PRIVATE_ERROR_BODY"}}), "provider_unavailable"),
    (ServerError(504, {"error": {"code": 504}}), "provider_timeout"),
    (ClientError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED"}}), "rate_or_quota_limit"),
    (ClientError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "Your prepayment credits are depleted."}}), "prepaid_credits_depleted"),
    (ClientError(401, {"error": {"code": 401}}), "provider_authentication"),
    (httpx.ReadTimeout("PRIVATE_ERROR_BODY"), "network_timeout"),
    (httpx.ConnectError("PRIVATE_ERROR_BODY"), "network_error"),
    (GeminiResponseError("PRIVATE_ERROR_BODY"), "invalid_structured_response"),
    (RuntimeError("PRIVATE_ERROR_BODY"), "inference_error"),
])
def test_terminal_failure_categories_do_not_confuse_outage_with_billing(error, expected):
    category = gemini_failure_category(error)
    assert category == expected
    reason = gemini_handoff_reason(category, gemini_error_status_code(error))
    assert reason.startswith("Gemini no respondió:")
    assert "PRIVATE_ERROR_BODY" not in reason
    assert ("créditos" in reason) == (category == "prepaid_credits_depleted")
