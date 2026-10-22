import pytest

from conversation_summary import build_handoff_summary, compact_order_summary, supplied_customer_data, format_handoff_history


def test_extracts_supplied_data_without_requiring_one_form_message():
    messages = [
        {"role": "user", "content": "Ciudad: Cali\nDirección: Calle 1 # 2-3\nBarrio: Centro"},
        {"role": "model", "content": "¿Cuál es tu método de pago?"},
        {"role": "user", "content": "Pago: Nequi y email cliente@example.com"},
    ]

    assert supplied_customer_data(messages) == {
        "Ciudad": "Cali",
        "Email": "cliente@example.com",
        "Dirección": "Calle 1 # 2-3",
        "Barrio": "Centro",
        "Pago": "Nequi",
    }


def test_order_summary_is_short_and_uses_checkout_values():
    messages = [{
        "role": "model",
        "content": "Resumen de tu pedido:\n• 1 Brownie: $18.500\n• 1 Galleta: $18.500\n• Domicilio: $9.000\nTotal: $46.000\nDatos registrados:",
    }]

    summary = compact_order_summary(messages)

    assert summary == "1 Brownie: $18.500; 1 Galleta: $18.500; Total: $46.000"
    assert len(summary) < 100


def test_handoff_summary_includes_order_and_preserved_data():
    messages = [
        {"role": "user", "content": "Documento: 123456 y Pago: efectivo"},
        {"role": "user", "content": "Quiero un paquete de arepas"},
    ]

    summary = build_handoff_summary(messages)

    assert "**Pedido en curso:** Quiero un paquete de arepas" in summary
    assert "Documento: 123456" in summary
    assert "Pago: efectivo" in summary
    assert "**Incidente operativo:** ninguno registrado" in summary


def test_handoff_summary_surfaces_latest_technical_incident():
    messages = [
        {"role": "system", "content": "ERROR enviando archivos: catalogo_pdf. Detalle técnico: timeout"},
        {"role": "system", "content": "ERROR enviando archivos: catalogo_nuevo. Detalle técnico: Meta 500"},
    ]

    summary = build_handoff_summary(messages)

    assert "**Incidente operativo:** ERROR enviando archivos: catalogo_nuevo" in summary
    assert "Detalle técnico: Meta 500" in summary


def test_preserves_unlabelled_document_phone_and_address_form_answer():
    messages = [{
        "role": "user",
        "content": "1130657902 3136650796 Calle 91 #26i2-41, torre 8, apto 404",
    }]

    assert supplied_customer_data(messages) == {
        "Dirección": "Calle 91 #26i2-41, torre 8, apto 404",
        "Documento": "1130657902",
        "Teléfono": "3136650796",
    }


def test_audio_handoff_identifies_the_transcript_that_motivated_the_handoff_without_truncating_it():
    transcript = (
        "Quiero una nota escrita a mano para mañana. "
        + "Esta es una instrucción importante para el mensaje personalizado. " * 12
        + "Termina exactamente con: nos vemos el viernes, mamá."
    )
    summary = build_handoff_summary([], trigger_is_audio=True, trigger_transcript=transcript)
    assert "**Origen de la derivación:** nota de voz del cliente" in summary
    assert "**Transcripción automática que motivó la derivación:**" in summary
    assert transcript in summary
    assert "Termina exactamente con: nos vemos el viernes, mamá." in summary
    assert "audio adjuntado" not in summary.lower()


@pytest.mark.parametrize("transcript", [None, "", " \n "])
def test_missing_audio_transcript_does_not_invent_content_or_claim_it_triggered_the_decision(transcript):
    summary = build_handoff_summary([], trigger_is_audio=True, trigger_transcript=transcript)
    assert "**Mensaje recibido:** nota de voz del cliente" in summary
    assert "no fue posible transcribir esta nota de voz" in summary
    assert "motivó" not in summary
    assert "**Origen de la derivación:**" not in summary


def test_plaintext_handoff_retains_the_existing_summary_and_does_not_guess_an_audio_origin():
    messages = [{"role": "user", "content": "Quiero una nota personalizada"}]
    assert build_handoff_summary(messages) == (
        "**Resumen de handoff**\n"
        "**Pedido en curso:** Quiero una nota personalizada\n"
        "**Datos ya suministrados:** ninguno identificado\n"
        "**Incidente operativo:** ninguno registrado"
    )


def test_handoff_history_labels_audio_transcripts_but_preserves_typed_messages_and_roles():
    messages = [
        {"role": "user", "content": "Mándame el catálogo"},
        {"role": "model", "content": "Te lo comparto"},
        {"role": "user", "content": "La tarjeta debe decir exactamente: Gracias, mamá.",
         "metadata": {"input_type": "audio", "is_transcription": True}},
        {"role": "asesor", "content": "Ya lo reviso"},
        {"role": "system", "content": "HANDOFF: revisión pendiente"},
    ]
    assert format_handoff_history(messages) == (
        "👤: Mándame el catálogo\n"
        "🤖: Te lo comparto\n"
        "👤: 🎙️ [Transcripción automática de nota de voz] La tarjeta debe decir exactamente: Gracias, mamá.\n"
        "🧑‍💼: Ya lo reviso\n"
        "⚠️: HANDOFF: revisión pendiente"
    )
    assert messages[2]["content"] == "La tarjeta debe decir exactamente: Gracias, mamá."


def test_raw_audio_marker_is_not_mislabeled_as_a_successful_transcription():
    history = format_handoff_history([
        {"role": "user", "content": "[Archivo/audio] [Audio Nota]", "metadata": {"input_type": "audio"}},
        {"role": "model", "content": "Respuesta", "metadata": {"input_type": "audio", "is_transcription": True}},
    ])
    assert history == "👤: 🎙️ [Nota de voz] [Archivo/audio] [Audio Nota]\n🤖: Respuesta"
    assert "Transcripción automática" not in history


def test_recovered_handoff_uses_the_explicit_durable_audio_source():
    transcript = "Quiero esta nota escrita a mano para la entrega de mañana."
    messages = [
        {"role": "system", "content": "HANDOFF_SOURCE: audio anterior",
         "metadata": {"handoff_trigger": "audio", "trigger_transcript": "Pedido anterior ya atendido"}},
        {"role": "system", "content": "RESOLVED: Chatwoot marcó la conversación como resuelta."},
        {"role": "user", "content": transcript, "metadata": {"input_type": "audio", "is_transcription": True}},
        {"role": "system", "content": "HANDOFF_SOURCE: nota de voz interpretada mediante transcripción automática.",
         "metadata": {"handoff_trigger": "audio", "trigger_transcript": transcript}},
        {"role": "system", "content": "ERROR creando caso: recuperación pendiente"},
        {"role": "user", "content": "Gracias", "metadata": {"input_type": "audio", "is_transcription": True}},
    ]
    # retry_pending_handoff has the stored logs, without original media kwargs.
    summary = build_handoff_summary(messages)
    assert "**Origen de la derivación:** nota de voz del cliente" in summary
    assert "**Transcripción automática que motivó la derivación:**\n" + transcript in summary
    assert "**Transcripción automática que motivó la derivación:**\nGracias" not in summary
    assert "Pedido anterior ya atendido" not in summary


def test_resolved_boundary_does_not_restore_an_old_audio_source_or_guess_from_later_audio():
    messages = [
        {"role": "system", "content": "HANDOFF_SOURCE: nota de voz interpretada mediante transcripción automática.",
         "metadata": {"handoff_trigger": "audio", "trigger_transcript": "Una solicitud de ayer"}},
        {"role": "system", "content": "RESOLVED: Chatwoot marcó la conversación como resuelta."},
        {"role": "user", "content": "Gracias", "metadata": {"input_type": "audio", "is_transcription": True}},
        {"role": "user", "content": "Quiero validar una condición nueva por escrito"},
    ]
    summary = build_handoff_summary(messages)
    assert "**Origen de la derivación:**" not in summary
    assert "**Transcripción automática que motivó la derivación:**" not in summary
    assert "Una solicitud de ayer" not in summary
