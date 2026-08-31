from gemini_errors import is_depleted_prepaid_credits


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


def test_accepts_structured_http_response_payload_and_string_status_code():
    class Response:
        def json(self):
            return {"error": {"status": "RESOURCE_EXHAUSTED", "message": "Prepaid credits depleted"}}

    error = RuntimeError("request failed")
    error.status_code = "429"
    error.response = Response()

    assert is_depleted_prepaid_credits(error)


def test_other_structured_429_billing_messages_are_not_assumed_to_be_prepaid_depletion():
    error = FakeGeminiError(
        429,
        {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "Billing account unavailable"}},
    )

    assert not is_depleted_prepaid_credits(error)
