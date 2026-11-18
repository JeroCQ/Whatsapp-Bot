"""Explicit draft preview. Runtime settings/worker routing never call this reader."""
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from crm_service.central_schemas import DraftSettings
from crm_service.models import Agent, BusinessConnectionReference, BusinessDraft, SharedConfigDraft
from crm_service.settings import Settings


def legacy_defaults(settings: Settings, business_id: str | None = None) -> dict[str, Any]:
    business = settings.businesses.get(business_id or "")
    return {
        "timezone": "America/Bogota", "locale": "es-CO",
        "sync_interval": settings.sync_interval, "page_size": settings.page_size,
        "max_pages": settings.max_pages, "sync_max_seconds": settings.sync_max_seconds,
        "notifications_enabled": settings.notifications_enabled,
        "operations_enabled": business.operations_enabled if business else False,
    }


def legacy_references(settings: Settings, business_id: str) -> dict[str, str]:
    business = settings.businesses.get(business_id)
    if business is None:
        return {}
    configured = {
        "bot_control": bool(business.bot_api_url and business.bot_control_secret),
        "source_read": bool(business.readonly_database_url or business.bot_source_secret or business.source_key),
        "chatwoot": bool(business.chatwoot_url and business.chatwoot_account),
    }
    return {purpose: f"legacy_settings:{business_id}:{purpose}" for purpose, exists in configured.items() if exists}


@dataclass(frozen=True)
class DraftPreview:
    effective: dict[str, Any]
    origins: dict[str, str]
    connection_references: dict[str, str] = field(repr=False)


def preview(session: Session, settings: Settings, business_id: str) -> DraftPreview:
    """Compatibility overlay only; does not instantiate, mutate or activate Settings."""
    shared = session.get(SharedConfigDraft, "shared")
    business = session.get(BusinessDraft, business_id)
    effective = legacy_defaults(settings, business_id)
    origins = {key: "legacy_default" for key in effective}
    for label, values in (("shared_draft", shared.values if shared else {}),
                          ("business_draft", business.overrides if business else {})):
        validated = DraftSettings.model_validate(values).model_dump(exclude_unset=True)
        effective.update(validated)
        origins.update({key: label for key in validated})
    references = legacy_references(settings, business_id)
    references.update({row.purpose: row.reference_key for row in session.scalars(
        select(BusinessConnectionReference).where(BusinessConnectionReference.business_id == business_id)
    )})
    return DraftPreview(effective, origins, references)


def public_business(session: Session, settings: Settings, business_id: str) -> dict[str, Any]:
    row = session.get(BusinessDraft, business_id)
    candidate = preview(session, settings, business_id)
    memberships = []
    for agent in session.scalars(select(Agent).order_by(Agent.profile)):
        if business_id not in agent.allowed_businesses:
            continue
        operator = agent.preferences.get("_crm_operator", {})
        if operator.get("role") == "retired":
            continue
        memberships.append({"profile": agent.profile, "role": operator.get("role", "operator"),
                            "queue": operator.get("queue"), "aliases": operator.get("aliases", [])})
    return {
        "business_id": business_id, "display_name": row.display_name if row else business_id,
        "state": "draft", "runtime_source": "legacy_settings" if business_id in settings.businesses else "not_configured",
        "draft_exists": row is not None, "version": row.version if row else 0,
        "whatsapp_phone_number_id": row.whatsapp_phone_number_id if row else None,
        "whatsapp_business_account_id": row.whatsapp_business_account_id if row else None,
        "overrides": row.overrides if row else {}, "preview": candidate.effective, "origins": candidate.origins,
        "memberships": memberships,
        # Neither opaque reference keys nor secret values/URLs reach the browser.
        "connections": [{"purpose": purpose, "configured_in_legacy": purpose in legacy_references(settings, business_id)}
                        for purpose in sorted(candidate.connection_references)],
        "activation_available": False,
    }
