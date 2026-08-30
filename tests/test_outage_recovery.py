from outage_recovery import OUTAGE_FALLBACK, Recovery, find_recoveries


def row(phone, role, content):
    return {"phone_number": phone, "role": role, "content": content}


def test_collects_multiple_failed_questions_into_one_recovery():
    rows = [
        row("57300", "user", "¿Qué precio tiene?"),
        row("57300", "model", OUTAGE_FALLBACK),
        row("57300", "user", "¿Y hacen envíos?"),
        row("57300", "model", OUTAGE_FALLBACK),
    ]

    assert find_recoveries(rows) == [
        Recovery("57300", ("¿Qué precio tiene?", "¿Y hacen envíos?"))
    ]


def test_skips_conversation_that_already_received_a_real_answer():
    rows = [
        row("57300", "user", "¿Qué precio tiene?"),
        row("57300", "model", OUTAGE_FALLBACK),
        row("57300", "model", "Cuesta $20.000"),
    ]

    assert find_recoveries(rows) == []


def test_skips_conversation_picked_up_by_advisor():
    rows = [
        row("57300", "user", "¿Qué precio tiene?"),
        row("57300", "model", OUTAGE_FALLBACK),
        row("57300", "asesor", "Hola, te ayudo con eso"),
    ]

    assert find_recoveries(rows) == []
