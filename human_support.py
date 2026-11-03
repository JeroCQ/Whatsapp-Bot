"""Select human support independently from a specific ticketing integration.

An empty provider preserves the historic CHATWOOT_ENABLED behavior, including
deployments that explicitly disable Chatwoot and run only the bot.
"""

from typing import Any

PROVIDERS = frozenset({"chatwoot", "crm", "bot_only"})


def human_support_provider(settings: Any) -> str:
    raw = str(getattr(settings, "HUMAN_SUPPORT_PROVIDER", "") or "").strip().lower()
    if not raw:
        return "chatwoot" if getattr(settings, "CHATWOOT_ENABLED", True) else "bot_only"
    if raw not in PROVIDERS:
        raise ValueError("HUMAN_SUPPORT_PROVIDER must be chatwoot, crm, or bot_only")
    return raw


def uses_chatwoot(settings: Any) -> bool:
    return human_support_provider(settings) == "chatwoot"


def uses_crm(settings: Any) -> bool:
    return human_support_provider(settings) == "crm"


def human_handoffs_enabled(settings: Any) -> bool:
    return human_support_provider(settings) != "bot_only"
