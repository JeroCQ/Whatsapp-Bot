from scripts.analyze_message_logs import post_resolution_segments, resolution_outcome


def row(role, content):
    return {"role": role, "content": content}


def test_post_resolution_analysis_does_not_treat_resolved_as_a_sale():
    conversations = {
        "phone": [
            row("system", "RESOLVED: Conversación cerrada por el asesor."),
            row("user", "Quiero hacer un pedido"),
            row("asesor", "Gracias por tu compra, paso a despachos"),
        ]
    }

    segments = post_resolution_segments(conversations)

    assert len(segments) == 1
    assert resolution_outcome(segments[0]) == "sold"


def test_resolution_without_commercial_evidence_stays_unknown():
    assert resolution_outcome([row("asesor", "¿Cómo te puedo ayudar?")]) == "unknown"
