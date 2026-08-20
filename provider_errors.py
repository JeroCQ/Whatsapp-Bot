import json
import re


class ProviderError(RuntimeError):
    def __init__(self, provider: str, operation: str, status: int | None, code: str = "", subcode: str = "", message: str = ""):
        self.provider = provider
        self.operation = operation
        self.status = status
        self.code = code
        self.subcode = subcode
        self.safe_message = sanitize_text(message)
        super().__init__(f"{provider} {operation} failed (status={status}, code={code}, subcode={subcode}, message={self.safe_message})")


def sanitize_text(value: object, secrets=()) -> str:
    text = str(value or "")[:500]
    for secret in secrets:
        if secret:
            text = text.replace(str(secret), "[REDACTED]")
    text = re.sub(r"(?i)(bearer|api_access_token|access_token|token|authorization)([\s:=\"']+)[^\s,;\"']+", r"\1\2[REDACTED]", text)
    text = re.sub(r"https?://[^\s]+", "[REDACTED_URL]", text)
    return text.replace("\n", " ").replace("\r", " ")


def provider_error(provider: str, operation: str, response, secrets=()) -> ProviderError:
    code = subcode = message = ""
    try:
        body = response.json()
        error = body.get("error", body) if isinstance(body, dict) else {}
        code = str(error.get("code", ""))
        subcode = str(error.get("error_subcode", error.get("subcode", "")))
        message = error.get("message", "")
    except (ValueError, AttributeError, json.JSONDecodeError):
        message = "provider returned a non-JSON error"
    return ProviderError(provider, operation, getattr(response, "status_code", None), code, subcode, sanitize_text(message, secrets))

