"""Pure helpers shared by webhook handlers and their unit tests."""


RESTART_COMMANDS = {"/reset", "/restart"}
SIMPLE_GREETINGS = {"hola", "holi", "hello", "buenas", "buenos dias", "buenos días"}


def is_restart_command(text: str) -> bool:
    return bool(text) and text.strip().lower() in RESTART_COMMANDS


def is_simple_greeting(text: str) -> bool:
    return bool(text) and text.strip().lower().strip("!¡., ") in SIMPLE_GREETINGS


def valid_whatsapp_sender(value) -> bool:
    """Return whether Meta supplied a usable WhatsApp sender identifier.

    Most senders are E.164 phone numbers (at most 15 digits), but Meta can expose
    a longer, numeric, business-scoped WhatsApp user identifier for privacy-aware
    accounts.  The canonical schema deliberately stores up to 25 characters, so
    accepting the numeric identifier does not weaken brand isolation or require a
    migration.  Non-numeric values remain rejected rather than being guessed.
    """
    return isinstance(value, str) and value.isdigit() and 7 <= len(value) <= 25


def whatsapp_sender(message: dict, contacts: list) -> str | None:
    """Resolve the sender from a Meta message, with a safe contact fallback.

    Normal Cloud API notifications put the identifier in ``message.from``.  Some
    otherwise valid notifications omit it while still supplying the same WhatsApp
    identifier as ``contacts[].wa_id``.  Only use the fallback when there is one
    unambiguous, valid contact; never guess between multiple customers.
    """
    direct_sender = message.get("from")
    if valid_whatsapp_sender(direct_sender):
        return direct_sender

    contact_senders = {
        contact.get("wa_id")
        for contact in contacts
        if isinstance(contact, dict) and valid_whatsapp_sender(contact.get("wa_id"))
    }
    if len(contact_senders) == 1:
        return contact_senders.pop()
    return None


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
