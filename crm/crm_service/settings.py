import math
import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


def https_root(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise ValueError("An HTTPS origin without credentials is required")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ValueError("Expected an origin, without path/query/fragment")
    return value.rstrip("/")


def bot_origin(value: str, *, test_mode: bool = False) -> str:
    parts = urlsplit(value)
    if test_mode and parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1", "::1"}:
        if parts.username or parts.password or parts.path not in ("", "/") or parts.query or parts.fragment:
            raise ValueError("Expected a loopback test origin without credentials or path")
        return value.rstrip("/")
    return https_root(value)


def flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "true" if default else "false").strip().lower()
    if raw not in {"true", "false"}:
        raise ValueError(f"{name} must be true or false")
    return raw == "true"


@dataclass(frozen=True)
class Business:
    id: str
    source_url: str
    source_key: str = field(repr=False)
    chatwoot_url: str = ""
    chatwoot_account: str = ""
    readonly_database_url: str = field(default="", repr=False)
    bot_api_url: str = ""
    bot_control_secret: str = field(default="", repr=False)
    bot_source_secret: str = field(default="", repr=False)
    operations_enabled: bool = False
    bot_api_enabled: bool = False

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
    sync_interval: int = 5
    page_size: int = 500
    max_pages: int = 20
    jwt_max_lifetime: int = 300
    sync_max_seconds: float = 15.0
    sync_max_requests: int = 100
    sync_max_connections: int = 1
    reconcile_interval: int = 3600
    backoff_base_seconds: int = 30
    backoff_max_seconds: int = 1800
    source_mode: str = "synthetic"
    sync_max_projections: int = 50
    sync_max_history_messages: int = 10000
    bot_test_mode: bool = False
    bot_timeout_seconds: float = 20.0
    media_enabled: bool = False
    # None inherits the configured business registry; an explicit empty list
    # enables no businesses. Authentication still scopes every request.
    media_businesses: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if (
            type(self.media_enabled) is not bool
            or any(not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", name) for name in self.media_businesses or ())
            or len(set(self.media_businesses or ())) != len(self.media_businesses or ())
        ):
            raise ValueError("Invalid CRM media business configuration")
        for name, low, high in (
            ("page_size", 1, 1000),
            ("max_pages", 1, 1000),
            ("sync_max_requests", 4, 5000),
            ("sync_max_connections", 1, 4),
            ("sync_interval", 5, 3600),
            ("reconcile_interval", 30, 86400),
            ("backoff_base_seconds", 1, 3600),
            ("backoff_max_seconds", 1, 86400),
        ):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{name} must be an integer in [{low}, {high}]")
        if not math.isfinite(self.sync_max_seconds) or not 0.05 <= self.sync_max_seconds <= 120:
            raise ValueError("sync_max_seconds must be finite and between 0.05 and 120")
        if self.backoff_max_seconds < self.backoff_base_seconds:
            raise ValueError("Backoff maximum must be >= base")
        if self.source_mode not in {"synthetic", "postgres_readonly", "disabled", "bot_api"}:
            raise ValueError("Unsupported CRM_SOURCE_MODE")
        if not math.isfinite(self.bot_timeout_seconds) or not 0.1 <= self.bot_timeout_seconds <= 60:
            raise ValueError("bot_timeout_seconds must be finite and between 0.1 and 60")
        for name, high in (("sync_max_projections", 1000), ("sync_max_history_messages", 100000)):
            if type(getattr(self, name)) is not int or not 1 <= getattr(self, name) <= high:
                raise ValueError(f"Invalid {name}")

    @classmethod
    def from_env(cls, service: str = "api") -> "Settings":
        if service not in {"api", "worker"}:
            raise ValueError("Unknown CRM service")
        database = os.environ["CRM_DATABASE_URL"]
        if not database.startswith("postgresql+psycopg://"):
            raise ValueError("CRM_DATABASE_URL must use postgresql+psycopg")
        jwt_secret = os.environ["CRM_JWT_SECRET"]
        sync_secret = os.environ["CRM_SYNC_SECRET"]
        if min(len(jwt_secret), len(sync_secret)) < 32 or jwt_secret == sync_secret:
            raise ValueError("Use distinct CRM secrets of at least 32 characters")
        businesses = {}
        test_mode = flag("CRM_BOT_TEST_MODE")
        for raw in os.environ.get("CRM_BUSINESSES", "tanaka,memos,velvet").split(","):
            name = raw.strip()
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", name) or name in businesses:
                raise ValueError("Invalid or duplicate business identifier")
            prefix = name.upper()
            url = os.environ.get(f"{prefix}_SOURCE_SUPABASE_URL", "")
            if service == "worker" and os.environ.get(f"{prefix}_SOURCE_SUPABASE_SERVICE_ROLE_KEY"):
                raise ValueError("Administrative bot source credentials are unsupported")
            readonly = (
                os.environ.get(f"{prefix}_SOURCE_READONLY_DATABASE_URL", "") if service == "worker" else ""
            )
            cw = os.environ.get(f"{prefix}_CHATWOOT_APP_URL", "")
            account = os.environ.get(f"{prefix}_CHATWOOT_ACCOUNT_ID", "")
            bot_url = os.environ.get(f"{prefix}_BOT_API_URL", "")
            operations = flag(f"{prefix}_HUMAN_CONTROL_ENABLED")
            source_enabled = flag(f"{prefix}_BOT_API_ENABLED")
            control_secret = os.environ.get(f"{prefix}_BOT_CONTROL_SECRET", "") if service == "api" else ""
            source_secret = os.environ.get(f"{prefix}_BOT_SOURCE_SECRET", "") if service == "worker" else ""
            if (operations or source_enabled) and not bot_url:
                raise ValueError("Enabled bot integration requires a configured bot origin")
            if operations and service == "api" and len(control_secret) < 32:
                raise ValueError(
                    "Human control requires a dedicated bot control secret of at least 32 characters"
                )
            if source_enabled and service == "worker" and len(source_secret) < 32:
                raise ValueError(
                    "Bot source requires a dedicated bot source secret of at least 32 characters"
                )
            if account and not account.isdigit():
                raise ValueError("Chatwoot account must be numeric")
            businesses[name] = Business(
                name,
                https_root(url) if url else "",
                "",
                https_root(cw) if cw else "",
                account,
                readonly,
                bot_origin(bot_url, test_mode=test_mode) if bot_url else "",
                control_secret,
                source_secret,
                operations,
                source_enabled,
            )
        bot_origins = [
            b.bot_api_url
            for b in businesses.values()
            if b.bot_api_url and (b.operations_enabled or b.bot_api_enabled)
        ]
        if len(bot_origins) != len(set(bot_origins)):
            raise ValueError("Each business needs its own isolated bot origin")
        origins = [b.source_url for b in businesses.values() if b.source_url]
        if len(origins) != len(set(origins)):
            raise ValueError("Each business must use a distinct isolated source Supabase origin")
        sources, readers = set(), set()
        for business in businesses.values():
            if not business.readonly_database_url:
                continue
            try:
                parsed = make_url(business.readonly_database_url)
            except (ArgumentError, ValueError):
                raise ValueError("Invalid dedicated source database URL") from None
            host = "loopback" if parsed.host in {"localhost", "127.0.0.1", "::1"} else parsed.host
            identity = (host, parsed.port or 5432, parsed.database)
            if identity in sources or parsed.username in readers:
                raise ValueError("Each business needs a distinct source database and dedicated login")
            sources.add(identity)
            readers.add(parsed.username)
        enabled = os.environ.get("NOTIFICATIONS_ENABLED", "false").lower() == "true"
        if enabled:
            raise ValueError("This staging branch requires NOTIFICATIONS_ENABLED=false")
        return cls(
            database_url=database,
            jwt_secret=jwt_secret,
            sync_secret=sync_secret,
            businesses=businesses,
            sync_interval=int(os.environ.get("SYNC_INTERVAL_SECONDS", "5")),
            page_size=int(os.environ.get("SYNC_PAGE_SIZE", "500")),
            max_pages=int(os.environ.get("SYNC_MAX_PAGES", "20")),
            sync_max_seconds=float(os.environ.get("SYNC_MAX_SECONDS", "15")),
            sync_max_requests=int(os.environ.get("SYNC_MAX_REQUESTS", "100")),
            sync_max_connections=int(os.environ.get("SYNC_MAX_CONNECTIONS", "1")),
            reconcile_interval=int(os.environ.get("SYNC_RECONCILE_INTERVAL_SECONDS", "3600")),
            backoff_base_seconds=int(os.environ.get("SYNC_BACKOFF_BASE_SECONDS", "30")),
            backoff_max_seconds=int(os.environ.get("SYNC_BACKOFF_MAX_SECONDS", "1800")),
            source_mode=os.environ.get("CRM_SOURCE_MODE", "synthetic"),
            sync_max_projections=int(os.environ.get("SYNC_MAX_PROJECTIONS", "50")),
            sync_max_history_messages=int(os.environ.get("SYNC_MAX_HISTORY_MESSAGES", "10000")),
            bot_test_mode=test_mode,
            bot_timeout_seconds=float(os.environ.get("CRM_BOT_TIMEOUT_SECONDS", "20")),
            media_enabled=flag("CRM_MEDIA_ENABLED"),
            media_businesses=(
                tuple(
                    part.strip()
                    for part in os.environ["CRM_MEDIA_ALLOWED_BUSINESSES"].split(",")
                    if part.strip()
                )
                if "CRM_MEDIA_ALLOWED_BUSINESSES" in os.environ
                else None
            ),
        )

    def media_allowed(self, business_id: str) -> bool:
        business = self.businesses.get(business_id)
        return bool(
            self.media_enabled
            and (self.media_businesses is None or business_id in self.media_businesses)
            and business
            and business.operations_enabled
        )
