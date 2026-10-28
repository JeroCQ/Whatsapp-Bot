"""Customer-facing handoff copy; technical details stay in private advisor notes."""

from collections.abc import Mapping


HUMAN_HANDOFF_MESSAGE = "Te comunico con nuestro equipo para continuar ayudándote."
HANDOFF_PENDING_MESSAGE = "Gracias por tu mensaje. Permíteme un momento para continuar ayudándote."


def handoff_transition_message(result):
    """Only confirm the transition when Chatwoot has a created conversation."""
    if isinstance(result, Mapping) and result.get("status") == "created":
        return HUMAN_HANDOFF_MESSAGE
    return HANDOFF_PENDING_MESSAGE
