"""Pure helpers shared by webhook handlers and their unit tests."""

import re


RESTART_COMMANDS = {"/reset", "/restart"}
SIMPLE_GREETINGS = {"hola", "holi", "hello", "buenas", "buenos dias", "buenos días"}

# Usernames can hide the phone: Meta supplies a business-scoped user ID instead.
# Keep it opaque and case-sensitive, including parent IDs with the ENT segment.
_PHONE_PATTERN = r"[0-9]{7,15}"
_USER_ID_PATTERN = r"[A-Z]{2}\.(?:ENT\.)?[A-Za-z0-9]{1,128}"
WHATSAPP_SENDER_PATTERN = rf"^(?:{_PHONE_PATTERN}|{_USER_ID_PATTERN})$"


def valid_whatsapp_phone(value) -> bool:
    return isinstance(value, str) and re.fullmatch(_PHONE_PATTERN, value) is not None


def valid_whatsapp_user_id(value) -> bool:
    return isinstance(value, str) and re.fullmatch(_USER_ID_PATTERN, value) is not None


def is_restart_command(text: str) -> bool:
    return bool(text) and text.strip().lower() in RESTART_COMMANDS


def is_simple_greeting(text: str) -> bool:
    return bool(text) and text.strip().lower().strip("!¡., ") in SIMPLE_GREETINGS


def valid_whatsapp_sender(value) -> bool:
    """Accept a phone or Meta BSUID without converting one identity into another."""
    return valid_whatsapp_phone(value) or valid_whatsapp_user_id(value)


def whatsapp_recipient_fields(value: str) -> dict:
    """BSUID messages use recipient; sending them in to can silently lose delivery."""
    if valid_whatsapp_user_id(value):
        return {"recipient_type": "individual", "recipient": value}
    if valid_whatsapp_phone(value):
        return {"to": value}
    raise ValueError("Invalid WhatsApp recipient identifier")


def resolve_whatsapp_sender(message: dict, contacts: list, *, allow_single_contact: bool = False) -> tuple[str | None, str]:
    """Resolve only explicit Meta identities; never infer a phone from a wamid/name.

    Prefer an available phone to retain existing customer histories. Contact
    aliases are used only for a matching message, or a single-message/single-
    contact payload; the first contact of an unrelated batch is never assumed.
    """
    message_ids = [
        value for value in (
            message.get("from"), message.get("from_user_id"), message.get("from_parent_user_id")
        ) if valid_whatsapp_sender(value)
    ]
    contacts = contacts if isinstance(contacts, list) else []
    matches = []
    for contact in contacts:
        if not isinstance(contact, dict):
            continue
        ids = [
            value for value in (contact.get("wa_id"), contact.get("user_id"), contact.get("parent_user_id"))
            if valid_whatsapp_sender(value)
        ]
        if set(message_ids).intersection(ids):
            matches.append(contact)
        elif allow_single_contact and len(contacts) == 1 and (not message_ids or not ids):
            matches.append(contact)
    contact = matches[0] if len(matches) == 1 else {}
    contact_phone = contact.get("wa_id")
    if valid_whatsapp_phone(message.get("from")):
        sender = message["from"]
    elif valid_whatsapp_phone(contact_phone):
        sender = contact_phone
    else:
        sender = next((value for value in message_ids if valid_whatsapp_user_id(value)), None)
        if sender is None and contact:
            sender = next((value for value in (contact.get("user_id"), contact.get("parent_user_id"), contact.get("wa_id")) if valid_whatsapp_user_id(value)), None)
    profile = contact.get("profile") or {}
    profile = profile if isinstance(profile, dict) else {}
    name = next((value.strip() for value in (profile.get("name"), profile.get("username")) if isinstance(value, str) and value.strip()), "Cliente")
    return sender, name


def whatsapp_destination_matches(value: dict, expected_phone_number_id: str) -> bool:
    """Keep a deployment from consuming another Meta number's webhook events.

    Meta identifies the receiving number in every message-bearing change.  Checking
    it is independent of ``BUSINESS_ID`` and protects against a webhook callback
    accidentally being configured on more than one brand.
    """
    actual = (value.get("metadata") or {}).get("phone_number_id")
    return bool(actual and expected_phone_number_id and str(actual) == str(expected_phone_number_id))


def chatwoot_event_identity(data: dict) -> str:
    """Build an idempotency key without confusing a conversation id for an event id."""
    event = str(data.get("event") or "")
    conversation = data.get("conversation") or {}

    if event == "message_created":
        message_id = data.get("id") or data.get("message_id")
        return f"{event}:{message_id}" if message_id is not None else ""

    conversation_id = conversation.get("id") or data.get("id")
    timestamp = (
        data.get("updated_at")
        or conversation.get("updated_at")
        or conversation.get("last_activity_at")
        or data.get("created_at")
    )
    status = data.get("status") or conversation.get("status")
    # With no event timestamp there is no safe deduplication key. Processing the event
    # is preferable to permanently dropping a resolve/reopen transition.
    if not event or conversation_id is None or timestamp is None:
        return ""
    return f"{event}:{conversation_id}:{status or ''}:{timestamp}"
