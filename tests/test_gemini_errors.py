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
