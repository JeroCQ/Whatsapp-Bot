"""Identify legacy Gemini outage turns that still need a Chatwoot handoff."""

from collections import defaultdict
from dataclasses import dataclass


OUTAGE_FALLBACK = (
    "Disculpa, en este momento estoy teniendo un retraso en procesar tu mensaje. "
    "¿Podrías escribir nuevamente?"
)


@dataclass(frozen=True)
class Recovery:
    phone: str
    questions: tuple[str, ...]


def find_recoveries(rows: list[dict]) -> list[Recovery]:
    """Find unanswered outage turns in chronologically ordered message logs."""
    by_phone = defaultdict(list)
    for row in rows:
        by_phone[str(row.get("phone_number") or "")].append(row)

    recoveries = []
    for phone, messages in by_phone.items():
        if not phone:
            continue
        failed_indexes = [
            index
            for index, message in enumerate(messages)
            if message.get("role") == "model" and message.get("content") == OUTAGE_FALLBACK
        ]
        if not failed_indexes:
            continue

        # A later real bot/advisor response means the conversation was already
        # picked up; do not send a surprise duplicate answer.
        last_failure = failed_indexes[-1]
        if any(
            message.get("role") in {"model", "asesor"}
            and message.get("content") != OUTAGE_FALLBACK
            for message in messages[last_failure + 1 :]
        ):
            continue

        questions = []
        for index in failed_indexes:
            for previous in reversed(messages[:index]):
                if previous.get("role") == "user":
                    question = str(previous.get("content") or "").strip()
                    if question and question not in questions:
                        questions.append(question)
                    break
        if questions:
            recoveries.append(Recovery(phone, tuple(questions)))
    return recoveries
