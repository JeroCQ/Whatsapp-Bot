import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit


def https_root(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise ValueError("An HTTPS origin without credentials is required")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ValueError("Expected an origin, without path/query/fragment")
    return value.rstrip("/")


@dataclass(frozen=True)
class Business:
    id: str
    source_url: str
    source_key: str = field(repr=False)
    chatwoot_url: str = ""
    chatwoot_account: str = ""

    def deep_link(self, conversation_id: str | int | None) -> str | None:
        if not self.chatwoot_url or not self.chatwoot_account or not conversation_id:
            return None
        if not str(conversation_id).isdigit():
            return None
        return f"{self.chatwoot_url}/app/accounts/{self.chatwoot_account}/conversations/{conversation_id}"


@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)
    jwt_secret: str = field(repr=False)
    sync_secret: str = field(repr=False)
    businesses: dict[str, Business]
    notifications_enabled: bool = False
    vapid_private_key: str = field(default="", repr=False)
    vapid_subject: str = ""
    push_hosts: tuple[str, ...] = (
        "fcm.googleapis.com",
        "updates.push.services.mozilla.com",
        "web.push.apple.com",
        "wns.windows.com",
    )
    sync_interval: int = 30
    page_size: int = 500
    max_pages: int = 10000
    jwt_max_lifetime: int = 300

    @classmethod
    def from_env(cls) -> "Settings":
        database = os.environ["CRM_DATABASE_URL"]
        if not database.startswith("postgresql+psycopg://"):
            raise ValueError("CRM_DATABASE_URL must use postgresql+psycopg")
        jwt_secret = os.environ["CRM_JWT_SECRET"]
        sync_secret = os.environ["CRM_SYNC_SECRET"]
        if min(len(jwt_secret), len(sync_secret)) < 32 or jwt_secret == sync_secret:
            raise ValueError("Use distinct CRM secrets of at least 32 characters")
        businesses = {}
        for raw in os.environ.get("CRM_BUSINESSES", "tanaka,memos,velvet").split(","):
            name = raw.strip()
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", name) or name in businesses:
                raise ValueError("Invalid or duplicate business identifier")
            prefix = name.upper()
            url = os.environ.get(f"{prefix}_SOURCE_SUPABASE_URL", "")
            key = os.environ.get(f"{prefix}_SOURCE_SUPABASE_SERVICE_ROLE_KEY", "")
            cw = os.environ.get(f"{prefix}_CHATWOOT_APP_URL", "")
            account = os.environ.get(f"{prefix}_CHATWOOT_ACCOUNT_ID", "")
            if account and not account.isdigit():
                raise ValueError("Chatwoot account must be numeric")
            businesses[name] = Business(
                name, https_root(url) if url else "", key, https_root(cw) if cw else "", account
            )
        origins = [b.source_url for b in businesses.values() if b.source_url]
        if len(origins) != len(set(origins)):
            raise ValueError("Each business must use a distinct isolated source Supabase origin")
        enabled = os.environ.get("NOTIFICATIONS_ENABLED", "false").lower() == "true"
        return cls(
            database,
            jwt_secret,
            sync_secret,
            businesses,
            enabled,
            os.environ.get("VAPID_PRIVATE_KEY", ""),
            os.environ.get("VAPID_SUBJECT", ""),
            tuple(
                filter(
                    None,
                    os.environ.get(
                        "PUSH_ALLOWED_HOSTS",
                        "fcm.googleapis.com,updates.push.services.mozilla.com,web.push.apple.com,wns.windows.com",
                    ).split(","),
                )
            ),
            max(5, int(os.environ.get("SYNC_INTERVAL_SECONDS", "30"))),
        )
