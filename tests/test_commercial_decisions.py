import pytest

from commercial_intents import (follow_up_plan, follow_up_suppression_reason, b2b_reason,
    catalog_may_suppress_handoff, unregistered_presentation_reason, follow_up_preference_patch)


def test_absent_and_intentionally_empty_followup_are_different():
    assert follow_up_plan("Te comparto opciones", ["catalogo_memos"]) is not None
    for empty in ("", " \n "):
        assert follow_up_plan("Te comparto opciones", ["catalogo_memos"], empty) is None


@pytest.mark.parametrize("text", ["No gracias", "No me interesa", "Yo te aviso cuando salga", "Yo les escribo después", "Hasta luego"])
def test_customer_decision_not_to_contact_wins(text):
    assert follow_up_plan("¿Retomamos?", [], "Te recuerdo tu pedido", customer_message=text) is None


def test_thanks_in_pending_order_is_not_automatically_a_closure():
    assert follow_up_plan("Solo falta tu barrio", [], customer_message="gracias") is None
    state = {"current_state": "PAYMENT_PENDING", "order_summary": "2 quesos, pendiente pago"}
    assert follow_up_plan("Solo falta tu pago", [], customer_message="gracias", state=state) is not None
    assert follow_up_plan("Solo falta tu pago", [], "", customer_message="gracias", state=state) is None
    assert follow_up_suppression_reason("hola", state={"is_paused": True}) == "human_control"


def test_confirmed_wholesale_phrase_cannot_be_overridden_by_catalog():
    text = "Eso es precios al por mayor"
    assert b2b_reason(text)
    assert not catalog_may_suppress_handoff(text)
    assert not catalog_may_suppress_handoff("quiero catálogo y un asesor")
    assert catalog_may_suppress_handoff("Mándame el catálogo")
    assert not catalog_may_suppress_handoff("Mándame el catálogo", model_reason="Validar una condición de entrega previamente acordada")


def test_contact_preferences_survive_acknowledgements_and_need_reengagement():
    held = follow_up_preference_patch("Yo te aviso cuando salga", {})
    assert held["follow_up_suppressed"]
    assert follow_up_preference_patch("gracias", held) == {}
    assert follow_up_plan("Solo falta tu barrio", [], "¿Tu barrio?", customer_message="gracias", state=held) is None
    resumed = follow_up_preference_patch("quiero comprar ahora", held)
    assert resumed["follow_up_suppressed"] is False
    opted_out = follow_up_preference_patch("No me contacten", {})
    assert follow_up_preference_patch("quiero un queso", opted_out) == {}
    assert follow_up_preference_patch("pueden escribirme", opted_out)["follow_up_suppressed"] is False


def test_unknown_block_is_not_unavailable_and_quantity_alone_is_not_handoff():
    approved = "* Mozzarella: $6.200 por 250 g.\nPedir 5 libras no activa por sí solo el escalamiento."
    assert unregistered_presentation_reason("Tienen bloques de cinco libras?", approved)
    assert unregistered_presentation_reason("Quiero 5 libras", approved) is None
    assert unregistered_presentation_reason("Bloque de 5 libras", "Bloque de 5 libras: $40.000") is None
