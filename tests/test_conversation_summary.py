from conversation_summary import build_handoff_summary, compact_order_summary, supplied_customer_data


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
