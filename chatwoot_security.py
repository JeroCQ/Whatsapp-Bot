import hashlib
import hmac
import time
from urllib.parse import urlsplit, urlunsplit


def normalize_chatwoot_root(value: str, *, production: bool = True) -> str:
    raw = (value or "").strip()
    parsed = urlsplit(raw)
    if not raw or not parsed.scheme or not parsed.hostname:
        raise ValueError("CHATWOOT_BASE_URL must be an absolute installation root")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("CHATWOOT_BASE_URL cannot contain userinfo, query, or fragment")
    if parsed.path not in ("", "/"):
        raise ValueError("CHATWOOT_BASE_URL must be the installation root without /api/v1 or /app")
    if production and parsed.scheme.lower() != "https":
        raise ValueError("CHATWOOT_BASE_URL must use HTTPS in production")
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("CHATWOOT_BASE_URL must use HTTP or HTTPS")
    host = parsed.hostname.lower()
    port = parsed.port
    authority = f"{host}:{port}" if port else host
    return urlunsplit((parsed.scheme.lower(), authority, "", "", ""))


def verify_chatwoot_signature(raw_body: bytes, timestamp: str, signature: str, secret: str, *, now: int | None = None, tolerance: int = 300) -> bool:
    if not timestamp or not signature or not secret:
        return False
    try:
        sent_at = int(timestamp)
    except (TypeError, ValueError):
        return False
    if abs((int(time.time()) if now is None else now) - sent_at) > tolerance:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), timestamp.encode() + b"." + raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def chatwoot_scope(data: dict) -> tuple[str, str]:
    conversation = data.get("conversation") if isinstance(data.get("conversation"), dict) else data
    account = data.get("account") or conversation.get("account") or {}
    inbox = data.get("inbox") or conversation.get("inbox") or {}
    account_id = account.get("id") if isinstance(account, dict) else account
    inbox_id = inbox.get("id") if isinstance(inbox, dict) else inbox
    inbox_id = inbox_id or conversation.get("inbox_id")
    return str(account_id or ""), str(inbox_id or "")
