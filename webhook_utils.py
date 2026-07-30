"""Pure helpers shared by webhook handlers and their unit tests."""


RESTART_COMMANDS = {"/reset", "/restart"}
SIMPLE_GREETINGS = {"hola", "holi", "hello", "buenas", "buenos dias", "buenos días"}


def is_restart_command(text: str) -> bool:
    return bool(text) and text.strip().lower() in RESTART_COMMANDS


def is_simple_greeting(text: str) -> bool:
    return bool(text) and text.strip().lower().strip("!¡., ") in SIMPLE_GREETINGS


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
